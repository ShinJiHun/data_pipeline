"""
PT 운동 기록 CSV 임포트 v2
- 세트별 무게 파싱 (20/22/25/30 형식)
- exercise_set 테이블에 세트별 상세 저장
"""

import csv
import os
import pymysql
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset":  "utf8mb4",
}

CSV_PATH = "/mnt/200gb/NAS/data/health/exercise.csv"
USER_ID  = 1


def parse_weights(weight_str, num_sets):
    """'20/22/25/30' or '40*2/45' → [20, 22, 25, 30]"""
    if not weight_str:
        return [None] * num_sets

    w = str(weight_str).strip()
    if w in ('', '/', '-', '0/0/0', '0/0', '/'):
        return [None] * num_sets

    # '*2' 같은 표현 처리 (40*2 → 40, 40)
    parts = []
    for part in w.split('/'):
        part = part.strip()
        if '*' in part:
            val, cnt = part.split('*')
            try:
                parts.extend([float(val.strip())] * int(cnt.strip()))
            except:
                parts.append(None)
        else:
            try:
                parts.append(float(part))
            except:
                parts.append(None)

    if not parts:
        return [None] * num_sets

    # sets 수에 맞게 조정
    if len(parts) < num_sets:
        parts += [parts[-1]] * (num_sets - len(parts))

    return parts[:num_sets]


def to_int(val):
    try:
        v = str(val).replace('/', '').replace('-', '').strip()
        return int(float(v)) if v else None
    except:
        return None


def get_or_create_category(cur, name):
    cur.execute("SELECT id FROM exercise_category WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO exercise_category (name) VALUES (%s)", (name,))
    return cur.lastrowid


def get_or_create_item(cur, category_id, name_ko, name_en):
    cur.execute(
        "SELECT id FROM exercise_item WHERE name_ko = %s",
        (name_ko,)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO exercise_item (category_id, name_ko, name_en) VALUES (%s, %s, %s)",
        (category_id, name_ko, name_en or '')
    )
    return cur.lastrowid


def get_or_create_session(cur, user_id, session_date):
    cur.execute(
        "SELECT id FROM exercise_session WHERE user_id = %s AND session_date = %s",
        (user_id, session_date)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO exercise_session (user_id, session_date, is_pt) VALUES (%s, %s, 1)",
        (user_id, session_date)
    )
    return cur.lastrowid


def parse_csv():
    sessions = {}
    current_session_no = None
    current_body_part  = None
    current_date       = None

    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # 헤더 찾기 (session_no 있는 행)
    data_start = None
    for i, row in enumerate(rows):
        if len(row) > 1 and row[1].strip() == 'session_no':
            data_start = i + 1
            break

    if data_start is None:
        print("❌ 헤더 행을 찾지 못했습니다.")
        return {}

    for row in rows[data_start:]:
        if not any(c.strip() for c in row):
            continue

        col_session  = row[1].strip() if len(row) > 1 else ""
        col_body     = row[2].strip() if len(row) > 2 else ""
        col_date     = row[3].strip() if len(row) > 3 else ""
        col_exko     = row[4].strip() if len(row) > 4 else ""
        col_exen     = row[5].strip() if len(row) > 5 else ""
        col_weight   = row[6].strip() if len(row) > 6 else ""
        col_reps     = row[7].strip() if len(row) > 7 else ""
        col_sets     = row[8].strip() if len(row) > 8 else ""
        col_memo     = row[9].strip() if len(row) > 9 else ""

        if col_session:
            try:
                current_session_no = int(col_session)
                current_body_part  = col_body
                current_date       = col_date
            except ValueError:
                continue

        if not current_session_no or not col_exko:
            continue

        # 날짜 유효성
        if not current_date or len(current_date) < 8:
            continue

        reps_val = to_int(col_reps)
        sets_val = to_int(col_sets) or 1

        if current_session_no not in sessions:
            sessions[current_session_no] = {
                "date":      current_date,
                "body_part": current_body_part,
                "exercises": []
            }

        weights = parse_weights(col_weight, sets_val)

        sessions[current_session_no]["exercises"].append({
            "ko":      col_exko,
            "en":      col_exen,
            "weight":  col_weight,
            "weights": weights,
            "reps":    reps_val,
            "sets":    sets_val,
            "memo":    col_memo,
        })

    return sessions


def main():
    print("🚀 PT 운동 기록 임포트 v2 시작")

    sessions = parse_csv()
    print(f"  📋 파싱된 세션: {len(sessions)}개")

    conn = pymysql.connect(**DB_CONFIG)
    cur  = conn.cursor()

    inserted_sessions = 0
    inserted_logs     = 0
    inserted_sets     = 0
    skipped           = 0

    for session_no, data in sorted(sessions.items()):
        date      = data["date"]
        body_part = data["body_part"]
        exercises = data["exercises"]

        if not exercises:
            continue

        # 카테고리 정규화
        category_name = body_part.split('/')[0].strip() if body_part else "기타"

        try:
            category_id = get_or_create_category(cur, category_name)
            session_id  = get_or_create_session(cur, USER_ID, date)

            # 이미 로그 있으면 스킵
            cur.execute(
                "SELECT COUNT(*) FROM exercise_log WHERE session_id = %s",
                (session_id,)
            )
            if cur.fetchone()[0] > 0:
                skipped += 1
                continue

            for ex in exercises:
                item_id = get_or_create_item(cur, category_id, ex["ko"], ex["en"])

                # 평균 무게 계산
                valid_weights = [w for w in ex["weights"] if w is not None]
                avg_weight = sum(valid_weights) / len(valid_weights) if valid_weights else None

                cur.execute("""
                    INSERT INTO exercise_log
                        (session_id, exercise_item_id, weight, reps, sets, memo)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    session_id,
                    item_id,
                    avg_weight,
                    ex["reps"],
                    ex["sets"],
                    ex["memo"] or None
                ))
                log_id = cur.lastrowid
                inserted_logs += 1

                # 세트별 상세 저장
                for set_num, w in enumerate(ex["weights"], start=1):
                    cur.execute("""
                        INSERT INTO exercise_set
                            (log_id, set_number, weight, reps)
                        VALUES (%s, %s, %s, %s)
                    """, (log_id, set_num, w, ex["reps"]))
                    inserted_sets += 1

            inserted_sessions += 1
            conn.commit()
            print(f"  ✅ session {session_no} ({date}) → {len(exercises)}종목")

        except Exception as e:
            conn.rollback()
            print(f"  ❌ session {session_no} ({date}) 실패: {e}")

    cur.close()
    conn.close()

    print(f"""
========================================
🎉 임포트 완료
   세션: {inserted_sessions}개 삽입 | {skipped}개 스킵
   로그: {inserted_logs}개 삽입
   세트: {inserted_sets}개 삽입
========================================
""")


if __name__ == "__main__":
    main()
