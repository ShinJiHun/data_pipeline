"""
식약처 식품영양성분DB(공공데이터포털) → food_nutrition_master 적재

사용법:
    python load_food_nutrition_master.py                  # 전체 적재 (약 684페이지, 1시간 내외)
    python load_food_nutrition_master.py --max-pages 2    # 테스트용 일부 적재
    python load_food_nutrition_master.py --start-page 120 # 중단된 지점부터 재개

필요 환경변수(.env):
    DATA_GO_KR_API_KEY, DATA_GO_KR_FOOD_NUTRITION_URL, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

영양성분 값은 모두 SERVING_SIZE(100g 또는 100mL) 기준.
1회 섭취량 환산은 food_weight_g(Z10500, 예: 신라면 120g) 사용.
"""

import os
import re
import json
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import unquote

import requests
import pymysql
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# ========================================
# 설정
# ========================================

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "food_nutrition_master.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"{key} 환경변수가 설정되지 않았습니다.")
    return val


# Encoding/Decoding 키 어느 쪽이 들어있어도 동작하도록 디코딩해서 보관 (requests가 인코딩)
API_KEY = unquote(get_env("DATA_GO_KR_API_KEY"))
API_URL = get_env("DATA_GO_KR_FOOD_NUTRITION_URL")

DB_CONFIG = {
    "host": get_env("DB_HOST"),
    "port": int(os.environ.get("DB_PORT", 3306)),
    "user": get_env("DB_USER"),
    "password": get_env("DB_PASSWORD"),
    "database": get_env("DB_NAME"),
    "charset": "utf8mb4",
}

PAGE_SIZE = 500          # API 최대값
MAX_RETRIES = 5
REQUEST_TIMEOUT = 120

# AMT_NUMn → 컬럼 (food_log 컬럼명과 맞춤). 신라면 포장 표시값으로 검증한 매핑.
NUTRIENT_FIELDS = {
    "AMT_NUM1": "calories",        # kcal
    "AMT_NUM2": "water",           # g
    "AMT_NUM3": "protein",         # g
    "AMT_NUM4": "fat",             # g
    "AMT_NUM5": "ash",             # g
    "AMT_NUM6": "carbs",           # g
    "AMT_NUM7": "sugar",           # g
    "AMT_NUM8": "dietary_fiber",   # g
    "AMT_NUM9": "calcium",         # mg
    "AMT_NUM10": "iron",           # mg
    "AMT_NUM11": "phosphorus",     # mg
    "AMT_NUM12": "potassium",      # mg
    "AMT_NUM13": "sodium",         # mg
    "AMT_NUM23": "cholesterol",    # mg
    "AMT_NUM24": "saturated_fat",  # g
    "AMT_NUM25": "trans_fat",      # g
}


# ========================================
# 파싱
# ========================================

def to_float(value):
    """'1,670.000' → 1670.0, '' / None → None"""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_amount(value):
    """'120.000g' → (120.0, 'g'), '100mL' → (100.0, 'mL')"""
    if not value:
        return None, None
    m = re.match(r"^\s*([\d.,]+)\s*([a-zA-Z]*)", str(value))
    if not m:
        return None, None
    return to_float(m.group(1)), (m.group(2) or None)


def parse_date(value):
    if not value:
        return None
    s = str(value).strip()
    if re.match(r"^\d{8}$", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else None


def none_if_blank(value):
    return value if value not in ("", None) else None


def to_row(item: dict) -> dict:
    basis_amount, basis_unit = parse_amount(item.get("SERVING_SIZE"))
    weight, weight_unit = parse_amount(item.get("Z10500"))

    row = {
        "food_cd": item["FOOD_CD"],
        "food_name": item.get("FOOD_NM_KR"),
        "db_group": none_if_blank(item.get("DB_GRP_NM")),
        "db_class": none_if_blank(item.get("DB_CLASS_NM")),
        "category": none_if_blank(item.get("FOOD_CAT1_NM")),
        "food_ref_name": none_if_blank(item.get("FOOD_REF_NM")),
        "maker_name": none_if_blank(item.get("MAKER_NM")),
        "basis_amount": basis_amount,
        "basis_unit": basis_unit,
        "food_weight": weight,
        "food_weight_unit": weight_unit,
        "serving_reference": none_if_blank(item.get("NUTRI_AMOUNT_SERVING")),
        "research_date": parse_date(item.get("RESEARCH_YMD")),
        "source_update_date": parse_date(item.get("UPDATE_DATE")),
    }
    for src, col in NUTRIENT_FIELDS.items():
        row[col] = to_float(item.get(src))

    # 매핑하지 않은 나머지 영양성분은 값이 있는 것만 원본 그대로 보관
    extra = {k: v for k, v in item.items()
             if k.startswith("AMT_NUM") and k not in NUTRIENT_FIELDS and v not in ("", None)}
    row["extra_nutrients"] = json.dumps(extra, ensure_ascii=False) if extra else None
    return row


# ========================================
# API
# ========================================

def fetch_page(session: requests.Session, page_no: int) -> dict:
    params = {"serviceKey": API_KEY, "type": "json", "pageNo": page_no, "numOfRows": PAGE_SIZE}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            code = data.get("header", {}).get("resultCode")
            if code != "00":
                raise RuntimeError(f"API 오류 {code}: {data.get('header', {}).get('resultMsg') or data}")
            return data["body"]
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 5 * attempt
            logger.warning(f"page {page_no} 실패 ({attempt}/{MAX_RETRIES}): {e} → {wait}초 후 재시도")
            time.sleep(wait)


# ========================================
# DB
# ========================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS food_nutrition_master (
    food_cd VARCHAR(40) NOT NULL PRIMARY KEY COMMENT '식품코드',
    food_name VARCHAR(300) NOT NULL,
    db_group VARCHAR(20) DEFAULT NULL COMMENT '음식, 가공식품, 원재료성 등',
    db_class VARCHAR(50) DEFAULT NULL COMMENT '품목대표, 상용제품 등',
    category VARCHAR(100) DEFAULT NULL COMMENT '대분류 (밥류, 면류 등)',
    food_ref_name VARCHAR(100) DEFAULT NULL COMMENT '대표식품명 (국밥, 라면 등)',
    maker_name VARCHAR(200) DEFAULT NULL,
    basis_amount DOUBLE DEFAULT NULL COMMENT '영양성분 기준량 (보통 100)',
    basis_unit VARCHAR(10) DEFAULT NULL COMMENT 'g 또는 mL',
    food_weight DOUBLE DEFAULT NULL COMMENT '식품 1개(봉지/컵 등) 중량',
    food_weight_unit VARCHAR(10) DEFAULT NULL,
    serving_reference VARCHAR(500) DEFAULT NULL COMMENT '1회 섭취참고량 설명',
    calories DOUBLE DEFAULT NULL COMMENT 'kcal',
    water DOUBLE DEFAULT NULL COMMENT 'g',
    protein DOUBLE DEFAULT NULL COMMENT 'g',
    fat DOUBLE DEFAULT NULL COMMENT 'g',
    ash DOUBLE DEFAULT NULL COMMENT 'g',
    carbs DOUBLE DEFAULT NULL COMMENT 'g',
    sugar DOUBLE DEFAULT NULL COMMENT 'g',
    dietary_fiber DOUBLE DEFAULT NULL COMMENT 'g',
    calcium DOUBLE DEFAULT NULL COMMENT 'mg',
    iron DOUBLE DEFAULT NULL COMMENT 'mg',
    phosphorus DOUBLE DEFAULT NULL COMMENT 'mg',
    potassium DOUBLE DEFAULT NULL COMMENT 'mg',
    sodium DOUBLE DEFAULT NULL COMMENT 'mg',
    cholesterol DOUBLE DEFAULT NULL COMMENT 'mg',
    saturated_fat DOUBLE DEFAULT NULL COMMENT 'g',
    trans_fat DOUBLE DEFAULT NULL COMMENT 'g',
    extra_nutrients JSON DEFAULT NULL COMMENT '그 외 AMT_NUMn 원본값',
    research_date DATE DEFAULT NULL,
    source_update_date DATE DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_food_name (food_name),
    INDEX idx_db_group (db_group),
    INDEX idx_maker (maker_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='식약처 식품영양성분DB (100g/100mL 기준)';
"""

COLUMNS = [
    "food_cd", "food_name", "db_group", "db_class", "category", "food_ref_name", "maker_name",
    "basis_amount", "basis_unit", "food_weight", "food_weight_unit", "serving_reference",
    *NUTRIENT_FIELDS.values(),
    "extra_nutrients", "research_date", "source_update_date",
]

UPSERT_SQL = (
    f"INSERT INTO food_nutrition_master ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
    f"ON DUPLICATE KEY UPDATE "
    + ", ".join(f"{c} = VALUES({c})" for c in COLUMNS if c != "food_cd")
)


def upsert_rows(conn, rows: list) -> int:
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, [tuple(r[c] for c in COLUMNS) for r in rows])
    conn.commit()
    return len(rows)


# ========================================
# 메인
# ========================================

def main():
    parser = argparse.ArgumentParser(description="식품영양성분DB → food_nutrition_master 적재")
    parser.add_argument("--start-page", type=int, default=1, help="시작 페이지 (중단 후 재개용)")
    parser.add_argument("--max-pages", type=int, default=None, help="최대 처리 페이지 수 (테스트용)")
    args = parser.parse_args()

    conn = pymysql.connect(**DB_CONFIG)
    session = requests.Session()

    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        first = fetch_page(session, args.start_page)
        total_count = int(first["totalCount"])
        total_pages = -(-total_count // PAGE_SIZE)
        last_page = total_pages
        if args.max_pages:
            last_page = min(total_pages, args.start_page + args.max_pages - 1)
        logger.info(f"총 {total_count:,}건 / {total_pages}페이지 → {args.start_page}~{last_page} 페이지 적재")

        saved = 0
        started = time.time()
        body = first
        for page_no in range(args.start_page, last_page + 1):
            if page_no != args.start_page:
                body = fetch_page(session, page_no)

            items = body.get("items") or []
            # 같은 페이지 안의 중복 food_cd는 마지막 값만 사용
            rows = list({r["food_cd"]: r for r in (to_row(i) for i in items if i.get("FOOD_CD"))}.values())
            saved += upsert_rows(conn, rows)

            elapsed = time.time() - started
            done = page_no - args.start_page + 1
            eta = elapsed / done * (last_page - page_no)
            logger.info(f"page {page_no}/{last_page} 저장 {len(rows)}건 (누적 {saved:,}, 남은 시간 약 {eta / 60:.0f}분)")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM food_nutrition_master")
            logger.info(f"완료: 이번 실행 {saved:,}건 upsert, 테이블 총 {cur.fetchone()[0]:,}건")

    except Exception:
        logger.exception(f"적재 중단. 로그의 마지막 저장 페이지 다음부터 --start-page 로 재개하세요.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
