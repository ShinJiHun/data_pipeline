"""
food_nutrition_master 에서 "제품 1개 기준 값이 100g/100mL 칸에 들어간" 행을 찾아
food_nutrition_correction 에 보정 배율을 기록한다.

예) 칠성사이다 500mL: 기준량 100mL 인데 열량 215kcal(= 한 병 값) → scale 0.2

사용법:
    python detect_food_nutrition_errors.py            # 감지 + 보정 테이블 반영
    python detect_food_nutrition_errors.py --dry-run  # 반영 없이 결과만 출력

판정 규칙 (같은 이름·제조사 제품끼리 비교):
    - 이 행의 열량이 나머지 행 열량 중앙값의 1.8배 초과이고
    - 그 값이 (중앙값 × 용량/기준량) 과 15% 이내로 맞으면 → 1개 기준 값으로 본다.
    - 아래를 모두 만족할 때만 자동 보정(source='auto'), 나머지는 검토 목록(CSV)에만 남긴다.
        · 용량/기준량 배수 2.5 이상 (2배 정도면 어느 쪽이 맞는지 애매함)
        · 제조사가 있음 (제조사 없는 일반 음식은 식당마다 값이 달라 비교가 안 됨)
        · 중앙값과 15% 이내로 맞는 다른 행이 2개 이상 (비교 대상 1개면 그쪽이 틀렸을 수도 있음.
          예: 라이스페이퍼·분말 믹스는 건조 상태 값이 원래 높다)

source='manual' 행은 건드리지 않는다. 오탐을 막으려면 manual 로 scale=1 을 넣어 두면 된다.
master 를 다시 적재(load_food_nutrition_master.py)한 뒤 이 스크립트를 한 번 더 돌린다.
"""

import os
import re
import csv
import logging
import argparse
import statistics
from pathlib import Path
from collections import defaultdict

import pymysql
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# ========================================
# 설정
# ========================================

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
REVIEW_CSV = LOG_DIR / "food_nutrition_review.csv"

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


DB_CONFIG = {
    "host": get_env("DB_HOST"),
    "port": int(os.environ.get("DB_PORT", 3306)),
    "user": get_env("DB_USER"),
    "password": get_env("DB_PASSWORD"),
    "database": get_env("DB_NAME"),
    "charset": "utf8mb4",
}

MIN_RATIO = 1.5        # 용량/기준량 배수가 이보다 작으면 비교 의미가 없음
OUTLIER_FACTOR = 1.8   # 중앙값 대비 이만큼 커야 이상치
MATCH_TOLERANCE = 0.15 # 중앙값×배수 와의 허용 오차
AUTO_RATIO = 2.5       # 이 배수 이상만 자동 보정
MIN_AGREEING = 2       # 중앙값과 맞는 다른 행이 이만큼 있어야 자동 보정

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS food_nutrition_correction (
    food_cd VARCHAR(40) NOT NULL,
    scale DOUBLE NOT NULL COMMENT '영양성분 값에 곱할 배율 (0.2 = 1/5)',
    source VARCHAR(10) NOT NULL DEFAULT 'auto' COMMENT 'auto | manual',
    reason VARCHAR(300) DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (food_cd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='food_nutrition_master 원본 오류 보정 (master 재적재에도 유지)'
"""


# ========================================
# 감지
# ========================================

def normalize_name(name: str) -> str:
    return re.sub(r"\s", "", name or "")


def normalize_maker(maker: str) -> str:
    # "해태제과식품㈜ 청주공장" 과 "해태제과식품㈜" 를 같은 제조사로 본다
    return re.sub(r"\(주\)|㈜|주식회사|\s|공장.*", "", maker or "")


def detect(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT food_cd, food_name, maker_name, basis_amount, basis_unit, food_weight, calories
            FROM food_nutrition_master
            WHERE calories > 0 AND food_weight > 0
        """)
        rows = cur.fetchall()

    groups = defaultdict(list)
    for r in rows:
        groups[(normalize_name(r[1]), normalize_maker(r[2]))].append(r)

    auto, review = [], []
    for members in groups.values():
        if len(members) < 2:
            continue
        for food_cd, name, maker, basis, unit, weight, kcal in members:
            basis = basis or 100.0
            ratio = weight / basis
            if ratio < MIN_RATIO:
                continue
            others = [m[6] for m in members if m[0] != food_cd]
            median = statistics.median(others)
            if kcal <= median * OUTLIER_FACTOR:
                continue
            if abs(kcal / (median * ratio) - 1) > MATCH_TOLERANCE:
                continue
            item = {
                "food_cd": food_cd,
                "food_name": name,
                "maker_name": maker,
                "food_weight": f"{weight:g}{unit or ''}",
                "calories": kcal,
                "sibling_median": median,
                "scale": round(1 / ratio, 6),
                "corrected_calories": round(kcal / ratio, 1),
            }
            agreeing = sum(1 for o in others if abs(o / median - 1) <= MATCH_TOLERANCE)
            confident = ratio >= AUTO_RATIO and maker and agreeing >= MIN_AGREEING
            (auto if confident else review).append(item)
    return auto, review


# ========================================
# 반영
# ========================================

def apply_corrections(conn, auto):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for a in auto:
            reason = (f"1개({a['food_weight']}) 기준 값으로 보임: {a['calories']:g}kcal, "
                      f"같은 제품 중앙값 {a['sibling_median']:g}kcal")
            # manual 행은 사람이 정한 값이므로 덮어쓰지 않는다
            cur.execute("""
                INSERT INTO food_nutrition_correction (food_cd, scale, source, reason)
                VALUES (%s, %s, 'auto', %s)
                ON DUPLICATE KEY UPDATE
                    scale  = IF(source = 'manual', scale, VALUES(scale)),
                    reason = IF(source = 'manual', reason, VALUES(reason))
            """, (a["food_cd"], a["scale"], reason))

        # 더 이상 감지되지 않는 auto 행(원본이 고쳐진 경우)은 지운다
        cur.execute("SELECT food_cd FROM food_nutrition_correction WHERE source = 'auto'")
        stale = {r[0] for r in cur.fetchall()} - {a["food_cd"] for a in auto}
        if stale:
            cur.executemany("DELETE FROM food_nutrition_correction WHERE food_cd = %s AND source = 'auto'",
                            [(cd,) for cd in stale])
    conn.commit()
    return len(stale)


def write_review_csv(review):
    with open(REVIEW_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(review[0].keys()) if review else ["food_cd"])
        writer.writeheader()
        writer.writerows(review)


# ========================================
# 메인
# ========================================

def main():
    parser = argparse.ArgumentParser(description="food_nutrition_master 1개 기준 값 오류 감지")
    parser.add_argument("--dry-run", action="store_true", help="보정 테이블에 반영하지 않음")
    args = parser.parse_args()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        auto, review = detect(conn)
        for a in auto:
            logger.info(f"[auto] {a['food_name']} {a['food_weight']} ({a['maker_name']}): "
                        f"{a['calories']:g} → {a['corrected_calories']:g}kcal/100")
        write_review_csv(review)
        logger.info(f"감지: 자동 보정 {len(auto)}건, 검토 필요 {len(review)}건 → {REVIEW_CSV}")

        if args.dry_run:
            logger.info("dry-run: 보정 테이블에 반영하지 않음")
            return
        removed = apply_corrections(conn, auto)
        logger.info(f"반영 완료: auto {len(auto)}건 upsert, 해제 {removed}건")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
