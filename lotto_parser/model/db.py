import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ✅ 환경 변수 기반 DB 연결 (기본은 riding-db)
DB_USER = os.getenv("DB_USER", "tho881")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ride1234!")
DB_HOST = os.getenv("DB_HOST", "tho881_riding-db")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "lotto")

DB_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
print(f"🔍 Lotto DB_URL 확인: {DB_URL}")

engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

