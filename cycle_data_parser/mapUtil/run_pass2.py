import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from pathlib import Path

from cycle_data_parser.model.models import ActivityCore
from cycle_data_parser.mapUtil.dem_pass2_distance import process_activity_distance

# ---------------------------------
# ✅ .env 명시 로드
# ---------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def main():
    print("🚀 START PASS2 (DEM 기반 업/평/다운 거리 계산)")

    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("❌ DB_URL not set")

    engine = create_engine(db_url, future=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    activities = (
        db.query(ActivityCore)
        .filter(ActivityCore.uphill_distance.is_(None))
        .all()
    )

    print(f"🔍 PASS2 대상 activity 수: {len(activities)}")

    for act in activities:
        process_activity_distance(db, act)

    db.close()
    print("🎉 PASS2 완료")


if __name__ == "__main__":
    main()

