"""
🚴 라이딩 기록(activity_point)이 통과한 행정구역을 activity_region에 채운다.

전체 포인트마다 256개 시/군/구와 ST_Contains를 하면 느리므로:
  1. activity_point의 위경도 bbox를 구한다.
  2. bbox와 겹치는(MBRIntersects) 시/군/구만 후보로 추려낸다. (SPATIAL INDEX 활용)
  3. 후보 시/군/구에 한해서만 실제 포인트 단위 ST_Contains를 돌려서 통과 여부/포인트 수를 센다.
  4. activity_region에 upsert 한다.

사용법:
  python -m riding_data_parser.mapUtil.match_activity_regions            # 아직 매칭 안 된 라이딩 전체
  python -m riding_data_parser.mapUtil.match_activity_regions --all      # 전체 라이딩 재매칭
  python -m riding_data_parser.mapUtil.match_activity_regions --id 123   # 특정 라이딩만
"""

import argparse
from sqlalchemy import text
from riding_data_parser.model.db import engine

# 포인트가 많은 라이딩(수십만 개)까지 매번 ST_Contains 하면 느리니 N개당 1개만 샘플링한다.
SAMPLE_EVERY_N = 5
BBOX_MARGIN_DEG = 0.02  # 약 2km. 경계 근처 오차 대비 여유


def get_target_activity_ids(conn, all_activities: bool, only_id: int | None):
    if only_id is not None:
        return [only_id]
    if all_activities:
        rows = conn.execute(text("SELECT id FROM activity_core ORDER BY id")).fetchall()
    else:
        rows = conn.execute(
            text(
                """
                SELECT ac.id FROM activity_core ac
                WHERE NOT EXISTS (
                    SELECT 1 FROM activity_region ar WHERE ar.activity_core_id = ac.id
                )
                ORDER BY ac.id
                """
            )
        ).fetchall()
    return [r[0] for r in rows]


def match_one(conn, activity_id: int) -> int:
    bbox = conn.execute(
        text(
            """
            SELECT MIN(lat), MAX(lat), MIN(lon), MAX(lon)
            FROM activity_point
            WHERE activity_core_id = :aid AND lat IS NOT NULL AND lon IS NOT NULL
            """
        ),
        {"aid": activity_id},
    ).fetchone()

    if bbox is None or bbox[0] is None:
        return 0

    min_lat, max_lat, min_lon, max_lon = bbox
    min_lat -= BBOX_MARGIN_DEG
    max_lat += BBOX_MARGIN_DEG
    min_lon -= BBOX_MARGIN_DEG
    max_lon += BBOX_MARGIN_DEG
    bbox_wkt = (
        f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, "
        f"{max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
    )

    candidates = conn.execute(
        text(
            """
            SELECT id FROM region
            WHERE level = 2
              AND MBRIntersects(boundary, ST_GeomFromText(:bbox, 4326))
            """
        ),
        {"bbox": bbox_wkt},
    ).fetchall()
    candidate_ids = [r[0] for r in candidates]
    if not candidate_ids:
        return 0

    matched = conn.execute(
        text(
            """
            SELECT r.id, COUNT(*) AS point_count
            FROM activity_point ap
            JOIN region r
              ON r.id IN :region_ids
             AND ST_Contains(r.boundary, ST_GeomFromText(CONCAT('POINT(', ap.lon, ' ', ap.lat, ')'), 4326))
            WHERE ap.activity_core_id = :aid
              AND ap.lat IS NOT NULL AND ap.lon IS NOT NULL
              AND ap.seq % :sample_n = 0
            GROUP BY r.id
            """
        ).bindparams(region_ids=tuple(candidate_ids)),
        {"aid": activity_id, "sample_n": SAMPLE_EVERY_N},
    ).fetchall()

    for region_id, point_count in matched:
        conn.execute(
            text(
                """
                INSERT INTO activity_region (activity_core_id, region_id, point_count)
                VALUES (:aid, :rid, :pc)
                ON DUPLICATE KEY UPDATE point_count = VALUES(point_count)
                """
            ),
            {"aid": activity_id, "rid": region_id, "pc": point_count},
        )
    return len(matched)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="이미 매칭된 라이딩도 재매칭")
    parser.add_argument("--id", type=int, default=None, help="특정 activity_core_id만 매칭")
    args = parser.parse_args()

    with engine.begin() as conn:
        target_ids = get_target_activity_ids(conn, args.all, args.id)

    print(f"🎯 매칭 대상 라이딩 {len(target_ids)}개")

    for i, aid in enumerate(target_ids, 1):
        with engine.begin() as conn:
            n = match_one(conn, aid)
        print(f"[{i}/{len(target_ids)}] activity_core_id={aid} -> {n}개 행정구역 통과")

    print("✅ 매칭 완료")


if __name__ == "__main__":
    main()
