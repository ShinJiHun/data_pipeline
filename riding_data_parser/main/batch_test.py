# batch_match_segments.py
import os, sys
sys.path.insert(0, "/mnt/200gb/project/data_pipeline")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from riding_data_parser.model.models import ActivityCore
from riding_data_parser.services.segment_match_service import match_segments

db_url = os.getenv("PYTHON_DB_URL")
engine = create_engine(db_url)
SessionLocal = sessionmaker(bind=engine)

db = SessionLocal()

# 세그먼트 매칭 안 된 활동만
activities = (
    db.query(ActivityCore)
    .filter(ActivityCore.user_id == 1)
    .all()
)

print(f"총 {len(activities)}개 활동 처리 시작")

for ac in activities:
    print(f"\n[{ac.id}] {ac.start_time}")
    match_segments(db, ac.id, user_id=1)

db.close()
print("완료!")
