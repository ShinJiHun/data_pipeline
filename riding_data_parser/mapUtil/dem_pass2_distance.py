from sqlalchemy.orm import Session

from riding_data_parser.model.models import ActivityCore, ActivityPoint
from riding_data_parser.mapUtil.dem_utils import DEMReader, haversine

# -------------------------
# slope 기준값 (%)
# -------------------------
UP_SLOPE = 1.0
DOWN_SLOPE = -1.0


def process_activity_distance(db: Session, activity: ActivityCore):
    """
    PASS2
    - ActivityPoint 기반
    - DEM 고도 조회
    - slope 계산
    - uphill / flat / downhill 거리 누적
    """

    points = (
        db.query(ActivityPoint)
        .filter(ActivityPoint.activity_core_id == activity.id)
        .order_by(ActivityPoint.seq)
        .all()
    )

    if len(points) < 2:
        return

    dem = DEMReader()

    uphill = 0.0
    flat = 0.0
    downhill = 0.0

    prev_ele = dem.get_elevation(points[0].lat, points[0].lon)

    for i in range(1, len(points)):
        p1 = points[i - 1]
        p2 = points[i]

        dist = haversine(p1.lat, p1.lon, p2.lat, p2.lon)
        if dist <= 0:
            continue

        cur_ele = dem.get_elevation(p2.lat, p2.lon)
        if prev_ele is None or cur_ele is None:
            prev_ele = cur_ele
            continue

        slope = ((cur_ele - prev_ele) / dist) * 100.0

        if slope >= UP_SLOPE:
            uphill += dist
        elif slope <= DOWN_SLOPE:
            downhill += dist
        else:
            flat += dist

        prev_ele = cur_ele

    dem.close()

    # -------------------------
    # ActivityCore 업데이트
    # -------------------------
    activity.uphill_distance = uphill
    activity.flat_distance = flat
    activity.down_distance = downhill

    db.commit()

    print(
        f"✅ PASS2 id={activity.id} | "
        f"UP {uphill:.1f}m | FLAT {flat:.1f}m | DOWN {downhill:.1f}m"
    )

