#!/usr/bin/env python3
import pymysql

import anthropic
import json
import os
import time
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
BATCH_SIZE = 20

SYSTEM_PROMPT = """당신은 피트니스 전문가입니다.
한국어 운동명을 받으면 아래 JSON 배열 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

[
  {
    "id": 숫자,
    "name_en": "영문 운동명",
    "description": "한국어로 2-3문장 운동 설명 (주요 근육, 올바른 자세 포인트)",
    "equipment_type": "barbell|dumbbell|machine|cable|bodyweight|band|kettlebell|smith_machine|other"
  }
]

equipment_type 선택 기준:
- barbell: 바벨 사용
- dumbbell: 덤벨 사용
- machine: 고정 머신
- cable: 케이블 머신
- bodyweight: 맨몸 운동
- band: 밴드/저항 밴드
- kettlebell: 케틀벨
- smith_machine: 스미스 머신
- other: 기타"""


def fetch_exercises(conn):
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT id, name_ko 
            FROM exercise_item 
            WHERE name_en IS NULL OR name_en = ''
            ORDER BY id
        """)
        return cursor.fetchall()


def update_exercises(conn, results):
    with conn.cursor() as cursor:
        for r in results:
            cursor.execute("""
                UPDATE exercise_item 
                SET name_en = %s, description = %s, equipment_type = %s
                WHERE id = %s
            """, (r["name_en"], r["description"], r["equipment_type"], r["id"]))
    conn.commit()


def process_batch(client, batch):
    user_content = json.dumps(
        [{"id": r["id"], "name_ko": r["name_ko"]} for r in batch],
        ensure_ascii=False
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}]
    )
    text = response.content[0].text.strip()
    return json.loads(text)


def main():
    print("DB 연결 중...")
    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    exercises = fetch_exercises(conn)
    total = len(exercises)
    print(f"처리할 운동 수: {total}개")

    success = 0
    fail = 0

    for i in range(0, total, BATCH_SIZE):
        batch = exercises[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n[{batch_num}/{total_batches}] {[r['name_ko'] for r in batch]}")

        try:
            results = process_batch(client, batch)
            update_exercises(conn, results)
            success += len(results)
            print(f"  ✓ {len(results)}개 완료")
        except json.JSONDecodeError as e:
            print(f"  ✗ JSON 파싱 실패: {e}")
            fail += len(batch)
        except Exception as e:
            print(f"  ✗ 오류: {e}")
            fail += len(batch)

        if i + BATCH_SIZE < total:
            time.sleep(1)

    conn.close()
    print(f"\n완료! 성공: {success}개 / 실패: {fail}개")


if __name__ == "__main__":
    main()
