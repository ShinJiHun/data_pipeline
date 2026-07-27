"""
LifeMetrics - LLM 재분석 배치
인바디 또는 FitDays 전체 레코드를 LLM으로 재분석해서 raw_llm_json 업데이트

사용법:
    python reanalyze_batch.py --type inbody    # user_body_record 재분석
    python reanalyze_batch.py --type fitdays   # user_fitdays_record 재분석
    python reanalyze_batch.py --type inbody --id 5   # 특정 레코드만
"""

import os
import json
import time
import argparse
import pymysql
import anthropic
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.getenv("DB_PASSWORD", "ride1234!"),
    "database": "riding_db",
    "charset":  "utf8mb4",
}

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


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
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system="체성분 및 사이클 퍼포먼스 전문 분석가. 순수 JSON만 응답.",
            messages=[{"role": "user", "content": prompt}],
        )
        t = response.content[0].text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            if t.startswith("json"):
                t = t[4:]
        return json.loads(t.strip())
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
        elif args.rype == "health_exam":
            run_health_exam(conn, target_id=args.id)
    finally:
        conn.close()

    print(f"\n{'='*50}\n🎉 완료!\n{'='*50}")


if __name__ == "__main__":
    main()


# ────────────────────────────────────────────
# health_exam 재분석
# ────────────────────────────────────────────
def run_health_exam(conn, target_id=None):
    print("\n[건강검진 재분석 시작]")
    with conn.cursor() as c:
        if target_id:
            c.execute("""
                SELECT id, exam_date, exam_place,
                       height_cm, weight_kg, bmi, waist_cm,
                       systolic_bp, diastolic_bp,
                       hemoglobin, fasting_glucose,
                       total_cholesterol, hdl_cholesterol, ldl_cholesterol, triglyceride,
                       creatinine, egfr, ast, alt, ggt,
                       urine_protein, overall_result, note
                FROM user_health_exam
                WHERE user_id = 1 AND id = %s
                ORDER BY exam_date ASC
            """, (target_id,))
        else:
            c.execute("""
                SELECT id, exam_date, exam_place,
                       height_cm, weight_kg, bmi, waist_cm,
                       systolic_bp, diastolic_bp,
                       hemoglobin, fasting_glucose,
                       total_cholesterol, hdl_cholesterol, ldl_cholesterol, triglyceride,
                       creatinine, egfr, ast, alt, ggt,
                       urine_protein, overall_result, note
                FROM user_health_exam
                WHERE user_id = 1
                ORDER BY exam_date ASC
            """)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]

    print(f"총 {len(rows)}개 처리")

    for i, rec in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        all_records = rows[:i + 1]  # 현재까지 누적 기록
        print(f"\n[{i+1:02d}/{len(rows)}] {rec['exam_date']}", end=" ... ")

        result = call_llm_health_exam(rec, prev, all_records)
        if not result:
            continue

        result["analyzed_at"] = datetime.now().isoformat()
        with conn.cursor() as c:
            c.execute(
                "UPDATE user_health_exam SET raw_llm_json = %s WHERE id = %s",
                (json.dumps(result, ensure_ascii=False), rec["id"])
            )
        conn.commit()
        print(f"✅ {result.get('summary', '')}")

        if i < len(rows) - 1:
            time.sleep(1.5)


def call_llm_health_exam(current: dict, prev: dict | None, all_records: list) -> dict | None:
    try:
        prompt = build_health_exam_prompt(current, prev, all_records)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system="건강검진 데이터 분석 전문가. 시계열 건강 변화를 분석하고 순수 JSON만 응답.",
            messages=[{"role": "user", "content": prompt}],
        )
        t = response.content[0].text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            if t.startswith("json"):
                t = t[4:]
        return json.loads(t.strip())
    except Exception as e:
        print(f"  ❌ LLM 실패: {e}")
        return None


def build_health_exam_prompt(current: dict, prev: dict | None, all_records: list) -> str:
    def fmt(v, unit=""):
        return f"{v}{unit}" if v is not None else "미측정"

    lines = [
        f"## 건강검진 결과 분석",
        f"### 현재 검진일: {current.get('exam_date')} ({current.get('exam_place')})",
        "",
        "### 현재 측정값",
        f"- 키/몸무게: {fmt(current.get('height_cm'), 'cm')} / {fmt(current.get('weight_kg'), 'kg')}",
        f"- BMI: {fmt(current.get('bmi'))} / 허리둘레: {fmt(current.get('waist_cm'), 'cm')}",
        f"- 혈압: {fmt(current.get('systolic_bp'))}/{fmt(current.get('diastolic_bp'))} mmHg",
        f"- 혈색소: {fmt(current.get('hemoglobin'), 'g/dL')}",
        f"- 공복혈당: {fmt(current.get('fasting_glucose'), 'mg/dL')}",
        f"- 총콜레스테롤: {fmt(current.get('total_cholesterol'), 'mg/dL')}",
        f"- HDL: {fmt(current.get('hdl_cholesterol'), 'mg/dL')} / LDL: {fmt(current.get('ldl_cholesterol'), 'mg/dL')}",
        f"- 중성지방: {fmt(current.get('triglyceride'), 'mg/dL')}",
        f"- 크레아티닌: {fmt(current.get('creatinine'), 'mg/dL')} / eGFR: {fmt(current.get('egfr'))}",
        f"- AST: {fmt(current.get('ast'), 'IU/L')} / ALT: {fmt(current.get('alt'), 'IU/L')} / γ-GTP: {fmt(current.get('ggt'), 'IU/L')}",
        f"- 요단백: {fmt(current.get('urine_protein'))}",
        f"- 종합판정: {fmt(current.get('overall_result'))}",
    ]

    if prev:
        def diff(key, unit=""):
            a, b = current.get(key), prev.get(key)
            if a is None or b is None:
                return "비교불가"
            try:
                d = round(float(a) - float(b), 2)
                return f"{'+' if d >= 0 else ''}{d}{unit}"
            except:
                return "비교불가"

        lines += [
            "",
            f"### 이전 검진 대비 변화 ({prev.get('exam_date')})",
            f"- 체중: {diff('weight_kg', 'kg')}",
            f"- BMI: {diff('bmi')}",
            f"- 혈압(수축기): {diff('systolic_bp', 'mmHg')}",
            f"- 공복혈당: {diff('fasting_glucose', 'mg/dL')}",
            f"- AST: {diff('ast', 'IU/L')} / ALT: {diff('alt', 'IU/L')}",
            f"- γ-GTP: {diff('ggt', 'IU/L')}",
        ]

    if len(all_records) > 1:
        lines += [
            "",
            f"### 전체 검진 이력 ({len(all_records)}회)",
        ]
        for r in all_records:
            lines.append(
                f"- {r['exam_date']}: 체중{fmt(r.get('weight_kg'), 'kg')} "
                f"혈압{fmt(r.get('systolic_bp'))}/{fmt(r.get('diastolic_bp'))} "
                f"혈당{fmt(r.get('fasting_glucose'), 'mg/dL')} "
                f"판정:{fmt(r.get('overall_result'))}"
            )

    lines += [
        "",
        "---",
        "위 데이터를 분석하여 아래 JSON 형식으로만 응답. 순수 JSON만 출력.",
        """
{
  "summary": "이번 검진 한줄 요약 (50자 이내)",
  "trend": {
    "weight": "체중 변화 추세",
    "blood_pressure": "혈압 변화 추세",
    "glucose": "혈당 변화 추세",
    "liver": "간수치 변화 추세",
    "kidney": "신장 기능 추세",
    "overall": "전반적 건강 변화 추세"
  },
  "risk_factors": ["현재 주의가 필요한 항목들"],
  "improvements": ["이전 대비 개선된 항목들 (첫 검진이면 null)"],
  "recommendations": ["권고사항1", "권고사항2", "권고사항3"],
  "health_score": "0~100 사이 종합 건강점수 (숫자만)"
}"""
    ]
    return "\n".join(lines)


# ────────────────────────────────────────────
# health_exam 재분석
# ────────────────────────────────────────────
def run_health_exam(conn, target_id=None):
    print("\n[건강검진 재분석 시작]")
    with conn.cursor() as c:
        if target_id:
            c.execute("""
                SELECT id, exam_date, exam_place,
                       height_cm, weight_kg, bmi, waist_cm,
                       systolic_bp, diastolic_bp,
                       hemoglobin, fasting_glucose,
                       total_cholesterol, hdl_cholesterol, ldl_cholesterol, triglyceride,
                       creatinine, egfr, ast, alt, ggt,
                       urine_protein, overall_result, note
                FROM user_health_exam
                WHERE user_id = 1 AND id = %s
                ORDER BY exam_date ASC
            """, (target_id,))
        else:
            c.execute("""
                SELECT id, exam_date, exam_place,
                       height_cm, weight_kg, bmi, waist_cm,
                       systolic_bp, diastolic_bp,
                       hemoglobin, fasting_glucose,
                       total_cholesterol, hdl_cholesterol, ldl_cholesterol, triglyceride,
                       creatinine, egfr, ast, alt, ggt,
                       urine_protein, overall_result, note
                FROM user_health_exam
                WHERE user_id = 1
                ORDER BY exam_date ASC
            """)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]

    print(f"총 {len(rows)}개 처리")

    for i, rec in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        all_records = rows[:i + 1]  # 현재까지 누적 기록
        print(f"\n[{i+1:02d}/{len(rows)}] {rec['exam_date']}", end=" ... ")

        result = call_llm_health_exam(rec, prev, all_records)
        if not result:
            continue

        result["analyzed_at"] = datetime.now().isoformat()
        with conn.cursor() as c:
            c.execute(
                "UPDATE user_health_exam SET raw_llm_json = %s WHERE id = %s",
                (json.dumps(result, ensure_ascii=False), rec["id"])
            )
        conn.commit()
        print(f"✅ {result.get('summary', '')}")

        if i < len(rows) - 1:
            time.sleep(1.5)


def call_llm_health_exam(current: dict, prev: dict | None, all_records: list) -> dict | None:
    try:
        prompt = build_health_exam_prompt(current, prev, all_records)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system="건강검진 데이터 분석 전문가. 시계열 건강 변화를 분석하고 순수 JSON만 응답.",
            messages=[{"role": "user", "content": prompt}],
        )
        t = response.content[0].text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            if t.startswith("json"):
                t = t[4:]
        return json.loads(t.strip())
    except Exception as e:
        print(f"  ❌ LLM 실패: {e}")
        return None


def build_health_exam_prompt(current: dict, prev: dict | None, all_records: list) -> str:
    def fmt(v, unit=""):
        return f"{v}{unit}" if v is not None else "미측정"

    lines = [
        f"## 건강검진 결과 분석",
        f"### 현재 검진일: {current.get('exam_date')} ({current.get('exam_place')})",
        "",
        "### 현재 측정값",
        f"- 키/몸무게: {fmt(current.get('height_cm'), 'cm')} / {fmt(current.get('weight_kg'), 'kg')}",
        f"- BMI: {fmt(current.get('bmi'))} / 허리둘레: {fmt(current.get('waist_cm'), 'cm')}",
        f"- 혈압: {fmt(current.get('systolic_bp'))}/{fmt(current.get('diastolic_bp'))} mmHg",
        f"- 혈색소: {fmt(current.get('hemoglobin'), 'g/dL')}",
        f"- 공복혈당: {fmt(current.get('fasting_glucose'), 'mg/dL')}",
        f"- 총콜레스테롤: {fmt(current.get('total_cholesterol'), 'mg/dL')}",
        f"- HDL: {fmt(current.get('hdl_cholesterol'), 'mg/dL')} / LDL: {fmt(current.get('ldl_cholesterol'), 'mg/dL')}",
        f"- 중성지방: {fmt(current.get('triglyceride'), 'mg/dL')}",
        f"- 크레아티닌: {fmt(current.get('creatinine'), 'mg/dL')} / eGFR: {fmt(current.get('egfr'))}",
        f"- AST: {fmt(current.get('ast'), 'IU/L')} / ALT: {fmt(current.get('alt'), 'IU/L')} / γ-GTP: {fmt(current.get('ggt'), 'IU/L')}",
        f"- 요단백: {fmt(current.get('urine_protein'))}",
        f"- 종합판정: {fmt(current.get('overall_result'))}",
    ]

    if prev:
        def diff(key, unit=""):
            a, b = current.get(key), prev.get(key)
            if a is None or b is None:
                return "비교불가"
            try:
                d = round(float(a) - float(b), 2)
                return f"{'+' if d >= 0 else ''}{d}{unit}"
            except:
                return "비교불가"

        lines += [
            "",
            f"### 이전 검진 대비 변화 ({prev.get('exam_date')})",
            f"- 체중: {diff('weight_kg', 'kg')}",
            f"- BMI: {diff('bmi')}",
            f"- 혈압(수축기): {diff('systolic_bp', 'mmHg')}",
            f"- 공복혈당: {diff('fasting_glucose', 'mg/dL')}",
            f"- AST: {diff('ast', 'IU/L')} / ALT: {diff('alt', 'IU/L')}",
            f"- γ-GTP: {diff('ggt', 'IU/L')}",
        ]

    if len(all_records) > 1:
        lines += [
            "",
            f"### 전체 검진 이력 ({len(all_records)}회)",
        ]
        for r in all_records:
            lines.append(
                f"- {r['exam_date']}: 체중{fmt(r.get('weight_kg'), 'kg')} "
                f"혈압{fmt(r.get('systolic_bp'))}/{fmt(r.get('diastolic_bp'))} "
                f"혈당{fmt(r.get('fasting_glucose'), 'mg/dL')} "
                f"판정:{fmt(r.get('overall_result'))}"
            )

    lines += [
        "",
        "---",
        "위 데이터를 분석하여 아래 JSON 형식으로만 응답. 순수 JSON만 출력.",
        """
{
  "summary": "이번 검진 한줄 요약 (50자 이내)",
  "trend": {
    "weight": "체중 변화 추세",
    "blood_pressure": "혈압 변화 추세",
    "glucose": "혈당 변화 추세",
    "liver": "간수치 변화 추세",
    "kidney": "신장 기능 추세",
    "overall": "전반적 건강 변화 추세"
  },
  "risk_factors": ["현재 주의가 필요한 항목들"],
  "improvements": ["이전 대비 개선된 항목들 (첫 검진이면 null)"],
  "recommendations": ["권고사항1", "권고사항2", "권고사항3"],
  "health_score": "0~100 사이 종합 건강점수 (숫자만)"
}"""
    ]
    return "\n".join(lines)
