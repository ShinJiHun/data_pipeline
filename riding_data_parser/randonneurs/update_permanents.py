#!/usr/bin/env python3
"""
update_permanents.py

코리아 랜도너스 퍼머넌트(Permanent) 코스 목록을
https://api.ebrevet.co.kr/api/courses?type=PERMANENT 에서 받아와
riding_db(permanent_courses 테이블)에 동기화하는 배치 스크립트.

data_pipeline 프로젝트의 기존 컨벤션(.env의 DB_URL, SQLAlchemy)을 그대로 따름.

- 매월(또는 원하는 주기로) cron / GCP Cloud Scheduler 등에서 실행하도록 설계.
- 실행 시마다:
    1) API에서 최신 목록을 가져옴
    2) DB에 이미 있는 코스와 비교(diff)
    3) 신규(CREATED) / 변경(UPDATED) / 재응답에서 사라짐(DEACTIVATED) /
       다시 나타남(REACTIVATED) 을 permanent_courses_change_log 에 기록
    4) permanent_courses 테이블을 최신 상태로 upsert

사전 준비:
    - ddl_permanent_courses.sql 을 riding_db에 먼저 실행해 테이블 생성
      예) mysql -u $DB_USER -h $DB_HOST -P $DB_PORT $DB_NAME < ddl_permanent_courses.sql
    - pip install requests sqlalchemy pymysql python-dotenv
    - data_pipeline/.env 에 DB_URL 이 설정되어 있어야 함
      (예: DB_URL=mysql+pymysql://tho881:@127.0.0.1:3306/riding_db)
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
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

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

# data_pipeline/.env 를 상위 디렉토리로 거슬러 올라가며 자동으로 찾아서 로드
# (randonneurs/ -> riding_data_parser/ -> data_pipeline/.env)
load_dotenv(find_dotenv())


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


def get_engine() -> Engine:
    db_url = os.environ.get("DB_URL")
    if not db_url:
        raise RuntimeError(
            "DB_URL 환경변수가 없습니다. data_pipeline/.env 에 "
            "DB_URL=mysql+pymysql://user:pass@host:port/dbname 형태로 설정하세요."
        )
    return create_engine(db_url, pool_pre_ping=True)


def fetch_existing_rows(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT permanent_no, ebrevet_id, name, distance_km, time_limit_min,
                   time_limit_hm, elevation_gain_m, region, is_loop, is_active, note_url
            FROM permanent_courses
            """
        )
    ).mappings().all()
    return {row["permanent_no"]: dict(row) for row in rows}


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
    conn.execute(
        text(
            """
            INSERT INTO permanent_courses_change_log (permanent_no, change_type, diff_json)
            VALUES (:permanent_no, :change_type, :diff_json)
            """
        ),
        {
            "permanent_no": permanent_no,
            "change_type": change_type,
            "diff_json": json.dumps(diff, ensure_ascii=False) if diff else None,
        },
    )


def upsert_course(conn, c: Course, now: datetime):
    conn.execute(
        text(
            """
            INSERT INTO permanent_courses (
                permanent_no, ebrevet_id, name, distance_km, time_limit_min,
                time_limit_hm, elevation_gain_m, region, is_loop, is_active,
                note_url, first_seen_at, last_seen_at
            ) VALUES (
                :permanent_no, :ebrevet_id, :name, :distance_km, :time_limit_min,
                :time_limit_hm, :elevation_gain_m, :region, :is_loop, :is_active,
                :note_url, :now, :now
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
            """
        ),
        {**asdict(c), "now": now},
    )


def run():
    now = datetime.now()
    latest = fetch_courses()
    latest_by_no = {c.permanent_no: c for c in latest}

    engine = get_engine()
    with engine.begin() as conn:  # 성공 시 commit, 예외 시 자동 rollback
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
        if missing_nos:
            placeholders = ", ".join(f":no{i}" for i in range(len(missing_nos)))
            conn.execute(
                text(
                    f"UPDATE permanent_courses SET is_active = 0 "
                    f"WHERE permanent_no IN ({placeholders}) AND is_active = 1"
                ),
                {f"no{i}": no for i, no in enumerate(missing_nos)},
            )
        for no in missing_nos:
            log_change(conn, no, "DEACTIVATED", None)
            deactivated += 1
            log.info("DEACTIVATED %s", no)

    log.info(
        "Done. created=%d updated=%d reactivated=%d deactivated=%d unchanged=%d total_fetched=%d",
        created, updated, reactivated, deactivated, unchanged, len(latest_by_no),
    )


if __name__ == "__main__":
    run()
