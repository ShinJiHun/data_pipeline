"""
🚲 bike_id가 NULL인 라이딩을 재매칭한다.

match_bike()의 후보 필터링 버그(은퇴한 자전거도 후보에 남아있던 문제)로
자동매칭에 실패해 bike_id가 NULL로 남은 라이딩들을 다시 매칭하고,
영향받은 자전거의 누적치도 재집계한다.

실행: python -m riding_data_parser.main.match_unassigned_bikes
"""

from riding_data_parser.model.db import SessionLocal
from riding_data_parser.model.models import ActivityCore, GearUsage
from riding_data_parser.services.bike_match import match_bike, recompute_bike_totals


def main():
    db = SessionLocal()
    try:
        unmatched = db.query(ActivityCore).filter(ActivityCore.bike_id.is_(None)).all()
        print(f"🎯 bike_id 없는 라이딩 {len(unmatched)}건")

        touched_bike_ids = set()
        for core in unmatched:
            gear_usages = (
                db.query(GearUsage)
                .filter(GearUsage.activity_core_id == core.id)
                .all()
            )
            bike_id = match_bike(db, core, gear_usages)
            if bike_id:
                core.bike_id = bike_id
                touched_bike_ids.add(bike_id)
                print(f"  ✅ activity_core_id={core.id} -> bike_id={bike_id}")
            else:
                print(f"  ⏭️  activity_core_id={core.id} -> 매칭 실패 (수동 확인 필요)")

        db.commit()

        for bike_id in touched_bike_ids:
            recompute_bike_totals(db, bike_id)

        print(f"✅ 완료 ({len(touched_bike_ids)}대 자전거 누적치 재집계)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
