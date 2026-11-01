"""
YouTube '좋아요 표시한 동영상' 메타데이터를 전부 가져와서 DB에 저장한다.

사전 준비:
    .env 에 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN 추가
    (get_youtube_token.py를 로컬에서 먼저 실행해서 얻은 값)

사용법:
    export PYTHON_DB_URL='mysql+pymysql://tho881:ride1234!@127.0.0.1:3306/riding_db'
    python3 fetch_liked_videos.py
"""

import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, "/mnt/200gb/project/data_pipeline")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ENV_PATH = "/mnt/200gb/project/data_pipeline/.env"
load_dotenv(ENV_PATH)

CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")


def get_access_token():
    res = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).json()
    if "access_token" not in res:
        raise Exception(f"❌ 토큰 발급 실패: {res}")
    return res["access_token"]


def get_likes_playlist_id(token):
    res = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        headers={"Authorization": f"Bearer {token}"},
        params={"part": "contentDetails", "mine": "true"},
    ).json()
    items = res.get("items", [])
    if not items:
        raise Exception(f"❌ 채널 정보 조회 실패: {res}")
    return items[0]["contentDetails"]["relatedPlaylists"]["likes"]


def fetch_all_liked_videos(token, playlist_id):
    videos = []
    page_token = None

    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token

        res = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        ).json()

        if "items" not in res:
            print(f"⚠️ 응답 이상: {res}")
            break

        for item in res["items"]:
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            videos.append({
                "video_id": content.get("videoId"),
                "title": snippet.get("title"),
                "channel_title": snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle"),
                "channel_id": snippet.get("videoOwnerChannelId"),
                "description": (snippet.get("description") or "")[:2000],
                "thumbnail_url": (snippet.get("thumbnails", {}).get("medium") or {}).get("url"),
                "liked_at": snippet.get("publishedAt"),  # 이 재생목록에 추가된 시각 = 좋아요 누른 시각
                "video_published_at": content.get("videoPublishedAt"),
            })

        print(f"  ... {len(videos)}개 수집됨")

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    return videos


def save_to_db(videos):
    db_url = os.getenv("PYTHON_DB_URL")
    if not db_url:
        raise RuntimeError("❌ PYTHON_DB_URL 환경변수가 설정되지 않았습니다.")

    engine = create_engine(db_url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()

    inserted = 0
    skipped = 0

    for v in videos:
        if not v["video_id"] or not v["title"] or v["title"] == "Private video" or v["title"] == "Deleted video":
            skipped += 1
            continue

        existing = db.execute(
            text("SELECT video_id FROM youtube_liked_video WHERE video_id = :vid"),
            {"vid": v["video_id"]},
        ).first()

        if existing:
            skipped += 1
            continue

        db.execute(
            text(
                "INSERT INTO youtube_liked_video "
                "(video_id, title, channel_title, channel_id, description, thumbnail_url, liked_at, video_published_at, created_at) "
                "VALUES (:video_id, :title, :channel_title, :channel_id, :description, :thumbnail_url, :liked_at, :video_published_at, NOW())"
            ),
            v,
        )
        inserted += 1

    db.commit()
    db.close()

    print(f"\n✅ 저장 완료: 신규 {inserted}건 / 스킵(비공개·삭제·중복) {skipped}건")


def main():
    print("🔑 access token 발급 중...")
    token = get_access_token()

    print("📂 '좋아요' 재생목록 ID 조회 중...")
    playlist_id = get_likes_playlist_id(token)
    print(f"   playlist_id = {playlist_id}")

    print("📥 좋아요 영상 전체 수집 중...")
    videos = fetch_all_liked_videos(token, playlist_id)
    print(f"   총 {len(videos)}개 수집됨")

    save_to_db(videos)


if __name__ == "__main__":
    main()
