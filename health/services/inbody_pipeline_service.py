import os
import sys
import json
import shutil
import base64
from pathlib import Path
from sqlalchemy import create_engine, text

sys.path.insert(0, "/mnt/200gb/project")
from data_pipeline.claude_config import client, CLAUDE_MODEL, extract_text

DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USERNAME', 'tho881')}:{os.getenv('DB_PASSWORD', 'ride1234!')}"
    f"@{os.getenv('DB_HOST', '172.17.0.1')}:3306/riding_db"
)

MY_HEIGHT = 167.0

# 경로 설정
INBODY_RAW_DIR       = "/mnt/200gb/NAS/inbody/raw"
INBODY_PROCESSED_DIR = "/mnt/200gb/NAS/inbody/processed"
INBODY_FAILED_DIR    = "/mnt/200gb/NAS/inbody/failed"
INBODY_JSON_DIR      = "/mnt/200gb/NAS/inbody/json"

os.makedirs(INBODY_PROCESSED_DIR, exist_ok=True)
os.makedirs(INBODY_FAILED_DIR, exist_ok=True)
os.makedirs(INBODY_JSON_DIR, exist_ok=True)

engine = create_engine(DB_URL, pool_pre_ping=True)


def estimate_measurements(weight, pbf, smm, vfl):
    if not weight or not pbf or not smm:
        return None, None, None
    waist = (weight * 0.45) + (pbf * 0.7) + (vfl * 0.5 if vfl else 0)
    thigh = (weight * 0.3) + (smm * 0.8)
    chest = (MY_HEIGHT * 0.5) + (smm * 0.3)
    return round(waist, 1), round(thigh, 1), round(chest, 1)


def call_claude(image_path: str) -> dict:
    """Claude로 인바디 이미지 OCR"""
    print(f"[Claude] 이미지 분석 시작: {image_path}")

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    ext = Path(image_path).suffix.lower()
    media_type_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    media_type = media_type_map.get(ext, "image/png")

    prompt = """이 인바디 결과지에서 다음 정보를 추출해서 JSON으로 반환해주세요:
- record_date (YYYY-MM-DD 형식)
- weight (체중, kg)
- skeletal_muscle_mass (골격근량, kg)
- body_fat_mass (체지방량, kg)
- body_fat_percentage (체지방률, %)
- bmi
- visceral_fat_level (내장지방레벨. "내장지방레벨" 텍스트 바로 아래에 있는 숫자가 측정값입니다. 게이지 눈금의 10은 무시하세요.)
- total_body_water (체수분, kg)
- protein (단백질, kg)
- mineral (무기질, kg)

숫자만 추출하고, 단위는 제외해주세요.
JSON만 반환하고 다른 텍스트는 포함하지 마세요."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    try:
        raw_text = extract_text(response).strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            raw_text = raw_text.rsplit("```", 1)[0]
        data = json.loads(raw_text)
        result = data[0] if isinstance(data, list) else data
        print(f"[Claude] 추출 성공: {result}")
        return result
    except Exception as e:
        print(f"[Claude] ⚠️ 응답 파싱 실패: {e}")
        raise e


# ──────────────────────────────────────────
# LLM 분석 (신규 추가)
# ──────────────────────────────────────────

def get_user_profile() -> dict:
    """user_profile 테이블에서 목표값 조회"""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM user_profile WHERE user_id = 1")).fetchone()
            if row:
                return dict(row._mapping)
    except Exception as e:
        print(f"[Profile] 조회 실패: {e}")
    return {}


def get_prev_record(record_date: str) -> dict | None:
    """직전 측정 레코드 조회"""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT record_date, weight, skeletal_muscle_mass,
                       body_fat_mass, body_fat_percentage, bmi
                FROM user_body_record
                WHERE measurement_type = 'INBODY' AND record_date < :d
                ORDER BY record_date DESC
                LIMIT 1
            """), {"d": record_date}).fetchone()
            if row:
                return dict(row._mapping)
    except Exception as e:
        print(f"[Prev] 조회 실패: {e}")
    return None


def build_analysis_prompt(current: dict, prev: dict | None, profile: dict) -> str:
    def fmt(v, unit=""):
        return f"{v}{unit}" if v is not None else "미측정"

    g_weight = profile.get("goal_weight")
    g_fat    = profile.get("goal_body_fat_percentage")
    g_muscle = profile.get("goal_skeletal_muscle_mass")
    g_ftp    = profile.get("goal_ftp")
    g_speed  = profile.get("goal_avg_speed")
    g_wkg    = profile.get("goal_wkg")
    g_memo   = profile.get("memo", "")

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
        f"- 목표 체중: {fmt(g_weight, 'kg')}",
        f"- 목표 체지방률: {fmt(g_fat, '%')}",
        f"- 목표 골격근량: {fmt(g_muscle, 'kg')}",
        f"- 목표 FTP: {fmt(g_ftp, 'W')}",
        f"- 목표 평균 속도: {fmt(g_speed, 'km/h')}",
        f"- 목표 W/kg: {fmt(g_wkg)}",
        f"- 메모: {g_memo}",
        "",
        f"## 측정일: {current.get('record_date')}",
        "",
        "### 현재 측정값",
        f"- 체중: {fmt(current.get('weight'), 'kg')}",
        f"- 골격근량: {fmt(current.get('skeletal_muscle_mass'), 'kg')}",
        f"- 체지방량: {fmt(current.get('body_fat_mass'), 'kg')}",
        f"- 체지방률: {fmt(current.get('body_fat_percentage'), '%')}",
        f"- BMI: {fmt(current.get('bmi'))}",
        f"- 내장지방레벨: {fmt(current.get('visceral_fat_level'))}",
        f"- 체수분: {fmt(current.get('total_body_water'), 'L')}",
    ]

    if current_wkg:
        lines.append(f"- 현재 W/kg (목표FTP 기준): {current_wkg}")

    if prev:
        def diff(key, unit=""):
            a = current.get(key)
            b = prev.get(key)
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
            f"- 체지방량: {diff('body_fat_mass', 'kg')}",
            f"- 체지방률: {diff('body_fat_percentage', '%')}",
        ]

    lines += [
        "",
        "---",
        "위 데이터를 분석하여 아래 JSON 형식으로만 응답. 마크다운 없이 순수 JSON만 출력.",
        "",
        """{
  "summary": "한 줄 종합 요약 (50자 이내)",
  "current_evaluation": {
    "muscle": "근육/골격근 평가",
    "fat": "체지방/내장지방 평가",
    "overall": "전반적 체성분 평가"
  },
  "trend": "이전 측정 대비 변화 요약 (첫 측정이면 null)",
  "goal_status": {
    "body": "신체 목표 대비 현황",
    "cycling": "사이클 목표(W/kg, 평속) 달성을 위한 체성분 관점 분석"
  },
  "recommendations": ["권고사항1", "권고사항2", "권고사항3"]
}""",
    ]
    return "\n".join(lines)


# 분석 응답 스키마 (structured outputs로 강제 → JSON 파싱 실패 방지)
_STR = {"type": "string"}
ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "current_evaluation", "trend", "goal_status", "recommendations"],
    "properties": {
        "summary": _STR,
        "current_evaluation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["muscle", "fat", "overall"],
            "properties": {"muscle": _STR, "fat": _STR, "overall": _STR},
        },
        "trend": {"type": ["string", "null"]},
        "goal_status": {
            "type": "object",
            "additionalProperties": False,
            "required": ["body", "cycling"],
            "properties": {"body": _STR, "cycling": _STR},
        },
        "recommendations": {"type": "array", "items": _STR},
    },
}


def call_llm_analysis(current: dict) -> dict | None:
    """체성분 + 사이클 목표 기반 LLM 분석"""
    print(f"[분석] LLM 분석 시작...")
    try:
        profile = get_user_profile()
        prev    = get_prev_record(str(current.get("record_date", "")))
        prompt  = build_analysis_prompt(current, prev, profile)

        # max_tokens는 thinking 토큰까지 포함 → 1000이면 응답이 잘리거나 text 블록이 비어 버림
        # output_config는 SDK 0.75에 인자가 없어서 extra_body로 전달
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=(
                "당신은 체성분 및 사이클 퍼포먼스 전문 분석가입니다. "
                "인바디 데이터와 사이클 목표를 연결하여 실용적인 분석을 제공합니다."
            ),
            messages=[{"role": "user", "content": prompt}],
            extra_body={"output_config": {"format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}}},
        )

        if response.stop_reason == "max_tokens":
            raise ValueError("응답이 max_tokens에서 잘림")
        result = json.loads(extract_text(response))
        print(f"[분석] 완료: {result.get('summary', '')}")
        return result

    except Exception as e:
        print(f"[분석] ⚠️ 실패: {e}")
        return None


# ──────────────────────────────────────────

def save_json_backup(data: dict, filename: str):
    json_path = os.path.join(INBODY_JSON_DIR, Path(filename).stem + ".json")
    backup = dict(data)
    backup["_filename"] = filename
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)
    print(f"[JSON] 백업 저장: {json_path}")


def save_to_db(data: dict, filename: str, llm_analysis: dict | None, user_id: int = 1):
    """DB에 인바디 기록 저장 — raw_llm_json에 분석 결과 저장"""
    def to_float(val):
        try:
            return float(val)
        except:
            return None

    weight = to_float(data.get("weight"))
    pbf    = to_float(data.get("body_fat_percentage"))
    smm    = to_float(data.get("skeletal_muscle_mass"))
    vfl    = to_float(data.get("visceral_fat_level"))

    waist, thigh, chest = estimate_measurements(weight, pbf, smm, vfl)

    # raw_llm_json: 분석 결과 우선, 없으면 OCR 원본
    raw_llm_json = json.dumps(
        llm_analysis if llm_analysis else data,
        ensure_ascii=False
    )

    sql = text("""
        INSERT INTO user_body_record
        (user_id, record_date, weight, skeletal_muscle_mass, body_fat_mass,
         body_fat_percentage, bmi, visceral_fat_level, total_body_water,
         protein, mineral, waist_circumference, thigh_circumference,
         chest_circumference, source, raw_filename, measurement_type, raw_llm_json)
        VALUES
        (:user_id, :record_date, :weight, :smm, :fat, :pbf, :bmi, :vfl,
         :water, :protein, :mineral, :waist, :thigh, :chest,
         'CLAUDE', :fname, 'INBODY', :raw_llm_json)
        ON DUPLICATE KEY UPDATE
          weight=:weight, skeletal_muscle_mass=:smm, body_fat_mass=:fat,
          body_fat_percentage=:pbf, bmi=:bmi, visceral_fat_level=:vfl,
          total_body_water=:water, protein=:protein, mineral=:mineral,
          waist_circumference=:waist, thigh_circumference=:thigh,
          chest_circumference=:chest, raw_filename=:fname,
          raw_llm_json=:raw_llm_json
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "user_id":      user_id,
            "record_date":  data.get("record_date"),
            "weight":       weight,
            "smm":          smm,
            "fat":          to_float(data.get("body_fat_mass")),
            "pbf":          pbf,
            "bmi":          to_float(data.get("bmi")),
            "vfl":          int(vfl) if vfl else None,
            "water":        to_float(data.get("total_body_water")),
            "protein":      to_float(data.get("protein")),
            "mineral":      to_float(data.get("mineral")),
            "waist":        waist,
            "thigh":        thigh,
            "chest":        chest,
            "fname":        filename,
            "raw_llm_json": raw_llm_json,
        })

    print(f"[DB] 저장 완료: {filename}")


def process_image_file(image_path: str, user_id: int = 1):
    """
    단일 이미지 처리
    OCR → JSON백업 → LLM분석 → DB저장 → processed이동
    """
    filename = os.path.basename(image_path)

    try:
        print(f"\n[처리 시작] {filename}")

        # 1. Claude OCR
        data = call_claude(image_path)

        # 2. JSON 백업
        save_json_backup(data, filename)

        # 3. LLM 분석 (실패해도 DB 저장은 계속)
        llm_analysis = call_llm_analysis(data)

        # 4. DB 저장
        save_to_db(data, filename, llm_analysis, user_id)

        # 5. processed 이동
        dest = os.path.join(INBODY_PROCESSED_DIR, filename)
        shutil.move(image_path, dest)

        print(f"[✅ 성공] {filename} → processed/")
        return {"status": "success", "data": data}

    except Exception as e:
        print(f"[❌ 실패] {filename}: {e}")
        dest = os.path.join(INBODY_FAILED_DIR, filename)
        try:
            shutil.move(image_path, dest)
        except Exception as move_err:
            print(f"[이동 실패] {move_err}")
        return {"status": "error", "message": str(e)}


def batch_process_all():
    """raw 디렉토리의 모든 이미지 일괄 처리"""
    files = sorted([
        f for f in os.listdir(INBODY_RAW_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ])

    print(f"\n[일괄 처리] {len(files)}개 파일 발견")
    for fname in files:
        process_image_file(os.path.join(INBODY_RAW_DIR, fname), user_id=1)
    print(f"\n[완료] 일괄 처리 종료")


if __name__ == "__main__":
    batch_process_all()
