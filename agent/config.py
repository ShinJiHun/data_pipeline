"""
에이전트 공통 설정.

.env(기본: 저장소 루트 .env, AGENT_ENV_FILE 로 변경 가능)에서 읽는 값:
    OPENAI_API_KEY    langchain-openai 가 직접 읽음 (필수)
    AGENT_MODEL       사용할 OpenAI 모델 이름 (필수)
    AGENT_DB_URL      SELECT 전용 계정 DB URL (선택, 없으면 DB 요약 생략)
    AGENT_OUTPUT_DIR  제안 결과 저장 위치 (기본 /mnt/200gb/logs/agent)
"""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(os.getenv("AGENT_ENV_FILE") or REPO_ROOT / ".env")


def require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"{key} 환경변수가 없습니다. agent/README.md 참고.")
    return val


def model_name() -> str:
    return require_env("AGENT_MODEL")


def db_url() -> str | None:
    return os.getenv("AGENT_DB_URL") or None


def output_dir() -> Path:
    return Path(os.getenv("AGENT_OUTPUT_DIR", "/mnt/200gb/logs/agent"))
