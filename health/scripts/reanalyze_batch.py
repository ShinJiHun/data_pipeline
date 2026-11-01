"""
LifeMetrics - LLM 재분석 배치
인바디 또는 FitDays 전체 레코드를 LLM으로 재분석해서 raw_llm_json 업데이트

사용법:
    python reanalyze_batch.py --type inbody    # user_body_record 재분석
    python reanalyze_batch.py --type fitdays   # user_fitdays_record 재분석
    python reanalyze_batch.py --type health_exam   # health_checkup 재분석 (llm_analysis)
    python reanalyze_batch.py --type inbody --id 5   # 특정 레코드만
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
import os
import sys
import json
import time
import argparse
import pymysql
from datetime import datetime

sys.path.insert(0, "/mnt/200gb/project")
from data_pipeline.claude_config import client, CLAUDE_MODEL, extract_text

sys.path.insert(0, "/mnt/200gb/project/data_pipeline/health")
from services.inbody_pipeline_service import ANALYSIS_SCHEMA as BODY_SCHEMA
from services.health_exam_pipeline_service import (
    ANALYSIS_SCHEMA as HEALTH_EXAM_SCHEMA,
    build_llm_prompt as build_health_exam_prompt,
)


def call_json(system: str, prompt: str, schema: dict, max_tokens: int) -> dict:
    """structured outputs로 스키마에 맞는 JSON을 강제해서 호출
    (max_tokens는 thinking 토큰까지 포함, output_config는 SDK 0.75에 인자가 없어서 extra_body로 전달)"""
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}},
    )
    if response.stop_reason == "max_tokens":
        raise ValueError("응답이 max_tokens에서 잘림")
    return json.loads(extract_text(response))

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset":  "utf8mb4",
}


def get_profile(conn):
    with conn.cursor() as c:
        c.execute("SELECT * FROM user_profile WHERE user_id = 1")
        cols = [d[0] for d in c.description]
        row = c.fetchone()
        return dict(zip(cols, row)) if row else {}


def build_prompt(current: dict, prev: dict | None, profile: dict) -> str:
    def fmt(v, unit=""):
        return f"{v}{unit}" if v is not None else "미측정"

    g_ftp = profile.get("goal_ftp")
    current_wkg = None
    if g_ftp and current.get("weight"):
        try:
            current_wkg = round(g_ftp / float(current["weight"]), 2)
        except:
            pass

    lines = [
        "## 사용자 정보",
        f"- 성별: {profile.get('gender', '미입력')}",
        f"- 나이: {profile.get('age', '미입력')}세",
        f"- 키: {fmt(profile.get('height_cm'), 'cm')}",
        "",
        "## 목표",
        f"- 목표 체중: {fmt(profile.get('goal_weight'), 'kg')}",
        f"- 목표 체지방률: {fmt(profile.get('goal_body_fat_percentage'), '%')}",
        f"- 목표 골격근량: {fmt(profile.get('goal_skeletal_muscle_mass'), 'kg')}",
        f"- 목표 FTP: {fmt(g_ftp, 'W')}",
        f"- 목표 평균 속도: {fmt(profile.get('goal_avg_speed'), 'km/h')}",
        f"- 목표 W/kg: {fmt(profile.get('goal_wkg'))}",
        f"- 메모: {profile.get('memo', '')}",
        "",
        f"## 측정일: {current.get('record_date')}",
        "",
        "### 현재 측정값",
        f"- 체중: {fmt(current.get('weight'), 'kg')}",
        f"- 골격근량: {fmt(current.get('skeletal_muscle_mass'), 'kg')}",
        f"- 체지방량: {fmt(current.get('body_fat_mass'), 'kg')}",
        f"- 체지방률: {fmt(current.get('body_fat_percentage'), '%')}",
        f"- BMI: {fmt(current.get('bmi'))}",
        f"- 내장지방: {fmt(current.get('visceral_fat_level') or current.get('visceral_fat_index'))}",
        f"- 체수분: {fmt(current.get('total_body_water'), 'L')}",
    ]

    if current_wkg:
        lines.append(f"- 현재 W/kg (목표FTP 기준): {current_wkg}")

    if prev:
        def diff(key, unit=""):
            a, b = current.get(key), prev.get(key)
            if a is None or b is None:
                return "비교불가"
            d = round(float(a) - float(b), 2)
            return f"{'+' if d >= 0 else ''}{d}{unit}"

        lines += [
            "",
            "### 이전 측정 대비 변화",
            f"- 이전 측정일: {prev.get('record_date')}",
            f"- 체중: {diff('weight', 'kg')}",
            f"- 골격근량: {diff('skeletal_muscle_mass', 'kg')}",
            f"- 체지방률: {diff('body_fat_percentage', '%')}",
        ]

    lines += [
        "",
        "---",
        "순수 JSON만 반환:",
        '{"summary":"한줄요약(50자이내)","current_evaluation":{"muscle":"근육평가","fat":"지방평가","overall":"종합평가"},"trend":"추이(첫측정이면null)","goal_status":{"body":"신체목표현황","cycling":"사이클목표체성분분석"},"recommendations":["권고1","권고2","권고3"]}',
    ]
    return "\n".join(lines)


def call_llm(current: dict, prev: dict | None, profile: dict) -> dict | None:
    try:
        prompt = build_prompt(current, prev, profile)
        return call_json("체성분 및 사이클 퍼포먼스 전문 분석가.", prompt, BODY_SCHEMA, 4000)
    except Exception as e:
        print(f"  ❌ LLM 실패: {e}")
        return None


def run_inbody(conn, target_id=None):
    print("\n[인바디 재분석 시작]")
    with conn.cursor() as c:
        if target_id:
            c.execute("""
                SELECT id, record_date, weight, skeletal_muscle_mass,
                       body_fat_mass, body_fat_percentage, bmi,
                       visceral_fat_level, total_body_water
                FROM user_body_record
                WHERE measurement_type = 'INBODY' AND id = %s AND user_id = 1
            """, (target_id,))
        else:
            c.execute("""
                SELECT id, record_date, weight, skeletal_muscle_mass,
                       body_fat_mass, body_fat_percentage, bmi,
                       visceral_fat_level, total_body_water
                FROM user_body_record
                WHERE measurement_type = 'INBODY' AND user_id = 1
                ORDER BY record_date ASC
            """)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]

    print(f"총 {len(rows)}개 처리")
    profile = get_profile(conn)

    for i, rec in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        print(f"\n[{i+1:02d}/{len(rows)}] {rec['record_date']}", end=" ... ")

        result = call_llm(rec, prev, profile)
        if not result:
            continue

        result["analyzed_at"] = datetime.now().isoformat()
        with conn.cursor() as c:
            c.execute(
                "UPDATE user_body_record SET raw_llm_json = %s WHERE id = %s",
                (json.dumps(result, ensure_ascii=False), rec["id"])
            )
        conn.commit()
        print(f"✅ {result.get('summary', '')}")

        if i < len(rows) - 1:
            time.sleep(1.5)


def run_fitdays(conn, target_id=None):
    print("\n[FitDays 재분석 시작]")
    with conn.cursor() as c:
        if target_id:
            c.execute("""
                SELECT id, record_date, weight, skeletal_muscle_mass,
                       body_fat_mass, body_fat_percentage, bmi,
                       visceral_fat_index, total_body_water
                FROM user_fitdays_record
                WHERE user_id = 1 AND id = %s
            """, (target_id,))
        else:
            c.execute("""
                SELECT id, record_date, weight, skeletal_muscle_mass,
                       body_fat_mass, body_fat_percentage, bmi,
                       visceral_fat_index, total_body_water
                FROM user_fitdays_record
                WHERE user_id = 1
                ORDER BY record_date ASC
            """)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]

    print(f"총 {len(rows)}개 처리")
    profile = get_profile(conn)

    for i, rec in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        print(f"\n[{i+1:02d}/{len(rows)}] {rec['record_date']}", end=" ... ")

        result = call_llm(rec, prev, profile)
        if not result:
            continue

        result["analyzed_at"] = datetime.now().isoformat()
        with conn.cursor() as c:
            c.execute(
                "UPDATE user_fitdays_record SET raw_llm_json = %s WHERE id = %s",
                (json.dumps(result, ensure_ascii=False), rec["id"])
            )
        conn.commit()
        print(f"✅ {result.get('summary', '')}")

        if i < len(rows) - 1:
            time.sleep(1.5)


# ────────────────────────────────────────────
# health_exam 재분석
# ────────────────────────────────────────────

def call_llm_health_exam(current: dict, prev: dict | None, all_records: list) -> dict | None:
    try:
        prompt = build_health_exam_prompt(current, prev, all_records)
        return call_json("건강검진 데이터 분석 전문가. 시계열 건강 변화를 분석.", prompt, HEALTH_EXAM_SCHEMA, 6000)
    except Exception as e:
        print(f"  ❌ LLM 실패: {e}")
        return None


def run_health_exam(conn, target_id=None):
    print("\n[건강검진 재분석 시작]")
    # --id 지정 시에도 이전 검진/이력이 프롬프트에 들어가도록 전체를 읽고 대상만 골라서 처리
    with conn.cursor() as c:
        c.execute("""
            SELECT *
            FROM health_checkup
            WHERE user_id = 1
            ORDER BY checkup_date ASC
        """)
        rows = list(c.fetchall())

    targets = [i for i, r in enumerate(rows) if target_id is None or r["id"] == target_id]
    print(f"총 {len(targets)}개 처리")

    for n, i in enumerate(targets):
        rec = rows[i]
        prev = rows[i - 1] if i > 0 else None
        all_records = rows[:i + 1]
        print(f"\n[{n+1:02d}/{len(targets)}] {rec['checkup_date']}", end=" ... ")

        result = call_llm_health_exam(rec, prev, all_records)
        if not result:
            continue

        result["analyzed_at"] = datetime.now().isoformat()
        with conn.cursor() as c:
            c.execute(
                "UPDATE health_checkup SET llm_analysis = %s, updated_at = NOW() WHERE id = %s",
                (json.dumps(result, ensure_ascii=False), rec["id"])
            )
        conn.commit()
        print(f"✅ {result.get('summary', '')}")

        if n < len(targets) - 1:
            time.sleep(1.5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["inbody", "fitdays", "health_exam"], required=True)
    parser.add_argument("--id", type=int, default=None)
    args = parser.parse_args()

    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        if args.type == "inbody":
            run_inbody(conn, target_id=args.id)
        elif args.type == "fitdays":
            run_fitdays(conn, target_id=args.id)
        elif args.type == "health_exam":
            run_health_exam(conn, target_id=args.id)
    finally:
        conn.close()

    print(f"\n{'='*50}\n🎉 완료!\n{'='*50}")


if __name__ == "__main__":
    main()
