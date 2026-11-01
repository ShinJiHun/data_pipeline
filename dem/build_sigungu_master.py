# build_sigungu_master.py
import os
from datetime import datetime
import geopandas as gpd
from sqlalchemy import create_engine, text
from admdongkor import get as adk_get

DB_URL = os.getenv("DB_URL")  # mysql+pymysql://...riding_db
engine = create_engine(DB_URL, echo=False)

def create_table():
    ddl = """
    CREATE TABLE IF NOT EXISTS sigungu (
        id INT AUTO_INCREMENT PRIMARY KEY,
        sido_name VARCHAR(50) NOT NULL,
        sigungu_name VARCHAR(50) NOT NULL,
        sigungu_full_name VARCHAR(100) NOT NULL,   -- "경기도 진천군" 형태
        admcode VARCHAR(10) NOT NULL UNIQUE,        -- 행안부 코드
        boundary GEOMETRY REF_SYSTEM_ID=4326 NOT NULL,  -- MariaDB 문법 (SRID 강제는 안 됨, INSERT 시 4326 명시 필수)
        SPATIAL INDEX(boundary)
    ) ENGINE=InnoDB;
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))

def load_sigungu(base_date: str = "20260701"):
    # 사용 가능한 버전 키는 admdongkor.get_list()로 확인
    gdf = adk_get(base_date, "sgg", detail=True, crs=4326)  # 시군구 레벨, 원본 해상도, 위경도로 받음

    rows = []
    # detail=True면 geometry 컬럼명이 "geom"이라 활성 geometry로 접근
    for (_, row), geom in zip(gdf.iterrows(), gdf.geometry):
        code = str(row["sggcd"])
        name = row["sggnm"]
        sido = row["sidonm"]
        full_name = f"{sido} {name}"
        wkt = geom.wkt
        rows.append((sido, name, full_name, code, wkt))

    with engine.begin() as conn:
        for sido, name, full_name, code, wkt in rows:
            conn.execute(text("""
                INSERT INTO sigungu (sido_name, sigungu_name, sigungu_full_name, admcode, boundary)
                VALUES (:sido, :name, :full_name, :code, ST_GeomFromText(:wkt, 4326))
                ON DUPLICATE KEY UPDATE
                    sido_name=VALUES(sido_name),
                    sigungu_name=VALUES(sigungu_name),
                    boundary=VALUES(boundary)
            """), {"sido": sido, "name": name, "full_name": full_name, "code": code, "wkt": wkt})

    print(f"{len(rows)}개 시군구 적재 완료")

if __name__ == "__main__":
    create_table()
    load_sigungu()

