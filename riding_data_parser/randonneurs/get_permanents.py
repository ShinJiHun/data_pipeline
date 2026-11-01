#!/usr/bin/env python3
"""
update_permanents.py

코리아 랜도너스 퍼머넌트(Permanent) 코스 목록을
https://api.ebrevet.co.kr/api/courses?type=PERMANENT 에서 받아와
MariaDB(permanent_courses 테이블)에 동기화하는 배치 스크립트.

- 매월(또는 원하는 주기로) cron / GCP Cloud Scheduler 등에서 실행하도록 설계.
- 실행 시마다:
    1) API에서 최신 목록을 가져옴
    2) DB에 이미 있는 코스와 비교(diff)
    3) 신규(CREATED) / 변경(UPDATED) / 재응답에서 사라짐(DEACTIVATED) /
       다시 나타남(REACTIVATED) 을 permanent_courses_change_log 에 기록
    4) permanent_courses 테이블을 최신 상태로 upsert

사전 준비:
    - ddl_permanent_courses.sql 을 먼저 실행해 테이블 생성
    - pip install requests pymysql

환경변수:
    PERMANENTS_DB_HOST
    PERMANENTS_DB_PORT   (기본 3306)
    PERMANENTS_DB_USER
    PERMANENTS_DB_PASSWORD
    PERMANENTS_DB_NAME
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

import requests

try:
    import pymysql
    import pymysql.cursors
except ImportError:  # pragma: no cover
    print("pip install pymysql 필요", file=sys.stderr)
    raise

API_URL = "https://api.ebrevet.co.kr/api/courses?type=PERMANENT"
REQUEST_TIMEOUT_SEC = 30

# 변경 감지 시 비교할 필드 (permanent_no, is_active, first_seen_at, last_seen_at 등은 제외)
COMPARE_FIELDS = [
    "ebrevet_id",
    "name",
    "distance_km",
    "time_limit_min",
    "elevation_gain_m",
    "region",
    "is_loop",
    "note_url",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("update_permanents")


@dataclass
class Course:
    permanent_no: str
    ebrevet_id: int | None
    name: str
    distance_km: int | None
    time_limit_min: int | None
    time_limit_hm: str | None
    elevation_gain_m: int | None
    region: str | None
    is_loop: int | None
    is_active: int
    note_url: str | None


def min_to_hm(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def build_note_url(permanent_no: str) -> str:
    # PT-101 -> info-PT101.htm (하이픈 뒤 숫자를 코드에 붙임)
    code = permanent_no.replace("PT-", "PT")
    return f"http://www.korearandonneurs.kr:8080/jsp/permanent/info-{code}.htm"


def fetch_courses() -> list[Course]:
    log.info("Fetching %s", API_URL)
    resp = requests.get(API_URL, timeout=REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"API responded with ok=false: {data}")

    items = data.get("items") or []
    log.info("Fetched %d courses from API", len(items))

    courses: list[Course] = []
    for it in items:
        permanent_no = it["permanent_no"]
        tl = it.get("time_limit_min")
        courses.append(
            Course(
                permanent_no=permanent_no,
                ebrevet_id=it.get("id"),
                name=it.get("name"),
                distance_km=it.get("distance_km"),
                time_limit_min=tl,
                time_limit_hm=min_to_hm(tl),
                elevation_gain_m=it.get("elevation_gain_m"),
                region=it.get("region"),
                is_loop=it.get("is_loop"),
                is_active=1 if it.get("is_active") else 0,
                note_url=build_note_url(permanent_no),
            )
        )

    # permanent_no 중복 방지 (API가 중복을 줄 가능성에 대비한 방어 코드)
    dedup: dict[str, Course] = {}
    for c in courses:
        dedup[c.permanent_no] = c
    return list(dedup.values())


def get_db_connection():
    return pymysql.connect(
        host=os.environ["PERMANENTS_DB_HOST"],
        port=int(os.environ.get("PERMANENTS_DB_PORT", "3306")),
        user=os.environ["PERMANENTS_DB_USER"],
        password=os.environ["PERMANENTS_DB_PASSWORD"],
        database=os.environ["PERMANENTS_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def fetch_existing_rows(conn) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT permanent_no, ebrevet_id, name, distance_km, time_limit_min,
                   time_limit_hm, elevation_gain_m, region, is_loop, is_active, note_url
            FROM permanent_courses
            """
        )
        rows = cur.fetchall()
    return {row["permanent_no"]: row for row in rows}


def diff_fields(old: dict[str, Any], new: Course) -> dict[str, Any] | None:
    new_d = asdict(new)
    diff = {}
    for field in COMPARE_FIELDS:
        old_v = old.get(field)
        new_v = new_d.get(field)
        if old_v != new_v:
            diff[field] = {"old": old_v, "new": new_v}
    return diff or None


def log_change(conn, permanent_no: str, change_type: str, diff: dict[str, Any] | None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO permanent_courses_change_log (permanent_no, change_type, diff_json)
            VALUES (%s, %s, %s)
            """,
            (permanent_no, change_type, json.dumps(diff, ensure_ascii=False) if diff else None),
        )


def upsert_course(conn, c: Course, now: datetime):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO permanent_courses (
                permanent_no, ebrevet_id, name, distance_km, time_limit_min,
                time_limit_hm, elevation_gain_m, region, is_loop, is_active,
                note_url, first_seen_at, last_seen_at
            ) VALUES (
                %(permanent_no)s, %(ebrevet_id)s, %(name)s, %(distance_km)s, %(time_limit_min)s,
                %(time_limit_hm)s, %(elevation_gain_m)s, %(region)s, %(is_loop)s, %(is_active)s,
                %(note_url)s, %(now)s, %(now)s
            )
            ON DUPLICATE KEY UPDATE
                ebrevet_id       = VALUES(ebrevet_id),
                name             = VALUES(name),
                distance_km      = VALUES(distance_km),
                time_limit_min   = VALUES(time_limit_min),
                time_limit_hm    = VALUES(time_limit_hm),
                elevation_gain_m = VALUES(elevation_gain_m),
                region           = VALUES(region),
                is_loop          = VALUES(is_loop),
                is_active        = VALUES(is_active),
                note_url         = VALUES(note_url),
                last_seen_at     = VALUES(last_seen_at)
            """,
            {**asdict(c), "now": now},
        )


def mark_missing_as_inactive(conn, missing_nos: list[str], now: datetime):
    if not missing_nos:
        return
    with conn.cursor() as cur:
        format_strings = ",".join(["%s"] * len(missing_nos))
        cur.execute(
            f"""
            UPDATE permanent_courses
            SET is_active = 0, last_seen_at = last_seen_at  -- last_seen_at은 갱신하지 않음(마지막으로 보인 시점 유지)
            WHERE permanent_no IN ({format_strings}) AND is_active = 1
            """,
            missing_nos,
        )


def run():
    now = datetime.now()
    latest = fetch_courses()
    latest_by_no = {c.permanent_no: c for c in latest}

    conn = get_db_connection()
    try:
        existing = fetch_existing_rows(conn)

        created, updated, deactivated, reactivated, unchanged = 0, 0, 0, 0, 0

        # 1) 최신 목록 기준으로 upsert + 변경 감지
        for permanent_no, course in latest_by_no.items():
            old = existing.get(permanent_no)
            if old is None:
                upsert_course(conn, course, now)
                log_change(conn, permanent_no, "CREATED", None)
                created += 1
                log.info("CREATED %s (%s)", permanent_no, course.name)
                continue

            diff = diff_fields(old, course)
            was_inactive = old.get("is_active") == 0

            upsert_course(conn, course, now)

            if was_inactive:
                log_change(conn, permanent_no, "REACTIVATED", diff)
                reactivated += 1
                log.info("REACTIVATED %s (%s)", permanent_no, course.name)
            elif diff:
                log_change(conn, permanent_no, "UPDATED", diff)
                updated += 1
                log.info("UPDATED %s (%s): %s", permanent_no, course.name, diff)
            else:
                unchanged += 1

        # 2) DB엔 있었지만 이번 응답에는 없는 코스 -> 비활성 처리
        missing_nos = [
            no for no, row in existing.items()
            if no not in latest_by_no and row.get("is_active") == 1
        ]
        mark_missing_as_inactive(conn, missing_nos, now)
        for no in missing_nos:
            log_change(conn, no, "DEACTIVATED", None)
            deactivated += 1
            log.info("DEACTIVATED %s", no)

        conn.commit()
        log.info(
            "Done. created=%d updated=%d reactivated=%d deactivated=%d unchanged=%d total_fetched=%d",
            created, updated, reactivated, deactivated, unchanged, len(latest_by_no),
        )
    except Exception:
        conn.rollback()
        log.exception("Failed, rolled back")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
