"""
Korea Randonneurs Brevet GPX Downloader v2
==========================================
gdown의 50개 제한을 우회 → Google Drive API로 폴더 재귀 탐색 후
GPX 파일만 개별 다운로드

필요 패키지:
    pip install requests beautifulsoup4

실행:
    python brevet_downloader.py --api-key YOUR_KEY
    python brevet_downloader.py --all --year 2026
"""

import os
import re
import json
import time
import argparse
import requests
from pathlib import Path
from bs4 import BeautifulSoup

# ─── 설정 ────────────────────────────────────────────────
DRIVE_FOLDER_ID   = "19yg3ZZRk3GfTc29Tg_9J8FJ9hP3NOqD7"
SCHEDULE_BASE_URL = "http://www.korearandonneurs.kr:8080/jsp/randonneurs/brevet_{year}"
OUTPUT_DIR        = Path("/data/home/tho881/project/NAS/brevet")
SCHEDULE_OUTPUT   = Path("/data/home/tho881/project/NAS/brevet/brevet_schedule.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

# ─── Google Drive API ────────────────────────────────────
def list_folder(folder_id: str, api_key: str = None) -> list[dict]:
    items = []
    url = "https://www.googleapis.com/drive/v3/files"
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "nextPageToken, files(id, name, mimeType)",
        "pageSize": 1000,
        "orderBy": "name",
    }
    if api_key:
        params["key"] = api_key

    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                print(f"    ⚠️  Drive API {resp.status_code}: {resp.text[:120]}")
                return []
            data = resp.json()
            items.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        except Exception as e:
            print(f"    ❌ API 오류: {e}")
            return []
    return items


def download_file(file_id: str, filename: str, dest_path: Path) -> bool:
    if dest_path.exists():
        print(f"    ⏭️  이미 존재: {filename}")
        return True

    url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
    try:
        with requests.Session() as s:
            resp = s.get(url, headers=HEADERS, stream=True, timeout=30)
            if "virus scan warning" in resp.text.lower() or resp.headers.get("Content-Type", "").startswith("text/html"):
                soup = BeautifulSoup(resp.text, "html.parser")
                token_input = soup.find("input", {"name": "confirm"})
                if token_input:
                    confirm = token_input.get("value", "t")
                    resp = s.get(
                        f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}",
                        headers=HEADERS, stream=True, timeout=30
                    )

            if resp.status_code != 200:
                print(f"    ❌ HTTP {resp.status_code}: {filename}")
                return False

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    f.write(chunk)

            size_kb = dest_path.stat().st_size / 1024
            print(f"    ✅ {filename} ({size_kb:.1f} KB)")
            return True

    except Exception as e:
        print(f"    ❌ 다운로드 실패 {filename}: {e}")
        return False


def crawl_and_download(folder_id: str, base_output: Path, api_key: str = None,
                        depth: int = 0, stats: dict = None):
    """
    폴더명 결정 규칙:
      - depth 0 (루트): 서브폴더만 재귀, GPX 있으면 경고
      - depth 1+ (코스 폴더): 첫 번째 GPX 파일명(stem)으로 base_output 바로 아래에 폴더 생성
                              GPX 없으면 하위 폴더 계속 탐색
    """
    if stats is None:
        stats = {"ok": 0, "fail": 0, "folders": 0}

    items = list_folder(folder_id, api_key)

    if not items:
        if depth == 0:
            print("❌ Drive API 응답 없음. --api-key 옵션을 사용해 보세요.")
            print("   Google Cloud Console > API 키 발급 (Drive API 활성화 필요)")
        return stats

    folders = [i for i in items if i["mimeType"] == "application/vnd.google-apps.folder"]
    gpx_files = sorted(
        [i for i in items if i["name"].lower().endswith(".gpx")],
        key=lambda f: f["name"]
    )

    stats["folders"] += len(folders)

    if depth == 0:
        # 루트: 서브폴더만 재귀
        if gpx_files:
            print(f"⚠️  루트에 GPX {len(gpx_files)}개 발견 (스킵)")
        for folder in folders:
            crawl_and_download(folder["id"], base_output, api_key, depth + 1, stats)

    else:
        if gpx_files:
            # ★ 핵심: 첫 번째 GPX stem으로 폴더명 결정, 항상 base_output 바로 아래
            folder_name = Path(gpx_files[0]["name"]).stem
            actual_dir = base_output / folder_name
            print(f"📁 {folder_name}/ → GPX {len(gpx_files)}개")
            for f in gpx_files:
                result = download_file(f["id"], f["name"], actual_dir / f["name"])
                stats["ok" if result else "fail"] += 1
                time.sleep(0.2)
            # 하위 폴더도 같은 base_output 기준으로 재귀
            for folder in folders:
                crawl_and_download(folder["id"], base_output, api_key, depth + 1, stats)
        else:
            # GPX 없는 폴더: 하위 탐색 계속
            print(f"📂 GPX 없는 폴더 탐색 중 (depth={depth})")
            for folder in folders:
                crawl_and_download(folder["id"], base_output, api_key, depth + 1, stats)

    return stats


# ─── 일정 스크래핑 ───────────────────────────────────────
def scrape_schedule(year: int) -> list[dict]:
    url = SCHEDULE_BASE_URL.format(year=year)
    print(f"📅 일정 스크래핑: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️ HTTP {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        brevets = []

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                texts = [c.get_text(strip=True) for c in cells]
                combined = " ".join(texts)

                if any(kw in combined.upper() for kw in
                       ["200K", "300K", "400K", "600K", "1000K", "BRM"]):
                    dist_match = re.search(r"\b(200|300|400|600|1000|1200)\s*[kK]", combined)
                    date_match = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", combined)
                    links = [a["href"] for a in row.find_all("a", href=True)]

                    brevets.append({
                        "year": year,
                        "date": date_match.group(0) if date_match else None,
                        "distance": int(dist_match.group(1)) if dist_match else None,
                        "raw": texts,
                        "links": links,
                    })

        print(f"  ✅ {len(brevets)}개 항목")
        return brevets

    except Exception as e:
        print(f"  ❌ 오류: {e}")
        return []


# ─── GPX 목록 출력 ───────────────────────────────────────
def summarize(base_dir: Path):
    gpx_files = sorted(base_dir.rglob("*.gpx"))
    if not gpx_files:
        print("  (GPX 파일 없음)")
        return

    by_folder: dict[str, list] = {}
    for p in gpx_files:
        folder = p.parent.name
        by_folder.setdefault(folder, []).append(p.name)

    print(f"\n{'='*55}")
    print(f"📊 다운로드 결과: 총 {len(gpx_files)}개 GPX")
    print(f"{'='*55}")
    for folder, names in sorted(by_folder.items()):
        dist_match = re.search(r"(\d{3,4})[kK]", folder)
        dist = f"{dist_match.group(1)}K" if dist_match else "  -"
        print(f"  [{dist:5s}] {folder:<30s} {len(names):2d}개")
        for n in names:
            print(f"          └─ {n}")

    data = [{"folder": p.parent.name, "file": p.name,
             "path": str(p.relative_to(base_dir)),
             "size_kb": round(p.stat().st_size / 1024, 1)}
            for p in gpx_files]
    out = base_dir / "gpx_list.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 목록 저장: {out}")


# ─── 메인 ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Korea Randonneurs Brevet GPX Downloader")
    parser.add_argument("--api-key",  type=str, default=None,         help="Google Drive API 키")
    parser.add_argument("--output",   type=str, default=str(OUTPUT_DIR), help="저장 경로")  # ★ 수정
    parser.add_argument("--year",     type=int, default=2026,         help="일정 연도")
    parser.add_argument("--schedule", action="store_true",            help="일정 스크래핑만")
    parser.add_argument("--gpx-only", action="store_true",            help="GPX 다운로드만")
    parser.add_argument("--all",      action="store_true",            help="일정 + GPX 모두")
    args = parser.parse_args()

    do_schedule = args.schedule or args.all or (not args.gpx_only)
    do_gpx      = args.gpx_only or args.all or (not args.schedule)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if do_schedule:
        print(f"\n{'='*55}")
        print(f"📅 {args.year}년 브레베 일정")
        print(f"{'='*55}")
        schedule = scrape_schedule(args.year)
        if schedule:
            with open(SCHEDULE_OUTPUT, "w", encoding="utf-8") as f:
                json.dump(schedule, f, ensure_ascii=False, indent=2)
            print(f"💾 일정 저장: {SCHEDULE_OUTPUT}")

    if do_gpx:
        print(f"\n{'='*55}")
        print(f"📥 Google Drive GPX 일괄 다운로드")
        print(f"{'='*55}")
        print(f"폴더: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}")
        print(f"저장: {output_dir}\n")

        stats = crawl_and_download(DRIVE_FOLDER_ID, output_dir, args.api_key)

        print(f"\n✅ 완료 | 성공: {stats['ok']}  실패: {stats['fail']}  폴더: {stats['folders']}")
        summarize(output_dir)


if __name__ == "__main__":
    main()
