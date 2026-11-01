"""
🗺 대한민국 시/도, 시/군/구 경계를 region 테이블에 적재

데이터 출처: https://github.com/vuski/admdongkor (parquet/sido_*.parquet, parquet/sgg_*.parquet)
- 원본 좌표계는 EPSG:5179 (Korea 2000 / Unified CS) 이므로 GPS와 맞추기 위해 EPSG:4326으로 변환해서 저장한다.
- 시/도(level=1)를 먼저 넣고, 시/군/구(level=2)는 sidocd로 부모 region을 찾아 parent_id를 채운다.

실행 전: python -m riding_data_parser.mapUtil.setup_region_tables 로 테이블을 먼저 만들어야 한다.
실행: python -m riding_data_parser.mapUtil.load_region_boundaries
"""

import struct
import requests
import pandas as pd
from pyproj import Transformer
from sqlalchemy import text

from riding_data_parser.model.db import engine

VERSION = "20260701"  # 최신 버전이 필요하면 https://github.com/vuski/admdongkor/tree/master/parquet 에서 확인
BASE_URL = f"https://raw.githubusercontent.com/vuski/admdongkor/master/parquet"

_transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)


def download_parquet(name: str) -> pd.DataFrame:
    url = f"{BASE_URL}/{name}_{VERSION}.parquet"
    print(f"⬇️  {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    tmp_path = f"/tmp/{name}_{VERSION}.parquet"
    with open(tmp_path, "wb") as f:
        f.write(resp.content)
    return pd.read_parquet(tmp_path)


def transform_wkb_5179_to_4326(wkb: bytes) -> bytes:
    """Polygon/MultiPolygon WKB의 좌표만 EPSG:5179 -> EPSG:4326으로 in-place 변환한다."""
    buf = bytearray(wkb)
    pos = 0

    def read_u8():
        nonlocal pos
        v = buf[pos]
        pos += 1
        return v

    def read_u32():
        nonlocal pos
        v = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        return v

    def transform_point():
        nonlocal pos
        x, y = struct.unpack_from("<dd", buf, pos)
        nx, ny = _transformer.transform(x, y)
        struct.pack_into("<dd", buf, pos, nx, ny)
        pos += 16

    def walk():
        byte_order = read_u8()
        if byte_order != 1:
            raise ValueError("only little-endian WKB is supported")
        gtype = read_u32()
        if gtype == 1:  # Point
            transform_point()
        elif gtype == 2:  # LineString
            n = read_u32()
            for _ in range(n):
                transform_point()
        elif gtype == 3:  # Polygon
            nrings = read_u32()
            for _ in range(nrings):
                npoints = read_u32()
                for _ in range(npoints):
                    transform_point()
        elif gtype == 6:  # MultiPolygon
            npoly = read_u32()
            for _ in range(npoly):
                walk()
        else:
            raise ValueError(f"unsupported wkb geometry type {gtype}")

    walk()
    return bytes(buf)


def load_sido(conn) -> dict:
    """시/도 적재 후 {sidocd: region_id} 매핑을 반환한다."""
    df = download_parquet("sido")
    sido_id_map = {}
    for _, row in df.iterrows():
        wkb4326 = transform_wkb_5179_to_4326(row["geom"])
        result = conn.execute(
            text(
                """
                INSERT INTO region (level, parent_id, code, name, full_name, boundary)
                VALUES (1, NULL, :code, :name, :full_name, ST_GeomFromWKB(UNHEX(:wkb_hex), 4326))
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name), full_name = VALUES(full_name), boundary = VALUES(boundary)
                """
            ),
            {
                "code": row["sidocd"],
                "name": row["sidonm"],
                "full_name": row["sidonm"],
                "wkb_hex": wkb4326.hex(),
            },
        )
        region_id = result.lastrowid
        if not region_id:
            region_id = conn.execute(
                text("SELECT id FROM region WHERE code = :code"), {"code": row["sidocd"]}
            ).scalar()
        sido_id_map[row["sidocd"]] = region_id
    print(f"✅ 시/도 {len(df)}개 적재")
    return sido_id_map


def load_sgg(conn, sido_id_map: dict):
    df = download_parquet("sgg")
    count = 0
    for _, row in df.iterrows():
        parent_id = sido_id_map.get(row["sidocd"])
        wkb4326 = transform_wkb_5179_to_4326(row["geom"])
        conn.execute(
            text(
                """
                INSERT INTO region (level, parent_id, code, name, full_name, boundary)
                VALUES (2, :parent_id, :code, :name, :full_name, ST_GeomFromWKB(UNHEX(:wkb_hex), 4326))
                ON DUPLICATE KEY UPDATE
                    parent_id = VALUES(parent_id), name = VALUES(name),
                    full_name = VALUES(full_name), boundary = VALUES(boundary)
                """
            ),
            {
                "parent_id": parent_id,
                "code": row["sggcd"],
                "name": row["sggnm"],
                "full_name": f"{row['sidonm']} {row['sggnm']}",
                "wkb_hex": wkb4326.hex(),
            },
        )
        count += 1
    print(f"✅ 시/군/구 {count}개 적재")


def fill_centroids(conn):
    conn.execute(
        text(
            "UPDATE region SET center_lon = ST_X(ST_Centroid(boundary)), "
            "center_lat = ST_Y(ST_Centroid(boundary))"
        )
    )
    print("✅ 중심좌표 계산 완료")


def main():
    with engine.begin() as conn:
        sido_id_map = load_sido(conn)
        load_sgg(conn, sido_id_map)
        fill_centroids(conn)


if __name__ == "__main__":
    main()
