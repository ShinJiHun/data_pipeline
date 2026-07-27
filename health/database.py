import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv("/mnt/200gb/apps/.env")

DATABASE_URL = (
    f"mysql+pymysql://{os.getenv('DB_USERNAME', 'tho881')}:{os.getenv('DB_PASSWORD', 'ride1234!')}"
    f"@{os.getenv('DB_HOST', '172.17.0.1')}:3306/riding_db"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
