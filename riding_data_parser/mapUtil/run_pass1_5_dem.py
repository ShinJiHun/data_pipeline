from sqlalchemy.orm import Session

from riding_data_parser.model.db import SessionLocal
from riding_data_parser.model.models import ActivityPoint
from riding_data_parser.mapUtil.dem_utils import DEMReader

BATCH_SIZE = 100

def main():
    db: Session = SessionLocal()
    dem = DEMReader()

    points = (
        db.query(ActivityPoint)
        .filter(ActivityPoint.altitude_dem == None)
        .order_by(ActivityPoint.activity_core_id, ActivityPoint.seq)
        .all()
    )

    total = len(points)
    print(f"🔍 DEM 대상 point 수: {total}")

    processed = 0

    for idx, p in enumerate(points, start=1):
        ele = dem.get_elevation(p.lat, p.lon)
        if ele is not None:
            p.altitude_dem = ele

        if idx % BATCH_SIZE == 0:
            db.commit()
            processed += BATCH_SIZE
            print(f"✅ PASS1.5 진행중: {processed}/{total}")

    # 나머지 commit
    db.commit()
    dem.close()

    print(f"🎉 PASS1.5 완료: {total}/{total}")

if __name__ == "__main__":
    main()

