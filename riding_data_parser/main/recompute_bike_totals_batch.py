"""
🚲 모든 자전거의 total_distance / total_time을 activity_core 기준으로 재계산한다.

평소엔 라이딩 1건 저장될 때마다 그 자전거만 recompute_bike_totals()로 갱신되는데,
merge_activities(활동 병합)나 bike_id 수동 변경 등으로 activity_core가 바뀌면
해당 시점에 recompute가 같이 호출되지 않아 bike.total_distance가 실제 합계와 어긋난다.
이 스크립트는 그런 드리프트를 바로잡기 위해 전체 자전거를 한 번에 재계산한다.

실행: python -m riding_data_parser.main.recompute_bike_totals_batch
"""

from sqlalchemy import text
from riding_data_parser.model.db import SessionLocal
from riding_data_parser.services.bike_match import recompute_bike_totals


def main():
    db = SessionLocal()
    try:
        bike_ids = [r[0] for r in db.execute(text("SELECT id FROM bike")).fetchall()]
        print(f"🎯 대상 자전거 {len(bike_ids)}대")

        for bike_id in bike_ids:
            before = db.execute(
                text("SELECT total_distance, total_time FROM bike WHERE id = :id"),
                {"id": bike_id},
            ).first()

            recompute_bike_totals(db, bike_id)

            after = db.execute(
                text("SELECT total_distance, total_time FROM bike WHERE id = :id"),
                {"id": bike_id},
            ).first()

            changed = "🔧 수정됨" if before != after else "변화 없음"
            print(
                f"[bike_id={bike_id}] {before[0]:.0f}m/{before[1]}s -> "
                f"{after[0]:.0f}m/{after[1]}s ({changed})"
            )

        print("✅ 재계산 완료")
    finally:
        db.close()


if __name__ == "__main__":
    main()
