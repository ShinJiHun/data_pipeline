import requests
import pymysql
import re
import time
import random

DB_CONFIG = {
    "host": "localhost",
    "user": "tho881",
    "password": "ride1234!",
    "database": "riding_db",
    "charset": "utf8mb4",
}

# ==========================================
# ✅ 단축 URL에서 실제 좌표 추출 (kko.to → kakao.com)
# ==========================================
def expand_kakao_url(short_url):
    try:
        res = requests.get(short_url, allow_redirects=False)
        if "Location" in res.headers:
            redirect_url = res.headers["Location"]
            # 예: https://map.kakao.com/?urlX=128.39&urlY=35.98&urlLevel=3&map_type=TYPE_MAP
            m = re.search(r"urlX=([\d.]+)&urlY=([\d.]+)", redirect_url)
            if m:
                lon, lat = float(m.group(1)), float(m.group(2))
                return lon, lat
    except Exception as e:
        print(f"⚠️ Kakao URL 변환 실패 ({short_url}): {e}")
    return None, None


# ==========================================
# ✅ DB에서 아직 등록 안 된 출발지 불러오기
# ==========================================
def get_unregistered_start_locations():
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, code, start_location, kakao_url
        FROM tb_permanent
        WHERE kakao_url IS NOT NULL
          AND id NOT IN (SELECT permanent_id FROM tb_start_location)
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


# ==========================================
# ✅ tb_start_location에 저장
# ==========================================
def insert_start_location(permanent_id, name, x, y):
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    sql = """
        INSERT INTO tb_start_location (permanent_id, name, x, y)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name),
            x = VALUES(x),
            y = VALUES(y);
    """
    cur.execute(sql, (permanent_id, name, x, y))
    conn.commit()
    conn.close()


# ==========================================
# ✅ 메인 실행
# ==========================================
def main():
    rows = get_unregistered_start_locations()
    print(f"🚴 등록 안 된 출발지 {len(rows)}개 확인")

    for permanent_id, code, name, kakao_url in rows:
        if not kakao_url:
            continue

        lon, lat = expand_kakao_url(kakao_url)
        if not lon or not lat:
            print(f"❌ {code} 좌표 추출 실패 ({kakao_url})")
            continue

        insert_start_location(permanent_id, name, lon, lat)
        print(f"✅ {code} | {name} | ({lat:.6f}, {lon:.6f}) 저장 완료")

        time.sleep(random.uniform(1.0, 2.0))

    print("🎉 모든 출발지 등록 완료!")


if __name__ == "__main__":
    main()

