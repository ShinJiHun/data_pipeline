from sqlalchemy.exc import SQLAlchemyError
from riding_data_parser.model.models import ActivityCore, ActivityPoint
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
    # Core + Points 저장 (PASS1)
    # -------------------------------------------------
    def save_with_points(self, core: ActivityCore, points: list[ActivityPoint]):
        try:
            saved_core = self.save_core(core)
    
            # 🚲 자전거 자동매칭 (아직 안 정해진 경우만)
            if saved_core.bike_id is None:
                saved_core.bike_id = match_bike(self.db, saved_core)
    
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
