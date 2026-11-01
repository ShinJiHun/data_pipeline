#!/usr/bin/env python3
"""
퍼머넌트(Permanent) 코스별 GPX 다운로더

구조 (실제 Drive에서 확인함):
  "Korea Randonneurs" 루트 폴더 (0BwrBWXplGo9xcjIxSE5Zb0lLLTA)
    └ perms 0-50 / perms 51-100 / perms 101-150 / perms 151-199 / perms 200-plus
        └ "PT-01 documents", "PT-02 documents", ... (코스별 폴더)
            └ PT-01-2025.gpx, map PT-01.png, 사고포기 및 책임면제.docx, permanent sheet.png 등

기존 get_brevet.py 의 Google Drive OAuth 인증 방식(credentials.json/token.json)을
그대로 재사용함. 같은 randonneurs/ 폴더에 두고 실행하면 됨.

Usage:
  python3 download_permanent_gpx.py --all                # 전체 코스 GPX 다운로드
  python3 download_permanent_gpx.py --no PT-01 PT-02      # 특정 코스만
  python3 download_permanent_gpx.py --all --output /path  # 저장 경로 지정
"""

import os
import re
import io
import argparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

CREDENTIALS = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

ROOT_FOLDER_ID = "0BwrBWXplGo9xcjIxSE5Zb0lLLTA"  # "Korea Randonneurs" 공유 드라이브 루트
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "permanents_gpx")

FOLDER_MIME = "application/vnd.google-apps.folder"


def get_drive_service():
    """get_brevet.py 와 동일한 OAuth 플로우. 같은 token.json 재사용 가능."""
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
            import urllib.parse
            params = urllib.parse.parse_qs(urllib.parse.urlparse(full_url).query)
            code = params.get("code", [full_url])[0]
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("  token 저장 완료")
    return build("drive", "v3", credentials=creds)


def list_children(service, folder_id: str) -> list[dict]:
    items = []
    page_token = None
    while True:
        res = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


def find_permanent_folders(service, root_folder_id: str) -> dict[str, dict]:
    """
    루트 -> "perms *" 폴더들 -> "PT-xx documents" 폴더들을 순회하며
    { permanent_no: {"id": folder_id, "folder_name": "PT-01 documents"} } 매핑을 만든다.
    """
    result = {}
    top_level = list_children(service, root_folder_id)
    perms_folders = [
        f for f in top_level
        if f["mimeType"] == FOLDER_MIME and re.match(r"^perms\b", f["name"], re.I)
    ]
    print(f"'perms *' 폴더 {len(perms_folders)}개 발견: {[f['name'] for f in perms_folders]}")

    for pf in perms_folders:
        children = list_children(service, pf["id"])
        course_folders = [f for f in children if f["mimeType"] == FOLDER_MIME]
        for cf in course_folders:
            name = cf["name"]
            m = re.match(r"^(PT-[0-9A-Za-z-]+)\s*(.*)$", name, re.I)
            if not m:
                print(f"  ⚠️  이름 패턴 안 맞음, 스킵: {name}")
                continue

            permanent_no = m.group(1).upper()
            rest = m.group(2).strip().lower()

            if re.match(r"^no\s+documents?$", rest):
                # 예: "PT-00 no documents" -> 아직 GPX/문서가 없는 코스
                result[permanent_no] = {"id": cf["id"], "folder_name": name, "empty": True}
                print(f"  ℹ️  {permanent_no}: 아직 문서/GPX 없음 ({name})")
                continue

            if re.search(r"\balt\b", rest):
                # 예: "PT-58 ALT documents" -> 대체 경로. 본 코스(PT-58)와 안 섞이게
                # "PT-58-ALT" 라는 별도 키로 저장해서 --no PT-58-ALT 로만 받을 수 있게 함
                alt_no = f"{permanent_no}-ALT"
                result[alt_no] = {"id": cf["id"], "folder_name": name, "empty": False}
                print(f"  ℹ️  {permanent_no}의 대체 경로 폴더 발견 -> {alt_no} 로 별도 등록: {name}")
                continue

            # 그 외 (예: "PT-98 documens" 같은 오타)는 "documents" 계열로 간주하고 그냥 채택
            result[permanent_no] = {"id": cf["id"], "folder_name": name, "empty": False}

    return result


def download_file(service, file_id: str, filename: str, dest_dir: str) -> bool:
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)
    if os.path.exists(path):
        print(f"    ⏭️  이미 존재: {path}")
        return True
    try:
        req = service.files().get_media(fileId=file_id)
        with io.FileIO(path, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
        print(f"    ✅ {path}")
        return True
    except Exception as e:
        print(f"    ❌ {filename}: {e}")
        return False


def download_gpx_for(service, permanent_no: str, folder_id: str, output_dir: str) -> int:
    files = list_children(service, folder_id)
    gpx_files = [f for f in files if f["name"].lower().endswith(".gpx")]
    if not gpx_files:
        print(f"  {permanent_no}: GPX 없음")
        return 0
    dest_dir = os.path.join(output_dir, permanent_no)
    ok = 0
    for f in gpx_files:
        if download_file(service, f["id"], f["name"], dest_dir):
            ok += 1
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="전체 코스 GPX 다운로드")
    ap.add_argument("--no", nargs="+", help="특정 permanent_no만 (예: --no PT-01 PT-02)")
    ap.add_argument("--output", default=OUTPUT_DIR, help="저장 경로")
    ap.add_argument("--list-only", action="store_true", help="다운로드 없이 매핑만 출력")
    args = ap.parse_args()

    if not (args.all or args.no or args.list_only):
        ap.error("--all 또는 --no PT-xx ... 또는 --list-only 중 하나는 필요합니다")

    print("Google Drive 인증 중...")
    service = get_drive_service()
    print("인증 완료\n")

    print("▶ 코스별 Drive 폴더 탐색 중...")
    mapping = find_permanent_folders(service, ROOT_FOLDER_ID)
    print(f"  총 {len(mapping)}개 코스 폴더 발견\n")

    if args.list_only:
        for no in sorted(mapping):
            print(f"  {no}: {mapping[no]['folder_name']} (folder_id={mapping[no]['id']})")
        return

    targets = sorted(mapping) if args.all else [n.upper() for n in args.no]

    total_ok, total_missing = 0, 0
    for no in targets:
        info = mapping.get(no)
        if not info:
            print(f"❌ {no}: Drive에서 폴더를 찾을 수 없음 (permanent_courses 코드와 폴더명이 다를 수 있음)")
            total_missing += 1
            continue
        if info.get("empty"):
            print(f"⏭️  {no}: 아직 문서/GPX가 없는 코스라 스킵")
            continue
        print(f"▶ {no} ({info['folder_name']})")
        total_ok += download_gpx_for(service, no, info["id"], args.output)

    print(f"\n완료! GPX 다운로드 {total_ok}건, 매칭 실패 {total_missing}건")
    print(f"저장 위치: {args.output}")


if __name__ == "__main__":
    main()
