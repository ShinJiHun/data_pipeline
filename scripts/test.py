#!/usr/bin/env python3
"""
운동 설명 자동 생성
===================
DB의 exercise_item에서 description이 없는 운동들을 가져와서
Claude API로 1-2줄 설명을 생성하고 DB에 저장합니다.

실행:
    python generate_exercise_descriptions.py
    python generate_exercise_descriptions.py --dry-run   # 저장 없이 확인
    python generate_exercise_descriptions.py --limit 10  # 10개만 처리
"""

import os
import time
import argparse
import requests
import mysql.connector
from dotenv import load_dotenv

ENV_PATH = "/mnt/200gb/backup/data_pipeline/.env"
load_dotenv(ENV_PATH)

DB_HOST      = os.getenv("DB_HOST", "127.0.0.1")
DB_USER      = os.getenv("DB_USER", "tho881")
DB_PASSWORD  = os.getenv("DB_PASSWORD", "ride1234!")
DB_NAME      = os.getenv("DB_NAME", "riding_db")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def get_db():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER,
        password=DB_PASSWORD, database=DB_NAME,
        charset="utf8mb4"
    )


def get_items_without_description(limit: int):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT ei.id, ei.name_ko, ei.name_en, ei.equipment_type, ec.name AS category_name
        FROM exercise_item ei
        LEFT JOIN exercise_category ec ON ei.category_id = ec.id
        WHERE (ei.description IS NULL OR ei.description = '')
          AND ei.is_active = 1
        ORDER BY ei.category_id, ei.id
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def generate_description(item: dict) -> str:
    """Claude API로 운동 설명 1-2줄 생성"""
    name_ko   = item["name_ko"] or ""
    name_en   = item["name_en"] or ""
    equipment = item["equipment_type"] or ""
    category  = item["category_name"] or ""

    prompt = f"""다음 운동에 대한 설명을 한국어로 1-2줄로 작성해주세요.
타겟 근육과 핵심 동작을 포함해야 합니다.
설명만 출력하고 다른 말은 하지 마세요.

운동명(한국어): {name_ko}
운동명(영어): {name_en}
부위: {category}
장비: {equipment}"""

    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}],
    }

    res = requests.post(ANTHROPIC_URL, headers=headers, json=body)
    if res.status_code == 200:
        return res.json()["content"][0]["text"].strip()
    else:
        raise Exception(f"API 오류: {res.status_code} {res.text}")


def save_description(item_id: int, description: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE exercise_item SET description = %s WHERE id = %s
    """, (description, item_id))
    conn.commit()
    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int,  default=9999, help="최대 처리 개수")
    parser.add_argument("--dry-run", action="store_true",     help="저장 없이 확인")
    parser.add_argument("--delay",   type=float, default=0.5, help="API 호출 간격(초)")
    args = parser.parse_args()

    print(f"{'='*55}")
    print(f"  운동 설명 자동 생성")
    print(f"  dry-run: {args.dry_run} | delay: {args.delay}초")
    print(f"{'='*55}\n")

    items = get_items_without_description(args.limit)
    print(f"📋 설명 없는 운동: {len(items)}개\n")

    if not items:
        print("ℹ️  모든 운동에 설명이 있습니다.")
        return

    success = 0
    failed  = 0

    for idx, item in enumerate(items, 1):
        print(f"[{idx:3d}/{len(items)}] {item['name_ko']} ({item['name_en']})")

        try:
            desc = generate_description(item)
            print(f"         → {desc}")

            if not args.dry_run:
                save_description(item["id"], desc)
                success += 1
            else:
                success += 1

        except Exception as e:
            print(f"         ❌ 실패: {e}")
            failed += 1

        if idx < len(items):
            time.sleep(args.delay)

    print(f"\n{'='*55}")
    print(f"✅ 완료 | 성공 {success} | 실패 {failed}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
