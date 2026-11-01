"""
⛰ 기존 라이딩의 경사도 / 업·평·다운 거리 / 지형별 요약을 다시 계산한다.

예전 PASS1은 slope를 전부 0으로 저장했고(PASS2 DEM에서 갱신 예정이었음),
그래서 activity_point.slope = 0, activity_core.uphill/down_distance = 0 으로 남아 있다.
이 스크립트는 activity_point에 저장된 고도(FIT 기압계)와 누적거리로
  1) activity_point.slope
  2) activity_core.uphill_distance / flat_distance / down_distance
  3) activity_terrain (지형별 거리 비중 / 파워 / 심박)
  4) activity_core 난이도 (avg_uphill_grade / max_grade_1km / climb_load / difficulty_score / difficulty_level)
  5) 파워미터 없는 활동(has_power=0)의 추정 파워 — activity_point.power, activity_core.avg_power / max_power
     (예전 추정 파워는 GPS 속도 튐 때문에 수만~수십만 W가 찍혀 있었음)
를 다시 채운다.

실행:
  python -m riding_data_parser.main.backfill_terrain_batch            # 지형 요약 없는 활동만
  python -m riding_data_parser.main.backfill_terrain_batch --all      # 전체 재계산
  python -m riding_data_parser.main.backfill_terrain_batch --dry-run  # DB 수정 없이 결과만 출력
"""

import argparse

SLOPE_BATCH = 2000  # 한 번에 갱신할 포인트 수

from sqlalchemy import text
from riding_data_parser.model.db import SessionLocal
from riding_data_parser.model.models import ActivityCore, ActivityPoint
from riding_data_parser.analysis.terrain_utils import (
    compute_grades,
    calc_terrain_distances,
    build_terrain_summaries,
)
from riding_data_parser.analysis.difficulty_utils import compute_difficulty
from riding_data_parser.analysis.metrics_utils import compute_virtual_power
from riding_data_parser.services.activity_save_service import ActivitySaveService


def recompute_virtual_power(points, slopes):
    """
    포인트 거리/시간으로 속도(m/s)를 다시 구해 추정 파워 계산.
    point.speed는 FIT=km/h, GPX/TCX=m/s 로 단위가 섞여 있어 쓰지 않는다.
    """
    speeds, dt_list = [0.0], [1]
    for prev, p in zip(points, points[1:]):
        dt = max(1, (p.point_time - prev.point_time).total_seconds())
        speeds.append(max(0.0, p.distance - prev.distance) / dt)
        dt_list.append(dt)
    return compute_virtual_power(speeds, slopes, dt_list, mass=95)


def backfill_activity(db, saver, activity_id, dry_run=False):
    points = (
        db.query(ActivityPoint)
        .filter(ActivityPoint.activity_core_id == activity_id)
        .order_by(ActivityPoint.seq)
        .all()
    )
    if len(points) < 2 or any(p.distance is None for p in points):
        print(f"[{activity_id}] ⏭ 포인트/거리 없음 → skip")
        return

    # 세션에서 분리: p.slope 변경이 ORM 행 단위 UPDATE(수만 번 왕복)로 flush되지 않도록
    for p in points:
        db.expunge(p)

    distance_list = [p.distance for p in points]
    slopes = compute_grades(distance_list, [p.altitude for p in points])
    up, flat, down = calc_terrain_distances(distance_list, slopes)

    for p, s in zip(points, slopes):
        p.slope = s

    core = db.query(ActivityCore).filter(ActivityCore.id == activity_id).first()
    power_update = {}
    if not core.has_power and all(p.point_time is not None for p in points):
        powers = recompute_virtual_power(points, slopes)
        for p, w in zip(points, powers):
            p.power = w
        power_update = {"avg_power": sum(powers) / len(powers), "max_power": max(powers)}

    if dry_run:
        summaries = build_terrain_summaries(points, activity_id)
        diff = compute_difficulty(distance_list, [p.altitude for p in points], slopes)
        db.rollback()
    else:
        # (activity_core_id, seq) PK 기준 다건 upsert → 행이 이미 있으므로 slope/power만 갱신
        upsert = text(
            "INSERT INTO activity_point (activity_core_id, seq, slope, power) VALUES (:aid, :seq, :slope, :power) "
            "ON DUPLICATE KEY UPDATE slope = VALUES(slope), power = VALUES(power)"
        )
        rows = [{"aid": activity_id, "seq": p.seq, "slope": p.slope, "power": p.power} for p in points]
        for i in range(0, len(rows), SLOPE_BATCH):
            db.execute(upsert, rows[i:i + SLOPE_BATCH])
        db.query(ActivityCore).filter(ActivityCore.id == activity_id).update({
            "uphill_distance": up,
            "flat_distance": flat,
            "down_distance": down,
            **power_update,
        })
        db.commit()
        summaries = saver.save_terrain_summaries(activity_id, points, replace=True)
        core = db.query(ActivityCore).filter(ActivityCore.id == activity_id).first()
        saver.update_difficulty(core, points)
        diff = {"difficulty_score": core.difficulty_score, "difficulty_level": core.difficulty_level}

    detail = " | ".join(
        f"{t.terrain} {t.distance / 1000:.1f}km({t.distance_ratio:.0f}%) "
        f"{t.avg_power if t.avg_power is not None else '-'}W"
        for t in summaries
    )
    print(f"[{activity_id}] Lv{diff['difficulty_level']} ({diff['difficulty_score']}) | {detail}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="이미 지형 요약이 있는 활동도 다시 계산")
    parser.add_argument("--dry-run", action="store_true", help="DB 수정 없이 결과만 출력")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    saver = ActivitySaveService(db)
    try:
        sql = "SELECT id FROM activity_core"
        if not args.all:
            sql += " WHERE id NOT IN (SELECT DISTINCT activity_core_id FROM activity_terrain)"
        sql += " ORDER BY start_time DESC"
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"

        activity_ids = [r[0] for r in db.execute(text(sql)).fetchall()]
        print(f"🎯 대상 활동 {len(activity_ids)}건{' (dry-run)' if args.dry_run else ''}")

        for activity_id in activity_ids:
            try:
                backfill_activity(db, saver, activity_id, dry_run=args.dry_run)
            except Exception as e:
                db.rollback()
                print(f"[{activity_id}] ❌ 실패 → {e}")

        print("✅ 백필 완료")
    finally:
        db.close()


if __name__ == "__main__":
    main()
