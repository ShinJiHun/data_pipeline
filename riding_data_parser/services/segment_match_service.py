import math
from datetime import datetime
from sqlalchemy.orm import Session
from riding_data_parser.model.models import ActivityPoint, SegmentEffort, Segment


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def match_segments(db: Session, activity_core_id: int, user_id: int = 1):
    points = (
        db.query(ActivityPoint)
        .filter(ActivityPoint.activity_core_id == activity_core_id)
        .order_by(ActivityPoint.seq)
        .all()
    )

    if not points:
        print(f"⚠️ 포인트 없음: {activity_core_id}")
        return 0

    lats = [p.lat for p in points if p.lat]
    lons = [p.lon for p in points if p.lon]
    if not lats:
        return 0

    act_min_lat, act_max_lat = min(lats), max(lats)
    act_min_lon, act_max_lon = min(lons), max(lons)
    margin = 0.1

    candidates = (
        db.query(Segment)
        .filter(
            Segment.is_active == 1,
            Segment.start_lat >= act_min_lat - margin,
            Segment.start_lat <= act_max_lat + margin,
            Segment.start_lon >= act_min_lon - margin,
            Segment.start_lon <= act_max_lon + margin,
        )
        .all()
    )

    print(f"🔍 후보 세그먼트: {len(candidates)}개")

    matched = 0
    for seg in candidates:
        result = _try_match(points, seg)
        if result is None:
            continue

        start_idx, end_idx = result

        existing = (
            db.query(SegmentEffort)
            .filter(
                SegmentEffort.activity_core_id == activity_core_id,
                SegmentEffort.segment_id == seg.id,
            )
            .first()
        )
        if existing:
            continue

        seg_points = points[start_idx:end_idx+1]
        start_time = seg_points[0].point_time
        end_time = seg_points[-1].point_time
        elapsed = int((end_time - start_time).total_seconds()) if start_time and end_time else None

        speeds = [p.speed for p in seg_points if p.speed]
        hrs = [p.heart_rate for p in seg_points if p.heart_rate]
        cads = [p.cadence for p in seg_points if p.cadence]
        powers = [p.power for p in seg_points if p.power]
        distances = [p.distance for p in seg_points if p.distance]

        effort = SegmentEffort(
            segment_id=seg.id,
            activity_core_id=activity_core_id,
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            elapsed_time_sec=elapsed,
            moving_time_sec=elapsed,
            avg_speed=sum(speeds)/len(speeds) if speeds else None,
            max_speed=max(speeds) if speeds else None,
            avg_heart_rate=int(sum(hrs)/len(hrs)) if hrs else None,
            max_heart_rate=int(max(hrs)) if hrs else None,
            avg_cadence=int(sum(cads)/len(cads)) if cads else None,
            avg_power=sum(powers)/len(powers) if powers else None,
            max_power=max(powers) if powers else None,
            start_distance_m=distances[0] if distances else None,
            end_distance_m=distances[-1] if distances else None,
            start_point_seq=seg_points[0].seq,
            end_point_seq=seg_points[-1].seq,
        )
        db.add(effort)
        matched += 1
        print(f"  ✅ 매칭: {seg.name} ({elapsed}초)")

    db.commit()
    print(f"🎉 총 {matched}개 세그먼트 매칭 완료")
    return matched


def _try_match(points, seg):
    start_r = seg.start_radius_m or 30
    end_r = seg.end_radius_m or 30

    start_idx = None
    for i, p in enumerate(points):
        if not p.lat or not p.lon:
            continue
        if start_idx is None:
            if haversine(p.lat, p.lon, seg.start_lat, seg.start_lon) <= start_r:
                start_idx = i
        else:
            if haversine(p.lat, p.lon, seg.end_lat, seg.end_lon) <= end_r:
                return (start_idx, i)
    return None
