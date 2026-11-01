from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
import os
import re
import requests
from bs4 import BeautifulSoup
import pymysql
from datetime import datetime

# ===============================================
# ✅ DB 설정
# ===============================================
DB_CONFIG = {
    "host": "localhost",
    "user": "tho881",
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset": "utf8mb4",
}

BASE_URL = "http://www.korearandonneurs.kr:8080/jsp/permanent/"
SAVE_DIR = "html_logs"
os.makedirs(SAVE_DIR, exist_ok=True)

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

# ===============================================
# ✅ 카카오 단축 URL → itemId 추출
# ===============================================
def get_kakao_redirect_info(short_url):
    try:
        res = requests.head(short_url, allow_redirects=True, timeout=10)
        redirected_url = res.url
        m = re.search(r"itemId=(\d+)", redirected_url)
        item_id = int(m.group(1)) if m else None
        return redirected_url, item_id
    except Exception as e:
        print(f"⚠️ 카카오 URL 리디렉션 실패: {e}")
        return None, None

# ===============================================
# ✅ 출발지 텍스트 파싱 (정확한 블록 기반)
# ===============================================
def parse_start_location(soup):
    start_block = None
    for h5 in soup.find_all("h5"):
        if "출발" in h5.get_text() or "Start" in h5.get_text():
            start_block = h5.find_parent("div").find_next_sibling("div")
            break

    if start_block:
        p_tag = start_block.find("p")
        if p_tag:
            text = p_tag.get_text(separator=" ", strip=True)
            return text
    return None

# ===============================================
# ✅ 퍼머넌트 상세 정보 파싱
# ===============================================
def parse_permanent_detail(code):
    code_no_dash = code.replace("-", "")
    url = f"{BASE_URL}info-{code_no_dash}.htm"

    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"❌ {code} 페이지 요청 실패 ({res.status_code})")
            return None

        html_path = os.path.join(SAVE_DIR, f"{code}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(res.text)
        print(f"🧾 HTML 저장 완료: {html_path}")

        soup = BeautifulSoup(res.text, "html.parser")

        # 제목 (한국어/영문)
        title_tag = soup.select_one(".product-description h3")
        title_kr_en = title_tag.get_text(strip=True) if title_tag else None

        # 링크 추출
        google_map_link, rwgps_link, drive_link, kakao_url = None, None, None, None
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            lower = href.lower()
            if "google.com/maps" in lower:
                google_map_link = href
            elif "ridewithgps.com" in lower:
                rwgps_link = href
            elif "drive.google.com" in lower:
                drive_link = href
            elif "kko.to" in lower:
                kakao_url = href

        # 카카오 itemId 리디렉션 추적
        kakao_redirect, kakao_item_id = (None, None)
        if kakao_url:
            kakao_redirect, kakao_item_id = get_kakao_redirect_info(kakao_url)

        # 출발지
        start_location = parse_start_location(soup)

        # 상세 설명 (한국어 / 영어)
        desc_tags = soup.select("div#tabs-1 p")
        description_kr = desc_tags[0].get_text(strip=True) if len(desc_tags) >= 1 else None
        description_en = desc_tags[1].get_text(strip=True) if len(desc_tags) >= 2 else None

        return {
            "code": code,
            "title_kr_en": title_kr_en,
            "start_location": start_location,
            "google_map_link": google_map_link,
            "rwgps_link": rwgps_link,
            "drive_link": drive_link,
            "kakao_url": kakao_redirect or kakao_url,
            "kakao_item_id": kakao_item_id,
            "description_kr": description_kr,
            "description_en": description_en,
        }

    except Exception as e:
        print(f"❌ {code} 파싱 오류: {e}")
        return None

# ===============================================
# ✅ DB 업데이트
# ===============================================
def update_permanent_detail(data):
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        sql = """
            UPDATE tb_permanent
            SET 
                title_kr_en = %s,
                start_location = %s,
                google_map_link = %s,
                rwgps_link = %s,
                drive_link = %s,
                kakao_url = %s,
                kakao_item_id = %s,
                description_kr = %s,
                description_en = %s,
                updated_at = NOW()
            WHERE code = %s;
        """

        cur.execute(sql, (
            data.get("title_kr_en"),
            data.get("start_location"),
            data.get("google_map_link"),
            data.get("rwgps_link"),
            data.get("drive_link"),
            data.get("kakao_url"),
            data.get("kakao_item_id"),
            data.get("description_kr"),
            data.get("description_en"),
            data.get("code"),
        ))
        conn.commit()

        # ✅ tb_start_location 저장 (permanent_id 찾기)
        cur.execute("SELECT id FROM tb_permanent WHERE code = %s", (data["code"],))
        result = cur.fetchone()
        if result:
            permanent_id = result[0]
            insert_sql = """
                INSERT INTO tb_start_location (permanent_id, name, road_addr, region_addr, x, y)
                VALUES (%s, %s, NULL, NULL, NULL, NULL)
                ON DUPLICATE KEY UPDATE name = VALUES(name);
            """
            cur.execute(insert_sql, (permanent_id, data.get("start_location")))

        conn.commit()
        conn.close()

        print(f"✅ {data.get('code')} | {data.get('start_location')} | Kakao: {data.get('kakao_item_id')}")

    except Exception as e:
        print(f"❌ {data.get('code')} | DB 업데이트 실패: {e}")

# ===============================================
# ✅ 메인 실행
# ===============================================
def main():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT code FROM tb_permanent ORDER BY id;")
    codes = [row[0] for row in cur.fetchall()]
    conn.close()

    print(f"🚴 총 {len(codes)}개 퍼머넌트 상세정보 파싱 시작")

    for code in codes:
        data = parse_permanent_detail(code)
        if data:
            update_permanent_detail(data)

    print("🎉 모든 상세정보 업데이트 완료!")

if __name__ == "__main__":
    main()

