"""
LifeMetrics - 하루 운동 + 식단 통합 분석 (Claude API)
사용법: python daily_analysis.py --date 2026-03-04
"""

import anthropic
import mysql.connector
import argparse
import json
import os
from datetime import date
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

# ───────────────────────────────────────────
# 환경변수에서 설정 로드
# ───────────────────────────────────────────
def get_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"{key} 환경변수가 설정되지 않았습니다.")
    return val

DB_CONFIG = {
    "host":     get_env("DB_HOST"),
    "port":     int(os.environ.get("DB_PORT", 3306)),
    "user":     get_env("DB_USER"),
    "password": get_env("DB_PASSWORD"),
    "database": get_env("DB_NAME"),
    "charset":  "utf8mb4",
    "collation": "utf8mb4_general_ci",
}

ANTHROPIC_API_KEY = get_env("ANTHROPIC_API_KEY")


def rows_to_dict(cursor, rows):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


# ───────────────────────────────────────────
# 1. 운동 데이터 조회
# ───────────────────────────────────────────
def fetch_exercise_data(cursor, target_date: str) -> dict:
    cursor.execute("""
        SELECT id, memo
        FROM exercise_session
        WHERE DATE(session_date) = %s AND user_id = 1
    """, (target_date,))
    sessions = rows_to_dict(cursor, cursor.fetchall())

    if not sessions:
        return {"sessions": [], "logs": []}

    session_ids = [s["id"] for s in sessions]
    placeholders = ",".join(["%s"] * len(session_ids))

    cursor.execute(f"""
        SELECT
            el.session_id,
            ei.name_ko AS exercise_name,
            ei.equipment_type,
            ec.name AS category,
            el.weight,
            el.reps,
            el.sets
        FROM exercise_log el
        JOIN exercise_item ei ON el.exercise_item_id = ei.id
        JOIN exercise_category ec ON ei.category_id = ec.id
        WHERE el.session_id IN ({placeholders})
        ORDER BY el.session_id, el.id
    """, session_ids)
    logs = rows_to_dict(cursor, cursor.fetchall())

    return {"sessions": sessions, "logs": logs}


# ───────────────────────────────────────────
# 2. 식단 데이터 조회
# ───────────────────────────────────────────
def fetch_food_data(cursor, target_date: str) -> list:
    cursor.execute("""
        SELECT
            meal_type,
            food_name,
            calories,
            carbs,
            protein,
            fat
        FROM food_log
        WHERE log_date = %s AND user_id = 1
        ORDER BY FIELD(meal_type, 'breakfast', 'lunch', 'snack', 'dinner')
    """, (target_date,))
    return rows_to_dict(cursor, cursor.fetchall())


# ───────────────────────────────────────────
# 3. 운동 볼륨 계산
# ───────────────────────────────────────────
def calc_exercise_volume(logs: list) -> dict:
    volume_by_exercise = {}
    for log in logs:
        name = log["exercise_name"]
        volume = (log["weight"] or 0) * (log["reps"] or 0)
        if name not in volume_by_exercise:
            volume_by_exercise[name] = {
                "category": log["category"],
                "equipment": log["equipment_type"],
                "total_sets": 0,
                "total_reps": 0,
                "total_volume_kg": 0,
                "max_weight": 0,
            }
        v = volume_by_exercise[name]
        v["total_sets"] += 1
        v["total_reps"] += log["reps"] or 0
        v["total_volume_kg"] += volume
        v["max_weight"] = max(v["max_weight"], log["weight"] or 0)

    return volume_by_exercise


# ───────────────────────────────────────────
# 4. 영양 합산
# ───────────────────────────────────────────
def calc_nutrition(food_logs: list) -> dict:
    totals = {"calories": 0, "carbs": 0, "protein": 0, "fat": 0}
    by_meal = {}
    for f in food_logs:
        meal = f["meal_type"]
        if meal not in by_meal:
            by_meal[meal] = []
        by_meal[meal].append(f["food_name"])
        for key in totals:
            totals[key] += f[key] or 0
    return {"totals": totals, "by_meal": by_meal}


# ───────────────────────────────────────────
# 5. Claude API 분석 요청
# ───────────────────────────────────────────
def analyze_with_claude(target_date: str, exercise_volume: dict, nutrition: dict) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""
다음은 {target_date} 하루 운동 및 식단 데이터입니다. 한국어로 분석해주세요.

## 운동 데이터 (볼륨 분석)
{json.dumps(exercise_volume, ensure_ascii=False, indent=2)}

## 식단 데이터
### 총 영양소 섭취
- 칼로리: {nutrition['totals']['calories']:.0f} kcal
- 탄수화물: {nutrition['totals']['carbs']:.1f} g
- 단백질: {nutrition['totals']['protein']:.1f} g
- 지방: {nutrition['totals']['fat']:.1f} g

### 식사별 구성
{json.dumps(nutrition['by_meal'], ensure_ascii=False, indent=2)}

---
아래 항목을 분석해주세요:

1. **운동 볼륨 분석**: 각 종목별 총 볼륨(kg), 세트수, 최고 중량 평가
2. **칼로리 섭취 vs 소모 비교**: 
   - 운동 소모 칼로리는 총 볼륨(kg) 기준으로 추정 (저항운동 MET 활용)
   - 섭취 칼로리와 비교하여 잉여/부족 판단
3. **영양소 균형 분석**: 탄/단/지 비율 평가, 근성장 관점에서 단백질 충분 여부
4. **총평 및 내일을 위한 한줄 조언**
"""

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ───────────────────────────────────────────
# 메인
# ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()), help="분석 날짜 (YYYY-MM-DD)")
    args = parser.parse_args()
    target_date = args.date

    print(f"\n{'='*50}")
    print(f"  LifeMetrics 하루 분석: {target_date}")
    print(f"{'='*50}\n")

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        exercise_data = fetch_exercise_data(cursor, target_date)
        food_data = fetch_food_data(cursor, target_date)

        if not exercise_data["logs"] and not food_data:
            print(f"[!] {target_date} 데이터가 없습니다.")
            return

        exercise_volume = calc_exercise_volume(exercise_data["logs"])
        nutrition = calc_nutrition(food_data)

        print("[운동 요약]")
        for name, v in exercise_volume.items():
            print(f"  {name}: {v['total_sets']}세트 / 최고 {v['max_weight']}kg / 볼륨 {v['total_volume_kg']:.0f}kg")

        print("\n[식단 요약]")
        n = nutrition["totals"]
        print(f"  칼로리: {n['calories']:.0f} kcal | 탄: {n['carbs']:.1f}g | 단: {n['protein']:.1f}g | 지: {n['fat']:.1f}g")

        print("\n[Claude API 분석 중...]\n")
        analysis = analyze_with_claude(target_date, exercise_volume, nutrition)
        print(analysis)

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
