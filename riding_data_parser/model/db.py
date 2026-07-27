# riding_data_parser/model/db.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
from pathlib import Path

# 🔥 프로젝트 루트(.env)를 자동으로 찾기
BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)

DB_URL = os.getenv("DB_URL")

if not DB_URL:
    raise ValueError("❌ DB_URL not found. Check .env path.")

print(f"🔍 DB_URL 확인: {DB_URL}", flush=True)

engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

