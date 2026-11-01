"""
운동 기록 CSV → DB 임포트 스크립트
대상 테이블: exercise_category, exercise_item, exercise_session, exercise_log
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
import csv
import os
import sys
import mysql.connector
from datetime import datetime

# ── 설정 ──────────────────────────────────────────────
DB_CONFIG = {
    "host":     "172.17.0.1",
    "port":     3306,
    "user":     "tho881",
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset":  "utf8mb4",
    "collation": "utf8mb4_unicode_ci",
}

CSV_PATH = "/mnt/200gb/NAS/data/health/exercise.csv"
USER_ID  = 1

# PT 세션 판별: body_part에 PT 키워드 포함 여부 (필요시 수동 조정)
PT_KEYWORDS = []  # 데이터에 PT 구분 없으므로 기본 False

# ── body_part → category 정규화 ────────────────────────
CATEGORY_MAP = {
    "하체": "하체",
    "하체 / 삼두": "하체",
    "하체 / 이두": "하체",
    "허채/삼두/복부": "하체",
    "가슴": "가슴",
    "가슴/삼두": "가슴",
    "등": "등",
    "등/가슴/삼두": "등",
    "어깨": "어깨",
    "어깨(측면)": "어깨",
    "하체 / 어깨": "어깨",
    "허리/어깨": "어깨",
    "삼두": "삼두",
    "이두 / 삼두": "이두/삼두",
    "이두/ 삼두": "이두/삼두",
    "이두/삼두": "이두/삼두",
    "복근/ 이두": "이두/삼두",
    "서킷": "기타",
    "슈퍼 세트": "기타",
    "둥": "등",  # 오타
    "허리": "하체",
}

# ── DB 연결 ────────────────────────────────────────────
def get_conn():
    return mysql.connector.connect(**DB_CONFIG)

# ── 카테고리 upsert ────────────────────────────────────
def get_or_create_category(cur, name):
    cur.execute("SELECT id FROM exercise_category WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO exercise_category (name) VALUES (%s)", (name,))
    return cur.lastrowid

# ── 종목 upsert ───────────────────────────────────────
def get_or_create_item(cur, category_id, name_ko, name_en):
    cur.execute(
        "SELECT id FROM exercise_item WHERE category_id = %s AND name = %s",
        (category_id, name_ko)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO exercise_item (category_id, name, description) VALUES (%s, %s, %s)",
        (category_id, name_ko, name_en)
    )
    return cur.lastrowid

# ── 세션 upsert ───────────────────────────────────────
def get_or_create_session(cur, user_id, session_date, memo="", is_pt=False):
    cur.execute(
        "SELECT id FROM exercise_session WHERE user_id = %s AND session_date = %s",
        (user_id, session_date)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO exercise_session (user_id, session_date, memo, is_pt) VALUES (%s, %s, %s, %s)",
        (user_id, session_date, memo, is_pt)
    )
    return cur.lastrowid

# ── CSV 파싱 ──────────────────────────────────────────
def parse_csv(path):
    sessions = {}  # session_no → {date, body_part, exercises: [...]}

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # 헤더 행 찾기 (session_no 있는 행)
    header_row = None
    data_start = None
    for i, row in enumerate(rows):
        if row and row[1] == "session_no":
            header_row = i
            data_start = i + 1
            break

    if header_row is None:
        print("❌ 헤더 행을 찾지 못했습니다.")
        sys.exit(1)

    current_session_no = None
    current_body_part  = None
    current_date       = None

    for row in rows[data_start:]:
        # 빈 행 스킵
        if not any(c.strip() for c in row):
            continue

        # 컬럼: [빈칸, session_no, body_part, date, exercise_ko, exercise_en, weight, raps, sets, memo, ...]
        col_session  = row[1].strip() if len(row) > 1 else ""
        col_body     = row[2].strip() if len(row) > 2 else ""
        col_date     = row[3].strip() if len(row) > 3 else ""
        col_exko     = row[4].strip() if len(row) > 4 else ""
        col_exen     = row[5].strip() if len(row) > 5 else ""
        col_weight   = row[6].strip() if len(row) > 6 else ""
        col_reps     = row[7].strip() if len(row) > 7 else ""
        col_sets     = row[8].strip() if len(row) > 8 else ""
        col_memo     = row[9].strip() if len(row) > 9 else ""

        # 새 세션 시작
        if col_session:
            try:
                current_session_no = int(col_session)
            except ValueError:
                continue
            current_body_part = col_body
            current_date      = col_date

        if not current_session_no or not col_exko:
            continue

        # 유효한 날짜 확인
        try:
            datetime.strptime(current_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        # reps/sets 정수 변환 (/, -, 빈값 → None)
        def to_int(v):
            v = v.replace("/", "").replace("-", "").strip()
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return None

        reps_val = to_int(col_reps)
        sets_val = to_int(col_sets)

        if current_session_no not in sessions:
            sessions[current_session_no] = {
                "date":       current_date,
                "body_part":  current_body_part,
                "exercises":  []
            }

        sessions[current_session_no]["exercises"].append({
            "ko":     col_exko,
            "en":     col_exen,
            "weight": col_weight if col_weight and col_weight not in ("/", "-") else None,
            "reps":   reps_val,
            "sets":   sets_val,
            "memo":   col_memo,
        })

    return sessions

# ── 메인 ──────────────────────────────────────────────
def main():
    print("🚀 운동 기록 임포트 시작")
    sessions = parse_csv(CSV_PATH)
    print(f"  📋 파싱된 세션: {len(sessions)}개")

    conn = get_conn()
    cur  = conn.cursor()

    inserted_sessions  = 0
    inserted_logs      = 0
    skipped_sessions   = 0

    for session_no, data in sorted(sessions.items()):
        date      = data["date"]
        body_part = data["body_part"]
        exercises = data["exercises"]

        if not exercises:
            continue

        # 카테고리 정규화
        category_name = CATEGORY_MAP.get(body_part, body_part.split("/")[0].strip())

        try:
            category_id = get_or_create_category(cur, category_name)
            session_id = get_or_create_session(cur, USER_ID, date, is_pt=True)
    
            # 세션이 이미 있고 로그도 있으면 스킵
            cur.execute("SELECT COUNT(*) FROM exercise_log WHERE session_id = %s", (session_id,))
            if cur.fetchone()[0] > 0:
                skipped_sessions += 1
                continue

            for ex in exercises:
                item_id = get_or_create_item(cur, category_id, ex["ko"], ex["en"])

                cur.execute("""
                    INSERT INTO exercise_log
                        (session_id, exercise_item_id, weight, reps, sets, memo)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    session_id,
                    item_id,
                    None,           # weight는 텍스트 형태라 저장 안함 (별도 처리 필요)
                    ex["reps"],
                    ex["sets"],
                    f"{ex['weight']} / {ex['memo']}".strip(" /") if ex["weight"] or ex["memo"] else None
                ))
                inserted_logs += 1

            inserted_sessions += 1
            conn.commit()
            print(f"  ✅ session {session_no} ({date}) → {len(exercises)}개 종목")

        except Exception as e:
            conn.rollback()
            print(f"  ❌ session {session_no} ({date}) 실패: {e}")

    cur.close()
    conn.close()

    print(f"""
========================================
🎉 임포트 완료
   세션: {inserted_sessions}개 삽입 | {skipped_sessions}개 스킵
   로그: {inserted_logs}개 삽입
========================================
""")

if __name__ == "__main__":
    main()
