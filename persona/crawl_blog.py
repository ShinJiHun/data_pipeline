"""
페르소나 코퍼스 파이프라인 - 블로그 크롤러

tho881.tistory.com 사이트맵을 순회하며 글 본문을 수집하고,
chunk_embed_utils를 통해 청킹 → bge-m3 임베딩 → DB 저장까지 처리한다.

실행 위치: RunPod GPU 파드 (임베딩 계산용 GPU 필요)
DB 접속: common.database가 DB_URL 환경변수를 읽는다.
         RunPod은 GCP 내부망이 아니므로 GCP 서버 공인 IP로 접속해야 한다.

사전 준비 (RunPod 파드 안에서):
    pip install requests beautifulsoup4 sentence-transformers sqlalchemy pymysql lxml

환경변수 (반드시 journal_db를 가리키도록 설정):
    export DB_URL='mysql+pymysql://tho881:ride1234!@34.172.162.148:3306/journal_db'

사용법:
    python3 crawl_blog.py
"""

import re
import time
import requests
from bs4 import BeautifulSoup

from chunk_embed_utils import ingest_document
from common.database import SessionLocal

BLOG_BASE_URL = "https://tho881.tistory.com"
SITEMAP_PATH = "/sitemap.xml"
CONTENT_SELECTORS = (
    "#article-view .tt_article_useless_p_margin,"
    ".tt_article_useless_p_margin,"
    ".area_view_content,"
    ".article_view"
)
REQUEST_DELAY_SEC = 1.0


def fetch_post_urls():
    res = requests.get(BLOG_BASE_URL + SITEMAP_PATH, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.content, "xml")

    urls = []
    for loc in soup.find_all("loc"):
        url = loc.text.strip()
        if re.search(r"tho881\.tistory\.com/\d+$", url):
            urls.append(url)

    print(f"📄 사이트맵에서 {len(urls)}개 글 URL 발견")
    return urls


def fetch_post_content(url):
    res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    title_tag = soup.find("title")
    title = title_tag.text.strip() if title_tag else url

    content_el = soup.select_one(CONTENT_SELECTORS)
    if not content_el:
        return title, None

    for tag in content_el.select("script, style"):
        tag.decompose()

    text_content = content_el.get_text(separator="\n", strip=True)
    return title, text_content


def main():
    db = SessionLocal()
    urls = fetch_post_urls()
    stats = {"new": 0, "updated": 0, "skipped": 0, "failed": 0}

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] {url}")
        try:
            title, content = fetch_post_content(url)
            ingest_document(db, "blog", url, title, content, stats)
        except Exception as e:
            db.rollback()
            print(f"  ❌ 실패: {e}")
            stats["failed"] += 1

        time.sleep(REQUEST_DELAY_SEC)

    db.close()

    print("\n" + "=" * 50)
    print("🎉 블로그 크롤링 완료")
    print(f"   신규: {stats['new']} | 업데이트: {stats['updated']} | "
          f"스킵: {stats['skipped']} | 실패: {stats['failed']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
