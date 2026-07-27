import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DB_URL = os.getenv(
    "DB_URL",
    "mysql+pymysql://tho881:ride1234!@127.0.0.1:3306/riding_db",
)

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

