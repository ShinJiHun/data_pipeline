"""
DB 읽기 전용 도구.

1차 방어는 DB 계정 권한(SELECT 만 GRANT 된 계정, AGENT_DB_URL)이고,
여기서는 2차로 문장 종류 검사 + 세션 READ ONLY 를 건다.
"""
import re

from sqlalchemy import create_engine, text

_ALLOWED = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.IGNORECASE)


def run_readonly(url: str, sql: str, params: dict | None = None, limit: int = 500) -> list[dict]:
    body = sql.strip().rstrip(";")
    if not _ALLOWED.match(body) or ";" in body:
        raise ValueError("읽기 전용 단일 문장(SELECT/SHOW/DESCRIBE/EXPLAIN)만 허용합니다.")

    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
            rows = conn.execute(text(body), params or {}).mappings().fetchmany(limit)
            return [dict(r) for r in rows]
    finally:
        engine.dispose()


def table_summary(url: str) -> str:
    """현재 스키마의 테이블 목록과 대략적인 행 수 (information_schema 추정치)."""
    rows = run_readonly(
        url,
        "SELECT table_name AS name, table_type AS type, table_rows AS approx_rows "
        "FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name",
    )
    lines = [f"{r['name']}\t{r['type']}\t{r['approx_rows']}" for r in rows]
    return "name\ttype\tapprox_rows\n" + "\n".join(lines)
