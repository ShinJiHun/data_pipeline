import os
import re
import requests
import urllib.parse
from bs4 import BeautifulSoup
import pymysql
from time import sleep
from dotenv import load_dotenv, find_dotenv

# ==========================================
# ✅ DB 설정
# ==========================================
load_dotenv(find_dotenv())
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "database": os.getenv("DB_NAME", "riding_db"),
    "charset": "utf8mb4",
}

# ==========================================
# ✅ API 설정
# ==========================================
KAKAO_REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/address.json"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# ==========================================
# ✅ BASE URL
# ==========================================
BASE_LIST_URL = "http://www.korearandonneurs.kr:8080/jsp/randonneurs/permanents"
BASE_DETAIL_URL = "http://www.korearandonneurs.kr:8080/jsp/permanent/info-"

SAVE_DIR = "html_logs"
os.makedirs(SAVE_DIR, exist_ok=True)

# ==========================================
# ✅ DB Connection
# ==========================================
def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

# ==========================================
# ✅ Region Map (서울, 강원 등)
# ==========================================
def load_region_map():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT province, keyword FROM tb_region_map;")
    rows = cur.fetchall()
    conn.close()
    return [(p, k) for p, k in rows]


def infer_province(start_location, region_map):
    if not start_location:
        return "기타"
    start_location = start_location.strip()
    if any(k in start_location for k in ["서울", "경기", "인천"]):
        return "서울"
    if "단월" in start_location:
        return "강원"
    for province, keyword in region_map:
        if keyword and keyword in start_location:
            return province
    return "기타"

# ==========================================
# ✅ 좌표 변환 (Kakao → 실패 시 Google fallback)
# ==========================================
def get_coordinates(keyword):
    # ✅ Kakao API 시도
    try:
        headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
        encoded_query = urllib.parse.quote(keyword, safe='')
        url = f"{KAKAO_LOCAL_URL}?query={encoded_query}"
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = "utf-8"

        if res.status_code == 200:
            docs = res.json().get("documents", [])
            if docs:
                doc = docs[0]
                return (
                    float(doc["x"]),
                    float(doc["y"]),
                    doc.get("road_address_name") or doc.get("address_name"),
                    doc.get("address_name"),
                    "kakao"
                )
    except Exception as e:
        print(f"⚠️ Kakao geocode failed ({keyword}): {e}")

    # ✅ Google fallback
    try:
        params = {"address": keyword, "key": GOOGLE_API_KEY}
        res = requests.get(GOOGLE_GEOCODE_URL, params=params, timeout=5)
        res.encoding = "utf-8"

        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                loc = results[0]["geometry"]["location"]
                road = results[0]["formatted_address"]
                return (
                    float(loc["lng"]),
                    float(loc["lat"]),
                    road,  # ✅ Google 주소를 road_addr로 사용
                    None,
                    "google"
                )
    except Exception as e:
        print(f"⚠️ Google geocode failed ({keyword}): {e}")

    return None, None, None, None, None

# ==========================================
# ✅ DB Upsert (tb_permanent)
# ==========================================
def upsert_permanent(data):
    for f in ["title_kr_en", "google_map_link", "rwgps_link", "drive_link",
              "description_kr", "description_en", "kakao_url", "kakao_item_id"]:
        data.setdefault(f, None)

    conn = get_db_connection()
    cur = conn.cursor()

    sql = """
        INSERT INTO tb_permanent (
            code, title, distance_km, elevation_m, start_location, province,
            url, kakao_url, kakao_item_id,
            title_kr_en, google_map_link, rwgps_link, drive_link,
            description_kr, description_en,
            created_at, updated_at
        )
        VALUES (
            %(code)s, %(title)s, %(distance_km)s, %(elevation_m)s, %(start_location)s, %(province)s,
            %(url)s, %(kakao_url)s, %(kakao_item_id)s,
            %(title_kr_en)s, %(google_map_link)s, %(rwgps_link)s, %(drive_link)s,
            %(description_kr)s, %(description_en)s,
            NOW(), NOW()
        )
        ON DUPLICATE KEY UPDATE
            title = VALUES(title),
            distance_km = VALUES(distance_km),
            elevation_m = VALUES(elevation_m),
            start_location = VALUES(start_location),
            province = VALUES(province),
            url = VALUES(url),
            kakao_url = VALUES(kakao_url),
            kakao_item_id = VALUES(kakao_item_id),
            title_kr_en = VALUES(title_kr_en),
            google_map_link = VALUES(google_map_link),
            rwgps_link = VALUES(rwgps_link),
            drive_link = VALUES(drive_link),
            description_kr = VALUES(description_kr),
            description_en = VALUES(description_en),
            updated_at = NOW();
    """

    try:
        cur.execute(sql, data)
        conn.commit()
        cur.execute("SELECT id FROM tb_permanent WHERE code=%s", (data["code"],))
        row = cur.fetchone()
        permanent_id = row[0] if row else None
    except Exception as e:
        print(f"❌ {data.get('code')} DB INSERT 실패: {e}")
        conn.rollback()
        permanent_id = None
    finally:
        conn.close()

    return permanent_id

# ==========================================
# ✅ 출발지 Insert or Update
# ==========================================
def insert_or_update_start_location(permanent_id, name):
    if not name:
        return
    conn = get_db_connection()
    cur = conn.cursor()

    x, y, road_addr, region_addr, provider = get_coordinates(name)
    cur.execute("SELECT id, x, y FROM tb_start_location WHERE permanent_id=%s", (permanent_id,))
    row = cur.fetchone()

    if row:
        if not row[1] or not row[2]:
            cur.execute("""
                UPDATE tb_start_location 
                SET x=%s, y=%s, road_addr=%s, region_addr=%s 
                WHERE permanent_id=%s
            """, (x, y, road_addr, region_addr, permanent_id))
            print(f"🗺 출발지 갱신 완료: {name} ({x}, {y}) [{provider}]")
    else:
        cur.execute("""
            INSERT INTO tb_start_location (permanent_id, name, road_addr, region_addr, x, y, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW());
        """, (permanent_id, name, road_addr, region_addr, x, y))
        print(f"🗺 출발지 등록 완료: {name} ({x}, {y}) [{provider}]")

    conn.commit()
    conn.close()

# ==========================================
# ✅ 상세 페이지 파싱 (Kakao URL 강화 버전)
# ==========================================
def parse_detail_page(code, start_location=None):
    code_no_dash = code.replace("-", "")
    url = f"{BASE_DETAIL_URL}{code_no_dash}.htm"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"❌ {code} 요청 실패 ({res.status_code})")
            return {}

        # HTML 저장 (디버그용)
        soup = BeautifulSoup(res.text, "html.parser")
        html_path = os.path.join(SAVE_DIR, f"{code}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(res.text)

        # 제목
        title_kr_en = soup.find("h3").get_text(strip=True) if soup.find("h3") else None

        # 링크 탐색
        google_map_link = rwgps_link = drive_link = kakao_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "google.com/maps" in href:
                google_map_link = href
            elif "ridewithgps.com/routes" in href:
                rwgps_link = href
            elif "drive.google.com" in href:
                drive_link = href
            elif any(k in href for k in ["kko.to", "map.kakao.com", "kakaomap", "kka"]):
                kakao_url = href

        # ✅ 백업: HTML 전체에서 정규식으로 kakao 링크 탐색
        if not kakao_url:
            match = re.search(r'https?://[^\s"\'<>]*kakao[^\s"\'<>]*', res.text)
            if match:
                kakao_url = match.group(0)

        # ✅ 백업: 그래도 없으면 출발지 기반 생성
        if not kakao_url and start_location:
            kakao_url = f"https://map.kakao.com/link/map/{urllib.parse.quote(start_location)}"

        # 설명문
        desc_tags = soup.select("p")
        description_kr = desc_tags[0].get_text(strip=True) if len(desc_tags) > 0 else None
        description_en = desc_tags[1].get_text(strip=True) if len(desc_tags) > 1 else None

        # 로그 출력
        print(f"🔍 [{code}] Kakao URL: {kakao_url or '❌ 없음'}")

        return {
            "title_kr_en": title_kr_en,
            "google_map_link": google_map_link,
            "rwgps_link": rwgps_link,
            "drive_link": drive_link,
            "kakao_url": kakao_url,
            "description_kr": description_kr,
            "description_en": description_en,
        }

    except Exception as e:
        print(f"❌ {code} 상세 파싱 실패: {e}")
        return {}

# ==========================================
# ✅ 목록 페이지 파싱
# ==========================================
def parse_permanent_list():
    print(f"🔗 Fetching: {BASE_LIST_URL}")
    res = requests.get(BASE_LIST_URL, timeout=10)
    res.encoding = "utf-8"
    if res.status_code != 200:
        print(f"❌ 요청 실패: {res.status_code}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    rows = soup.select("table tbody tr")
    permanents = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue
        try:
            code = cols[0].get_text(strip=True).replace(" ", "").replace("\xa0", "")
            title = cols[1].get_text(strip=True)
            distance_km = re.sub(r"[^0-9]", "", cols[2].get_text(strip=True))
            elevation_m = re.sub(r"[^0-9]", "", cols[3].get_text(strip=True))
            start_location = cols[5].get_text(strip=True)

            a_tag = cols[0].find("a")
            url = None
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                if not href.startswith("http"):
                    url = f"http://www.korearandonneurs.kr:8080/jsp/permanent/{href.split('../permanent/')[-1]}"
                else:
                    url = href

            permanents.append({
                "code": code,
                "title": title,
                "distance_km": int(distance_km) if distance_km else 0,
                "elevation_m": int(elevation_m) if elevation_m else 0,
                "start_location": start_location,
                "url": url
            })
        except Exception as e:
            print(f"⚠️ 파싱 오류: {e}")

    return permanents

# ==========================================
# ✅ 메인
# ==========================================
def main():
    print("🚴 한국 랜도너스 퍼머넌트 + 출발지 자동 등록 (Kakao→Google fallback & 좌표갱신) 시작")
    region_map = load_region_map()
    permanents = parse_permanent_list()
    print(f"📦 총 {len(permanents)}개 퍼머넌트 목록 발견")

    for i, p in enumerate(permanents):
        province = infer_province(p["start_location"], region_map)
        p["province"] = province

        detail = parse_detail_page(p["code"], p["start_location"])
        p.update(detail)

        permanent_id = upsert_permanent(p)
        if not permanent_id:
            print(f"⚠️ {p['code']} DB 조회 실패 → tb_start_location 생략")
            continue

        insert_or_update_start_location(permanent_id, p["start_location"])
        print(f"✅ {i+1:03} | {p['code']} | {p['title']} | {p['start_location']} → {p['province']}")
        sleep(0.5)

    print("\n🎉 모든 데이터 및 출발지 동기화 완료!")

if __name__ == "__main__":
    main()

