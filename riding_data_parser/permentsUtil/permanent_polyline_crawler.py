import re
import json
import requests
import pymysql
import time
import random

DB_CONFIG = {
    "host": "localhost",
    "user": "tho881",
    "password": "ride1234!",
    "database": "riding_db",
    "charset": "utf8mb4",
}

# ✅ RWGPS JSON API에서 polyline 추출
def fetch_rwgps_polyline(route_url):
    match = re.search(r"/routes/(\d+)", route_url)
    if not match:
        print(f"⚠️ RWGPS ID 추출 실패: {route_url}")
        return None

    route_id = match.group(1)
    api_url = f"https://ridewithgps.com/routes/{route_id}.json"

    try:
        res = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            print(f"⚠️ 요청 실패 ({res.status_code}): {api_url}")
            return None

        data = res.json()
        if "track_points" in data:
            points = data["track_points"]
        elif "route" in data and "points" in data["route"]:
            points = data["route"]["points"]
        else:
            print(f"⚠️ JSON 구조 파악 실패: {api_url}")
            return None

        coords = [(p["y"], p["x"]) for p in points if "y" in p and "x" in p]
        print(f"✅ {route_id} | 좌표 {len(coords)}개 추출됨")
        return json.dumps(coords)  # DB에 JSON 형태로 저장
    except Exception as e:
        print(f"❌ RWGPS 요청 오류 ({route_url}): {e}")
        return None


# ✅ DB에서 RWGPS 링크 있는 퍼머넌트 조회
def get_permanents_with_rwgps():
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT id, code, rwgps_link FROM tb_permanent WHERE rwgps_link IS NOT NULL;")
    rows = cur.fetchall()
    conn.close()
    return rows


# ✅ polyline 저장
def save_polyline(permanent_id, polyline_json):
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()
    sql = """
        INSERT INTO tb_permanent_polyline (permanent_id, polyline)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE polyline = VALUES(polyline);
    """
    cur.execute(sql, (permanent_id, polyline_json))
    conn.commit()
    conn.close()


def main():
    permanents = get_permanents_with_rwgps()
    print(f"🚴 RWGPS 링크가 있는 퍼머넌트 {len(permanents)}개 확인")

    for permanent_id, code, link in permanents:
        if not link:
            continue
        polyline = fetch_rwgps_polyline(link)
        if polyline:
            save_polyline(permanent_id, polyline)
        time.sleep(random.uniform(1.0, 2.0))  # 서버 부하 방지

    print("🎉 모든 퍼머넌트 경로 저장 완료!")


if __name__ == "__main__":
    main()

