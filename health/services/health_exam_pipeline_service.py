"""
건강검진 결과지 PDF/이미지 → Claude OCR → LLM 분석 → DB 저장 (health_checkup 테이블)
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
import os
import re
import sys
import json
import shutil
import base64
import pymysql
from pathlib import Path

sys.path.insert(0, "/mnt/200gb/project")
from data_pipeline.claude_config import client, CLAUDE_MODEL, extract_text

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset":  "utf8mb4",
}

RAW_DIR       = "/mnt/200gb/NAS/health_check/raw"
PROCESSED_DIR = "/mnt/200gb/NAS/health_check/processed"
FAILED_DIR    = "/mnt/200gb/NAS/health_check/failed"

os.makedirs(RAW_DIR,       exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(FAILED_DIR,    exist_ok=True)

# ────────────────────────────────────────────
# OCR 추출 항목 = health_checkup 컬럼
# (컬럼명, 타입, 설명)  타입: str / int / float / bool(0·1) / date
# ────────────────────────────────────────────
CHECKUP_FIELDS = [
    ("checkup_date",            "date",  "검진일 (YYYY-MM-DD)"),
    ("checkup_place",           "str",   "검진장소 (예: 내원, 출장)"),
    ("checkup_org",             "str",   "검진기관명"),
    ("checkup_doctor",          "str",   "판정의사 이름"),
    ("overall_judgment",        "str",   "종합판정 (결과지 표기 그대로, 예: 정상A, 정상B(경계), 일반 질환의심, 유질환자)"),
    ("extra_exams",             "str",   "추가로 받은 검사 (쉼표로 구분, 예: 이상지질혈증,우울증검사)"),
    ("suspected_disease",       "str",   "의심질환 (쉼표로 구분)"),
    ("existing_disease",        "str",   "유질환 (쉼표로 구분)"),
    ("lifestyle_advice",        "str",   "생활습관 관리 소견"),
    ("etc_advice",              "str",   "기타 소견/권고사항"),
    ("height_cm",               "float", "키 (cm)"),
    ("weight_kg",               "float", "몸무게 (kg)"),
    ("bmi",                     "float", "체질량지수"),
    ("bmi_grade",               "str",   "비만도 판정 (예: 정상, 과체중, 비만)"),
    ("waist_cm",                "float", "허리둘레 (cm)"),
    ("waist_result",            "str",   "허리둘레 판정 (예: 정상, 복부비만)"),
    ("vision_left",             "float", "시력 좌"),
    ("vision_right",            "float", "시력 우"),
    ("vision_corrected",        "bool",  "교정시력 여부 (1=교정, 0=나안)"),
    ("hearing_left",            "str",   "청력 좌 (1=정상, 0=이상)"),
    ("hearing_right",           "str",   "청력 우 (1=정상, 0=이상)"),
    ("hearing_result",          "str",   "청력 판정"),
    ("systolic_bp",             "int",   "수축기 혈압 (mmHg)"),
    ("diastolic_bp",            "int",   "이완기 혈압 (mmHg)"),
    ("bp_result",               "str",   "혈압 판정 (예: 정상, 고혈압 전단계, 고혈압 의심)"),
    ("hemoglobin",              "float", "혈색소 (g/dL)"),
    ("anemia_result",           "str",   "빈혈 판정"),
    ("fasting_blood_sugar",     "int",   "공복혈당 (mg/dL)"),
    ("diabetes_result",         "str",   "당뇨병 판정"),
    ("total_cholesterol",       "int",   "총콜레스테롤 (mg/dL)"),
    ("hdl_cholesterol",         "int",   "HDL 콜레스테롤 (mg/dL)"),
    ("triglyceride",            "int",   "중성지방 (mg/dL)"),
    ("ldl_cholesterol",         "int",   "LDL 콜레스테롤 (mg/dL)"),
    ("lipid_result",            "str",   "이상지질혈증 판정"),
    ("serum_creatinine",        "float", "혈청 크레아티닌 (mg/dL)"),
    ("egfr",                    "int",   "신사구체여과율 (mL/min/1.73m²)"),
    ("kidney_result",           "str",   "신장질환 판정"),
    ("ast",                     "int",   "AST(SGOT) (IU/L)"),
    ("alt",                     "int",   "ALT(SGPT) (IU/L)"),
    ("ggt",                     "int",   "감마지티피 (IU/L)"),
    ("liver_result",            "str",   "간장질환 판정"),
    ("urine_protein_result",    "str",   "요단백 판정"),
    ("chest_xray_result",       "str",   "흉부촬영 판정"),
    ("past_history",            "str",   "과거병력 (없으면 '무')"),
    ("medication",              "str",   "약물치료 (없으면 '무')"),
    ("need_smoking_cessation",  "bool",  "생활습관 처방: 금연 필요 (1/0)"),
    ("need_drinking_reduction", "bool",  "생활습관 처방: 절주 필요 (1/0)"),
    ("need_physical_activity",  "bool",  "생활습관 처방: 신체활동 필요 (1/0)"),
    ("need_strength_exercise",  "bool",  "생활습관 처방: 근력운동 필요 (1/0)"),
    ("hep_b_result",            "str",   "B형간염 판정"),
    ("hep_c_result",            "str",   "C형간염 판정"),
    ("depression_result",       "str",   "우울증 검사 판정"),
    ("depression_score",        "int",   "우울증 검사 점수"),
    ("psychosis_result",        "str",   "조기정신증 검사 판정"),
    ("cognitive_result",        "str",   "인지기능장애 검사 판정"),
    ("bone_density_result",     "str",   "골밀도 검사 판정"),
    ("urination_result",        "str",   "배뇨장애 검사 판정"),
    ("raw_text",                "str",   "결과지 전체 텍스트를 그대로 옮겨 적은 것"),
]

# structured outputs 제한(null 허용 필드 16개, 선택 필드 24개) 때문에
# 전부 필수 문자열로 받고, 없는 항목은 ""로 받아서 parse_ocr_result()에서 타입 변환
OCR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [name for name, _, _ in CHECKUP_FIELDS],
    "properties": {name: {"type": "string", "description": desc} for name, _, desc in CHECKUP_FIELDS},
}

OCR_PROMPT = """이 건강검진 결과통보서에서 각 항목을 추출해 주세요.
- 결과지에 없는 항목은 빈 문자열("")로 두세요.
- 숫자 항목은 단위 없이 숫자만 적으세요.
- 판정 항목은 결과지 표기를 그대로 옮기되, 뒤에 붙은 점수 범위 안내(예: "(0~4점)")는 빼세요.
- "해당사항없음", "비해당"처럼 받지 않았거나 해당 없는 항목은 빈 문자열("")로 두세요.

""" + "\n".join(f"- {name}: {desc}" for name, _, desc in CHECKUP_FIELDS)


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


# 주민등록번호: YYMMDD-NNNNNNN (뒷자리 일부가 *로 가려진 표기 포함) → 전부 마스킹
_RRN_PATTERN = re.compile(r"(?<!\d)\d{6}\s*[-–]\s*[1-8][\d*]{0,6}(?![\d*])")


def mask_rrn(text: str | None) -> str | None:
    if not text:
        return text
    return _RRN_PATTERN.sub("******-*******", text)


def parse_ocr_result(raw: dict) -> dict:
    """OCR 문자열 결과 → health_checkup 컬럼 타입으로 변환 ("" 이나 변환 불가 → None)"""
    def convert(v: str, kind: str):
        v = (v or "").strip()
        if not v:
            return None
        try:
            if kind == "int":
                return int(float(v))
            if kind == "float":
                return float(v)
            if kind == "bool":
                return 1 if v in ("1", "true", "True", "예", "필요") else 0
        except ValueError:
            return None
        return v

    result = {name: convert(raw.get(name), kind) for name, kind, _ in CHECKUP_FIELDS}
    result["raw_text"] = mask_rrn(result["raw_text"])
    return result


def call_claude_ocr(file_path: str) -> dict:
    print(f"[OCR] 시작: {file_path}")
    data, media_type = file_to_base64(file_path)

    block_type = "document" if media_type == "application/pdf" else "image"
    content = [
        {"type": block_type, "source": {"type": "base64", "media_type": media_type, "data": data}},
        {"type": "text", "text": OCR_PROMPT},
    ]

    # raw_text(결과지 전문)까지 받으므로 max_tokens 넉넉히 (thinking 토큰 포함)
    # output_config는 SDK 0.75에 인자가 없어서 extra_body로 전달
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
        extra_body={"output_config": {"format": {"type": "json_schema", "schema": OCR_SCHEMA}}},
    )
    if response.stop_reason == "max_tokens":
        raise ValueError("OCR 응답이 max_tokens에서 잘림")

    result = parse_ocr_result(json.loads(extract_text(response)))
    if not result["checkup_date"]:
        raise ValueError("검진일(checkup_date)을 추출하지 못함")
    print(f"[OCR] 완료: {result.get('checkup_date')} / {result.get('checkup_org')}")
    return result


# ────────────────────────────────────────────
# LLM 분석
# ────────────────────────────────────────────

def get_prev_records(checkup_date, user_id: int = 1) -> list:
    """DB에서 해당 검진일 이전의 검진 기록 조회 (재처리 시 자기 자신이 섞이지 않도록)"""
    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT *
                FROM health_checkup
                WHERE user_id = %s AND checkup_date < %s
                ORDER BY checkup_date ASC
            """, (user_id, checkup_date))
            return list(c.fetchall())
    finally:
        conn.close()


def build_llm_prompt(current: dict, prev: dict | None, all_records: list) -> str:
    def fmt(v, unit=""):
        return f"{v}{unit}" if v is not None else "미측정"

    def with_result(v, unit, result_key):
        r = current.get(result_key)
        return f"{fmt(v, unit)}" + (f" ({r})" if r else "")

    lines = [
        f"## 건강검진 결과 분석",
        f"### 현재 검진일: {current.get('checkup_date')} ({current.get('checkup_org')})",
        "",
        "### 현재 측정값 (괄호는 결과지 판정)",
        f"- 키/몸무게: {fmt(current.get('height_cm'), 'cm')} / {fmt(current.get('weight_kg'), 'kg')}",
        f"- BMI: {with_result(current.get('bmi'), '', 'bmi_grade')} / 허리둘레: {with_result(current.get('waist_cm'), 'cm', 'waist_result')}",
        f"- 혈압: {fmt(current.get('systolic_bp'))}/{fmt(current.get('diastolic_bp'))} mmHg" + (f" ({current['bp_result']})" if current.get("bp_result") else ""),
        f"- 혈색소: {with_result(current.get('hemoglobin'), 'g/dL', 'anemia_result')}",
        f"- 공복혈당: {with_result(current.get('fasting_blood_sugar'), 'mg/dL', 'diabetes_result')}",
        f"- 총콜레스테롤: {fmt(current.get('total_cholesterol'), 'mg/dL')}",
        f"- HDL: {fmt(current.get('hdl_cholesterol'), 'mg/dL')} / LDL: {fmt(current.get('ldl_cholesterol'), 'mg/dL')}",
        f"- 중성지방: {with_result(current.get('triglyceride'), 'mg/dL', 'lipid_result')}",
        f"- 크레아티닌: {fmt(current.get('serum_creatinine'), 'mg/dL')} / eGFR: {with_result(current.get('egfr'), '', 'kidney_result')}",
        f"- AST: {fmt(current.get('ast'), 'IU/L')} / ALT: {fmt(current.get('alt'), 'IU/L')} / γ-GTP: {with_result(current.get('ggt'), 'IU/L', 'liver_result')}",
        f"- 요단백: {fmt(current.get('urine_protein_result'))}",
        f"- 종합판정: {fmt(current.get('overall_judgment'))}",
        f"- 의심질환: {fmt(current.get('suspected_disease'))} / 유질환: {fmt(current.get('existing_disease'))}",
        f"- 의사 소견: {fmt(current.get('lifestyle_advice'))} {current.get('etc_advice') or ''}",
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
            f"### 이전 검진 대비 변화 ({prev.get('checkup_date')})",
            f"- 체중: {diff('weight_kg', 'kg')}",
            f"- BMI: {diff('bmi')}",
            f"- 허리둘레: {diff('waist_cm', 'cm')}",
            f"- 혈압(수축기): {diff('systolic_bp', 'mmHg')}",
            f"- 공복혈당: {diff('fasting_blood_sugar', 'mg/dL')}",
            f"- AST: {diff('ast', 'IU/L')} / ALT: {diff('alt', 'IU/L')}",
            f"- γ-GTP: {diff('ggt', 'IU/L')}",
        ]

    if len(all_records) > 1:
        lines += ["", f"### 전체 검진 이력 ({len(all_records)}회)"]
        for r in all_records:
            lines.append(
                f"- {r.get('checkup_date')}: "
                f"체중{fmt(r.get('weight_kg'), 'kg')} "
                f"혈압{fmt(r.get('systolic_bp'))}/{fmt(r.get('diastolic_bp'))} "
                f"혈당{fmt(r.get('fasting_blood_sugar'), 'mg/dL')} "
                f"판정:{fmt(r.get('overall_judgment'))}"
            )

    lines += [
        "",
        "---",
        "아래 형식으로 응답:",
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
  "health_score": "0~100 사이 종합 건강점수"
}""",
    ]
    return "\n".join(lines)


# 분석 응답 스키마 (structured outputs로 강제 → JSON 파싱 실패 방지)
_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "trend", "risk_factors", "improvements", "recommendations", "health_score"],
    "properties": {
        "summary": _STR,
        "trend": {
            "type": "object",
            "additionalProperties": False,
            "required": ["weight", "blood_pressure", "glucose", "liver", "kidney", "overall"],
            "properties": {k: _STR for k in ["weight", "blood_pressure", "glucose", "liver", "kidney", "overall"]},
        },
        "risk_factors": _STR_LIST,
        "improvements": {"anyOf": [_STR_LIST, {"type": "null"}]},
        "recommendations": _STR_LIST,
        "health_score": {"type": "integer"},
    },
}


def call_llm_analysis(current: dict, user_id: int = 1) -> dict | None:
    print(f"[분석] LLM 분석 시작...")
    try:
        prev_records = get_prev_records(current["checkup_date"], user_id)
        prev         = prev_records[-1] if prev_records else None
        all_records  = prev_records + [current]
        prompt       = build_llm_prompt(current, prev, all_records)

        # max_tokens는 thinking 토큰까지 포함 → 1500이면 응답이 잘릴 수 있음
        # output_config는 SDK 0.75에 인자가 없어서 extra_body로 전달
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=6000,
            system="건강검진 데이터 분석 전문가. 시계열 건강 변화를 분석.",
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


# ────────────────────────────────────────────
# DB 저장
# ────────────────────────────────────────────

def save_to_db(data: dict, llm_result: dict | None, source_file: str, user_id: int = 1):
    """(user_id, checkup_date) 기준 upsert. 분석 실패(None) 시 기존 llm_analysis는 유지"""
    field_cols = [name for name, _, _ in CHECKUP_FIELDS]
    cols   = ["user_id"] + field_cols + ["llm_analysis", "source_file", "created_at", "updated_at"]
    values = (
        [user_id]
        + [data.get(name) for name in field_cols]
        + [json.dumps(llm_result, ensure_ascii=False) if llm_result else None, source_file]
    )
    updates = [f"{name} = VALUES({name})" for name in field_cols if name != "checkup_date"] + [
        "llm_analysis = COALESCE(VALUES(llm_analysis), llm_analysis)",
        "source_file = VALUES(source_file)",
        "updated_at = NOW()",
    ]
    sql = (
        f"INSERT INTO health_checkup ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * (len(cols) - 2))}, NOW(), NOW()) "
        f"ON DUPLICATE KEY UPDATE {', '.join(updates)}"
    )

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as c:
            c.execute(sql, values)
        conn.commit()
        print(f"[DB] 저장 완료: {data.get('checkup_date')}")
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
        llm_result = call_llm_analysis(data, user_id)

        # 3. DB 저장
        save_to_db(data, llm_result, filename, user_id)

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
