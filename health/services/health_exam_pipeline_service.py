"""
건강검진 결과지 PDF/이미지 → Claude OCR → LLM 분석 → DB 저장
"""

import os
import json
import shutil
import base64
import pymysql
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv("/mnt/200gb/apps/.env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.getenv("DB_PASSWORD", "ride1234!"),
    "database": "riding_db",
    "charset":  "utf8mb4",
}

RAW_DIR       = "/mnt/200gb/NAS/health_check/raw"
PROCESSED_DIR = "/mnt/200gb/NAS/health_check/processed"
FAILED_DIR    = "/mnt/200gb/NAS/health_check/failed"

os.makedirs(RAW_DIR,       exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(FAILED_DIR,    exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

OCR_PROMPT = """이 건강검진 결과지에서 다음 항목을 추출해서 JSON으로 반환해주세요.
없는 항목은 null로 반환하세요.
JSON만 반환하고 다른 텍스트는 포함하지 마세요.

{
  "exam_date": "검진일 (YYYY-MM-DD)",
  "exam_place": "검진기관명",
  "height_cm": 키(숫자),
  "weight_kg": 몸무게(숫자),
  "bmi": BMI(숫자),
  "waist_cm": 허리둘레(숫자),
  "systolic_bp": 수축기혈압(숫자),
  "diastolic_bp": 이완기혈압(숫자),
  "vision_left": 시력좌(숫자),
  "vision_right": 시력우(숫자),
  "hearing_left": 청력좌(1=정상, 0=이상),
  "hearing_right": 청력우(1=정상, 0=이상),
  "hemoglobin": 혈색소(숫자),
  "fasting_glucose": 공복혈당(숫자),
  "total_cholesterol": 총콜레스테롤(숫자),
  "hdl_cholesterol": HDL콜레스테롤(숫자),
  "ldl_cholesterol": LDL콜레스테롤(숫자),
  "triglyceride": 중성지방(숫자),
  "creatinine": 혈청크레아티닌(숫자),
  "egfr": 신사구체여과율(숫자),
  "ast": AST_SGOT(숫자),
  "alt": ALT_SGPT(숫자),
  "ggt": 감마GTP(숫자),
  "urine_protein": 요단백("정상" or "경계" or "단백뇨의심"),
  "overall_result": 종합판정("정상A" or "정상B" or "일반질환의심" or "고혈압당뇨의심" or "유질환자"),
  "note": 특이소견이나 생활습관관리 권고사항 요약
}"""


# ────────────────────────────────────────────
# OCR
# ────────────────────────────────────────────

def file_to_base64(file_path: str) -> tuple[str, str]:
    ext = Path(file_path).suffix.lower()
    media_type_map = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    media_type = media_type_map.get(ext)
    if not media_type:
        raise ValueError(f"지원하지 않는 파일 형식: {ext}")
    with open(file_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def call_claude_ocr(file_path: str) -> dict:
    print(f"[OCR] 시작: {file_path}")
    data, media_type = file_to_base64(file_path)

    if media_type == "application/pdf":
        content = [
            {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": data}},
            {"type": "text", "text": OCR_PROMPT},
        ]
    else:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
            {"type": "text", "text": OCR_PROMPT},
        ]

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )

    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        raw_text = raw_text.rsplit("```", 1)[0]

    result = json.loads(raw_text.strip())
    print(f"[OCR] 완료: {result.get('exam_date')} / {result.get('exam_place')}")
    return result


# ────────────────────────────────────────────
# LLM 분석
# ────────────────────────────────────────────

def get_prev_records() -> list:
    """DB에서 기존 검진 기록 전체 조회"""
    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT exam_date, exam_place,
                       height_cm, weight_kg, bmi, waist_cm,
                       systolic_bp, diastolic_bp,
                       hemoglobin, fasting_glucose,
                       total_cholesterol, hdl_cholesterol, ldl_cholesterol, triglyceride,
                       creatinine, egfr, ast, alt, ggt,
                       urine_protein, overall_result
                FROM user_health_exam
                WHERE user_id = 1
                ORDER BY exam_date ASC
            """)
            return list(c.fetchall())
    finally:
        conn.close()


def build_llm_prompt(current: dict, prev: dict | None, all_records: list) -> str:
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
        lines += ["", f"### 전체 검진 이력 ({len(all_records)}회)"]
        for r in all_records:
            lines.append(
                f"- {r.get('exam_date')}: "
                f"체중{fmt(r.get('weight_kg'), 'kg')} "
                f"혈압{fmt(r.get('systolic_bp'))}/{fmt(r.get('diastolic_bp'))} "
                f"혈당{fmt(r.get('fasting_glucose'), 'mg/dL')} "
                f"판정:{fmt(r.get('overall_result'))}"
            )

    lines += [
        "",
        "---",
        "순수 JSON만 출력:",
        """{
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
  "health_score": 0
}""",
    ]
    return "\n".join(lines)


def call_llm_analysis(current: dict) -> dict | None:
    print(f"[분석] LLM 분석 시작...")
    try:
        prev_records = get_prev_records()
        prev         = prev_records[-1] if prev_records else None
        all_records  = prev_records + [current]
        prompt       = build_llm_prompt(current, prev, all_records)

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
        result = json.loads(t.strip())
        print(f"[분석] 완료: {result.get('summary', '')}")
        return result
    except Exception as e:
        print(f"[분석] ⚠️ 실패: {e}")
        return None


# ────────────────────────────────────────────
# DB 저장
# ────────────────────────────────────────────

def save_to_db(data: dict, llm_result: dict | None, user_id: int = 1):
    def to_float(v):
        try: return float(v) if v is not None else None
        except: return None

    def to_int(v):
        try: return int(float(v)) if v is not None else None
        except: return None

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO user_health_exam (
                    user_id, exam_date, exam_place,
                    height_cm, weight_kg, bmi, waist_cm,
                    systolic_bp, diastolic_bp,
                    vision_left, vision_right,
                    hearing_left, hearing_right,
                    hemoglobin, fasting_glucose,
                    total_cholesterol, hdl_cholesterol,
                    ldl_cholesterol, triglyceride,
                    creatinine, egfr,
                    ast, alt, ggt,
                    urine_protein, overall_result,
                    note, raw_llm_json
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s
                )
                ON DUPLICATE KEY UPDATE
                    exam_place        = VALUES(exam_place),
                    height_cm         = VALUES(height_cm),
                    weight_kg         = VALUES(weight_kg),
                    bmi               = VALUES(bmi),
                    waist_cm          = VALUES(waist_cm),
                    systolic_bp       = VALUES(systolic_bp),
                    diastolic_bp      = VALUES(diastolic_bp),
                    vision_left       = VALUES(vision_left),
                    vision_right      = VALUES(vision_right),
                    hearing_left      = VALUES(hearing_left),
                    hearing_right     = VALUES(hearing_right),
                    hemoglobin        = VALUES(hemoglobin),
                    fasting_glucose   = VALUES(fasting_glucose),
                    total_cholesterol = VALUES(total_cholesterol),
                    hdl_cholesterol   = VALUES(hdl_cholesterol),
                    ldl_cholesterol   = VALUES(ldl_cholesterol),
                    triglyceride      = VALUES(triglyceride),
                    creatinine        = VALUES(creatinine),
                    egfr              = VALUES(egfr),
                    ast               = VALUES(ast),
                    alt               = VALUES(alt),
                    ggt               = VALUES(ggt),
                    urine_protein     = VALUES(urine_protein),
                    overall_result    = VALUES(overall_result),
                    note              = VALUES(note),
                    raw_llm_json      = VALUES(raw_llm_json),
                    updated_at        = CURRENT_TIMESTAMP
            """, (
                user_id,
                data.get("exam_date"),
                data.get("exam_place"),
                to_float(data.get("height_cm")),
                to_float(data.get("weight_kg")),
                to_float(data.get("bmi")),
                to_float(data.get("waist_cm")),
                to_int(data.get("systolic_bp")),
                to_int(data.get("diastolic_bp")),
                to_float(data.get("vision_left")),
                to_float(data.get("vision_right")),
                to_int(data.get("hearing_left")),
                to_int(data.get("hearing_right")),
                to_float(data.get("hemoglobin")),
                to_int(data.get("fasting_glucose")),
                to_int(data.get("total_cholesterol")),
                to_int(data.get("hdl_cholesterol")),
                to_int(data.get("ldl_cholesterol")),
                to_int(data.get("triglyceride")),
                to_float(data.get("creatinine")),
                to_int(data.get("egfr")),
                to_int(data.get("ast")),
                to_int(data.get("alt")),
                to_int(data.get("ggt")),
                data.get("urine_protein"),
                data.get("overall_result"),
                data.get("note"),
                json.dumps(llm_result, ensure_ascii=False) if llm_result else None,
            ))
        conn.commit()
        print(f"[DB] 저장 완료: {data.get('exam_date')}")
    finally:
        conn.close()


# ────────────────────────────────────────────
# 파일 처리
# ────────────────────────────────────────────

def process_file(file_path: str, user_id: int = 1):
    filename = os.path.basename(file_path)
    try:
        print(f"\n[처리 시작] {filename}")

        # 1. OCR
        data = call_claude_ocr(file_path)

        # 2. LLM 분석
        llm_result = call_llm_analysis(data)

        # 3. DB 저장
        save_to_db(data, llm_result, user_id)

        # 4. processed 이동
        dest = os.path.join(PROCESSED_DIR, filename)
        shutil.move(file_path, dest)
        print(f"[✅ 성공] {filename} → processed/")
        return {"status": "success", "data": data}

    except Exception as e:
        print(f"[❌ 실패] {filename}: {e}")
        dest = os.path.join(FAILED_DIR, filename)
        try:
            shutil.move(file_path, dest)
        except Exception as me:
            print(f"[이동 실패] {me}")
        return {"status": "error", "message": str(e)}


def batch_process_all(user_id: int = 1):
    exts = (".pdf", ".png", ".jpg", ".jpeg")
    files = sorted([
        f for f in os.listdir(RAW_DIR)
        if f.lower().endswith(exts)
    ])
    print(f"\n[일괄 처리] {len(files)}개 파일 발견")
    for fname in files:
        process_file(os.path.join(RAW_DIR, fname), user_id)
    print(f"\n[완료]")


if __name__ == "__main__":
    batch_process_all()
