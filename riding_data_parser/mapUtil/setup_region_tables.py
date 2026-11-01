"""
🗺 region / activity_region 테이블 생성

GEOMETRY + SPATIAL INDEX는 SQLAlchemy Column 타입으로 표현할 수 없어서
models.py의 Base.metadata.create_all() 대신 raw DDL로 생성한다.
(models.py의 Region/ActivityRegion 클래스는 boundary 컬럼을 매핑하지 않음)

실행: python -m riding_data_parser.mapUtil.setup_region_tables
"""

from sqlalchemy import text
from riding_data_parser.model.db import engine

DDL_REGION = """
CREATE TABLE IF NOT EXISTS region (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    level TINYINT NOT NULL,
    parent_id BIGINT NULL,
    code VARCHAR(10) NOT NULL,
    name VARCHAR(50) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    center_lat DOUBLE NULL,
    center_lon DOUBLE NULL,
    boundary GEOMETRY NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_region_code (code),
    KEY idx_region_parent (parent_id),
    SPATIAL INDEX idx_region_boundary (boundary),
    CONSTRAINT fk_region_parent FOREIGN KEY (parent_id) REFERENCES region(id)
) ENGINE=InnoDB;
"""

DDL_ACTIVITY_REGION = """
CREATE TABLE IF NOT EXISTS activity_region (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    activity_core_id BIGINT NOT NULL,
    region_id BIGINT NOT NULL,
    point_count INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_activity_region (activity_core_id, region_id),
    KEY idx_activity_region_region (region_id),
    CONSTRAINT fk_ar_activity FOREIGN KEY (activity_core_id) REFERENCES activity_core(id) ON DELETE CASCADE,
    CONSTRAINT fk_ar_region FOREIGN KEY (region_id) REFERENCES region(id)
) ENGINE=InnoDB;
"""


def main():
    with engine.begin() as conn:
        conn.execute(text(DDL_REGION))
        print("✅ region 테이블 준비 완료")
        conn.execute(text(DDL_ACTIVITY_REGION))
        print("✅ activity_region 테이블 준비 완료")


if __name__ == "__main__":
    main()
