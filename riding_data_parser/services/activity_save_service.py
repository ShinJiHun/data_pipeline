from sqlalchemy.exc import SQLAlchemyError
from riding_data_parser.model.models import ActivityCore, ActivityPoint, GearUsage, ActivityTerrain
from riding_data_parser.analysis.terrain_utils import build_terrain_summaries
from riding_data_parser.analysis.difficulty_utils import compute_difficulty
from riding_data_parser.services.bike_match import match_bike, recompute_bike_totals


class ActivitySaveService:
    def __init__(self, db):
        # 🔑 이게 없어서 터졌음
        self.db = db

    # -------------------------------------------------
    # Core 단독 저장 (내부용)
    # -------------------------------------------------
    def save_core(self, core: ActivityCore) -> ActivityCore:
        """
        filename 기준 중복 방지
        """
        existing = (
            self.db.query(ActivityCore)
            .filter(ActivityCore.filename == core.filename)
            .first()
        )

        if existing:
            print(f"⚠️ 이미 존재 → skip core: {core.filename}")
            return existing

        self.db.add(core)
        self.db.flush()   # id 확보
        return core

    # -------------------------------------------------
    # Core + Points + GearUsage 저장 (PASS1)
    # -------------------------------------------------
    def save_with_points(
        self,
        core: ActivityCore,
        points: list[ActivityPoint],
        gear_usages: list[GearUsage] = None,
    ):
        gear_usages = gear_usages or []

        try:
            saved_core = self.save_core(core)

            # 🚲 자전거 자동매칭 (아직 안 정해진 경우만)
            if saved_core.bike_id is None:
                saved_core.bike_id = match_bike(self.db, saved_core, gear_usages)

            # 이미 포인트가 있으면 스킵 (재실행 안전)
            existing_point = (
                self.db.query(ActivityPoint)
                .filter(ActivityPoint.activity_core_id == saved_core.id)
                .first()
            )
            if existing_point:
                print(f"⚠️ 이미 point 존재 → skip points: {core.filename}")
            else:
                for p in points:
                    p.activity_core_id = saved_core.id
                    self.db.add(p)

            # ⚙️ GearUsage 저장 (재실행 안전)
            existing_gear = (
                self.db.query(GearUsage)
                .filter(GearUsage.activity_core_id == saved_core.id)
                .first()
            )
            if existing_gear:
                print(f"⚠️ 이미 gear_usage 존재 → skip: {core.filename}")
            else:
                for g in gear_usages:
                    g.activity_core_id = saved_core.id
                    self.db.add(g)

            self.db.commit()

        except SQLAlchemyError as e:
            self.db.rollback()
            raise e

        # 📊 저장 성공 후 해당 자전거만 누적 재집계 (best-effort)
        try:
            recompute_bike_totals(self.db, saved_core.bike_id)
        except SQLAlchemyError as e:
            self.db.rollback()
            print(f"⚠️ 누적 재집계 실패 (라이딩은 저장됨): {core.filename} → {e}")

        # ⛰ 지형별 요약 (best-effort: 테이블 미생성 등으로 실패해도 라이딩은 저장됨)
        try:
            self.save_terrain_summaries(saved_core.id, points)
        except SQLAlchemyError as e:
            self.db.rollback()
            print(f"⚠️ 지형 요약 저장 실패 (라이딩은 저장됨): {core.filename} → {e}")

    # -------------------------------------------------
    # 난이도 재계산 (병합/백필용)
    # -------------------------------------------------
    def update_difficulty(self, core: ActivityCore, points: list[ActivityPoint]):
        """
        병합된 포인트는 활동마다 distance가 0부터 다시 시작하므로
        구간 증가분만 누적해 단조 증가 거리로 만든 뒤 계산한다.
        """
        distance_list, cum = [], 0.0
        for prev, p in zip([None] + points, points):
            if prev is not None and p.distance is not None and prev.distance is not None:
                cum += max(0.0, p.distance - prev.distance)
            distance_list.append(cum)

        result = compute_difficulty(distance_list, [p.altitude for p in points], [p.slope for p in points])
        if result is None:
            return
        for key in ("avg_uphill_grade", "max_grade_1km", "climb_load", "difficulty_score", "difficulty_level"):
            setattr(core, key, result[key])
        self.db.commit()

    # -------------------------------------------------
    # 지형별 요약 (업힐/평지/다운힐) 저장
    # -------------------------------------------------
    def save_terrain_summaries(self, activity_core_id: int, points: list[ActivityPoint], replace: bool = False):
        """
        points의 slope 기준으로 지형별 요약을 만들어 저장.
        slope가 전부 0이면(GPX/TCX 등 경사 미계산) 의미 없는 '전부 평지'가 되므로 저장하지 않는다.
        replace=True면 기존 요약을 지우고 다시 만든다 (병합/백필용).
        """
        if not any(p.slope for p in points):
            return []

        existing = self.db.query(ActivityTerrain).filter(ActivityTerrain.activity_core_id == activity_core_id)
        if existing.first():
            if not replace:
                return []
            existing.delete(synchronize_session=False)

        summaries = build_terrain_summaries(points, activity_core_id)
        self.db.add_all(summaries)
        self.db.commit()
        return summaries



    def merge_activities(self, parent_id: int, target_id: int):
        """
        Target 활동의 데이터를 Parent 활동으로 물리적으로 병합합니다.
        """
        try:
            # 1. 두 활동 조회
            parent = self.db.query(ActivityCore).filter(ActivityCore.id == parent_id).first()
            target = self.db.query(ActivityCore).filter(ActivityCore.id == target_id).first()

            if not parent or not target:
                raise ValueError("활동을 찾을 수 없습니다.")

            # 2. 포인트 seq 재조정 및 이동
            # 부모의 마지막 seq 확인
            max_seq = self.db.query(self.db.func.max(ActivityPoint.seq))\
                .filter(ActivityPoint.activity_core_id == parent_id).scalar() or 0

            target_points = self.db.query(ActivityPoint)\
                .filter(ActivityPoint.activity_core_id == target_id).all()

            for pt in target_points:
                pt.activity_core_id = parent_id
                pt.seq += (max_seq + 1)

            # 3. GearUsage 이동 (중복 방지 합산 로직)
            target_gears = self.db.query(GearUsage).filter(GearUsage.activity_core_id == target_id).all()
            for tg in target_gears:
                existing = self.db.query(GearUsage).filter(
                    GearUsage.activity_core_id == parent_id,
                    GearUsage.front_gear == tg.front_gear,
                    GearUsage.rear_gear == tg.rear_gear,
                    GearUsage.terrain == tg.terrain
                ).first()

                if existing:
                    existing.duration_sec += tg.duration_sec
                    existing.distance += tg.distance
                    self.db.delete(tg)
                else:
                    tg.activity_core_id = parent_id

            # 4. 통계치 합산 업데이트
            parent.end_time = target.end_time
            parent.total_distance += (target.total_distance or 0)
            parent.moving_time += (target.moving_time or 0)
            parent.elapsed_time += (target.elapsed_time or 0)
            parent.total_ascent += (target.total_ascent or 0)
            parent.total_descent += (target.total_descent or 0)
            parent.calories += (target.calories or 0)
            parent.max_speed = max(parent.max_speed or 0, target.max_speed or 0)
            parent.max_power = max(parent.max_power or 0, target.max_power or 0)

            # 5. 서브 활동 삭제 (on delete cascade로 포인트 자동 삭제됨)
            target_bike_id = target.bike_id
            self.db.delete(target)

            self.db.commit()

            # 📊 병합으로 activity_core 합계가 바뀌었으니 관련 자전거 누적치도 재집계 (best-effort)
            for bike_id in {parent.bike_id, target_bike_id}:
                try:
                    recompute_bike_totals(self.db, bike_id)
                except SQLAlchemyError as e:
                    self.db.rollback()
                    print(f"⚠️ 병합 후 자전거 누적 재집계 실패: bike_id={bike_id} → {e}")

            # ⛰ 포인트가 합쳐졌으니 지형 요약도 다시 계산 (best-effort)
            try:
                merged_points = (
                    self.db.query(ActivityPoint)
                    .filter(ActivityPoint.activity_core_id == parent_id)
                    .order_by(ActivityPoint.seq)
                    .all()
                )
                self.save_terrain_summaries(parent_id, merged_points, replace=True)
                self.update_difficulty(parent, merged_points)
            except SQLAlchemyError as e:
                self.db.rollback()
                print(f"⚠️ 병합 후 지형 요약 재계산 실패: activity_id={parent_id} → {e}")

            return parent_id

        except SQLAlchemyError as e:
            self.db.rollback()
            raise e
