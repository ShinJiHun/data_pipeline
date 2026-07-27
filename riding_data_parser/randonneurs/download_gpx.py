#!/usr/bin/env python3
import os, re, argparse, urllib.request, urllib.parse, io, time

CREDENTIALS = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE   = os.path.join(os.path.dirname(__file__), "token.json")
OUTPUT_DIR   = "/home/jihoon/project/NAS/brevet"
SCOPES       = ["https://www.googleapis.com/auth/drive.readonly"]
BASE         = "http://www.korearandonneurs.kr:8080"
HEADERS      = {"User-Agent": "Mozilla/5.0"}

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = Flow.from_client_secrets_file(CREDENTIALS, scopes=SCOPES, redirect_uri="http://localhost:9999")
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            print("\n아래 URL을 브라우저에서 여세요:\n")
            print(auth_url)
            full_url = input("\n로그인 후 리다이렉트된 전체 URL 붙여넣기: ").strip()
            params = urllib.parse.parse_qs(urllib.parse.urlparse(full_url).query)
            code = params.get("code", [full_url])[0]
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("  token 저장 완료")
    return build("drive", "v3", credentials=creds)


def extract_folder_id(url):
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def get_drive_url_from_brevet(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        soup = BeautifulSoup(r.read().decode("utf-8"), "html.parser")
    for a in soup.find_all("a", href=True):
        if "drive.google.com" in a["href"] and "folders" in a["href"]:
            return a["href"]
    return None


def get_all_brevet_urls(year=2026):
    url = f"{BASE}/jsp/randonneurs/brevet_{year}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        soup = BeautifulSoup(r.read().decode("utf-8"), "html.parser")
    results, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "info_BV" not in href:
            continue
        full = BASE + href if href.startswith("/") else href
        if full not in seen:
            seen.add(full)
            results.append({"name": a.get_text(strip=True), "url": full})
    return results


def list_gpx_files(service, folder_id):
    res = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType)", pageSize=100
    ).execute()
    files = res.get("files", [])
    return [f for f in files if "gpx" in f["name"].lower()]


def download_file(service, file_id, filename, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    if os.path.exists(path):
        print(f"  이미 존재 (스킵): {path}")
        return
    req = service.files().get_media(fileId=file_id)
    with io.FileIO(path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"  ✅ 저장: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="브레베 상세 페이지 URL")
    ap.add_argument("--folder-id", help="Drive 폴더 ID 직접 지정")
    ap.add_argument("--all", action="store_true", help="전체 코스 GPX 다운")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--output", default=OUTPUT_DIR)
    args = ap.parse_args()

    print("Google Drive 인증 중...")
    service = get_drive_service()
    print("인증 완료\n")

    if args.folder_id:
        targets = [{"name": "manual", "folder_id": args.folder_id}]

    elif args.all:
        print(f"▶ {args.year} 전체 브레베 목록 수집 중...")
        brevets = get_all_brevet_urls(args.year)
        print(f"  코스 {len(brevets)}개 발견")
        targets = []
        seen_folders = set()
        for b in brevets:
            try:
                drive_url = get_drive_url_from_brevet(b["url"])
                if drive_url:
                    fid = extract_folder_id(drive_url)
                    if fid and fid not in seen_folders:
                        seen_folders.add(fid)
                        targets.append({"name": b["name"], "folder_id": fid})
                        print(f"  Drive 폴더 발견: {b['name']}")
                time.sleep(0.3)
            except Exception as e:
                print(f"  스킵: {b['name']} ({e})")
        print(f"  총 {len(targets)}개 폴더\n")

    else:
        brevet_url = args.url or f"{BASE}/jsp/info_BV/info-b200j3"
        drive_url = get_drive_url_from_brevet(brevet_url)
        if not drive_url:
            print("Drive 링크 없음"); return
        name = brevet_url.split("/")[-1]
        targets = [{"name": name, "folder_id": extract_folder_id(drive_url)}]

    for t in targets:
        print(f"▶ [{t['name']}] 폴더 ID: {t['folder_id']}")
        try:
            gpx_files = list_gpx_files(service, t["folder_id"])
            if not gpx_files:
                print("  GPX 없음 (스킵)")
                continue

            # ★ 핵심 변경: 첫 번째 GPX 파일명(확장자 제거)으로 폴더명 결정
            gpx_files_sorted = sorted(gpx_files, key=lambda f: f["name"])
            folder_name = os.path.splitext(gpx_files_sorted[0]["name"])[0]
            out_dir = os.path.join(args.output, folder_name)

            print(f"  저장 폴더: {folder_name}  (GPX {len(gpx_files)}개)")
            for f in gpx_files_sorted:
                download_file(service, f["id"], f["name"], out_dir)

        except Exception as e:
            print(f"  오류: {e}")

    print("\n완료!")


if __name__ == "__main__":
    main()
