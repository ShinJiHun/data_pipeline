"""
⛰ activity_terrain 테이블 생성 + activity_core 난이도 컬럼 추가

⚠️ models.py의 ActivityCore에 난이도 컬럼이 추가됐으므로,
   이 스크립트를 먼저 실행하지 않으면 새 라이딩 저장(INSERT)이 실패한다.

실행: python -m riding_data_parser.analysis.setup_terrain_table
"""

from sqlalchemy import text
from riding_data_parser.model.db import engine

DDL_ACTIVITY_TERRAIN = """
CREATE TABLE IF NOT EXISTS activity_terrain (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    activity_core_id BIGINT NOT NULL,
    terrain VARCHAR(10) NOT NULL,
    distance DOUBLE NOT NULL DEFAULT 0,
    distance_ratio DOUBLE NULL,
    moving_time_sec INT NULL,
    elevation_gain DOUBLE NULL,
    elevation_loss DOUBLE NULL,
    avg_grade DOUBLE NULL,
    avg_speed DOUBLE NULL,
    avg_power DOUBLE NULL,
    avg_power_pedaling DOUBLE NULL,
    avg_heart_rate DOUBLE NULL,
    avg_cadence DOUBLE NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_activity_terrain (activity_core_id, terrain),
    CONSTRAINT fk_at_activity FOREIGN KEY (activity_core_id) REFERENCES activity_core(id) ON DELETE CASCADE
) ENGINE=InnoDB;
"""


# MySQL은 ADD COLUMN IF NOT EXISTS 미지원 → information_schema로 확인 후 추가
ACTIVITY_CORE_COLUMNS = [
    ("avg_uphill_grade", "DOUBLE NULL"),
    ("max_grade_1km", "DOUBLE NULL"),
    ("climb_load", "DOUBLE NULL"),
    ("difficulty_score", "DOUBLE NULL"),
    ("difficulty_level", "TINYINT NULL"),
]


def main():
    with engine.begin() as conn:
        conn.execute(text(DDL_ACTIVITY_TERRAIN))
        print("✅ activity_terrain 테이블 준비 완료")

        existing = {
            r[0] for r in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_core'"
            ))
        }
        for name, ddl in ACTIVITY_CORE_COLUMNS:
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE activity_core ADD COLUMN {name} {ddl}"))
            print(f"✅ activity_core.{name} 추가")
        print("✅ activity_core 난이도 컬럼 준비 완료")


if __name__ == "__main__":
    main()
