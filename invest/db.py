"""
invest 모듈 공통 DB 접속.

- 접속 정보는 .env 의 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD 를 쓰고,
  DB 이름만 따로 정한다: --db 인자 > TOSS_SNAPSHOT_DB 환경변수 > 기본값 riding_db_dev
- 이름이 *_dev 가 아니면(=운영 DB) allow_prod=True 일 때만 접속한다.
  운영 반영은 관리자 확인 후 crontab 에 --allow-prod 를 붙여서 켠다.
"""

import os

import pymysql
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

DEFAULT_DB = "riding_db_dev"


def get_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"{key} 환경변수가 설정되지 않았습니다.")
    return val


def resolve_db_name(cli_db: str | None, allow_prod: bool) -> str:
    db_name = cli_db or os.environ.get("TOSS_SNAPSHOT_DB") or DEFAULT_DB
    if not db_name.endswith("_dev") and not allow_prod:
        raise RuntimeError(
            f"운영 DB({db_name})에는 --allow-prod 없이 쓰지 않습니다. 개발 DB(*_dev)로 먼저 검증하세요."
        )
    return db_name


def get_connection(db_name: str):
    return pymysql.connect(
        host=get_env("DB_HOST"),
        port=int(os.environ.get("DB_PORT", 3306)),
        user=get_env("DB_USER"),
        password=get_env("DB_PASSWORD"),
        database=db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
