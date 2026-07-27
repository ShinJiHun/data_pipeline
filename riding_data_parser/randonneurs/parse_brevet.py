#!/usr/bin/env python3
"""
Korea Randonneurs 브레베 파서
- brevet_2026 목록 페이지에서 날짜 + 코스 URL 자동 수집
- 각 코스 상세 페이지에서 거리/획고/시간제한 파싱

Usage:
  python3 parse_brevet.py              # 2026 전체 목록 파싱
  python3 parse_brevet.py --json       # JSON 출력
  python3 parse_brevet.py --year 2025  # 연도 지정
  python3 parse_brevet.py --no-detail  # 목록만 (빠름)
"""

import urllib.request
import re
import sys
import json
import argparse
import time

try:
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "beautifulsoup4",
                    "--break-system-packages", "-q"])
    from bs4 import BeautifulSoup

BASE = "http://www.korearandonneurs.kr:8080"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; brevet-parser/1.0)"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8")


# ── 목록 페이지: 날짜 + 코스 링크 수집 ──────────────────────────
def parse_schedule(year: int = 2026) -> list:
    url = f"{BASE}/jsp/randonneurs/brevet_{year}"
    soup = BeautifulSoup(fetch(url), "html.parser")

    table = soup.find("table")
    if not table:
        print("표를 찾을 수 없습니다.")
        return []

    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    region_cols = headers[1:]  # 날짜 제외한 지역 컬럼들

    schedule = []
    current_date = ""

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        date_text = cells[0].get_text(strip=True)
        if re.match(r"\d{2}\.\d{2}", date_text):
            current_date = f"{year}.{date_text}"

        for i, cell in enumerate(cells[1:], 0):
            region = region_cols[i] if i < len(region_cols) else ""
            for a in cell.find_all("a", href=True):
                href = a["href"]
                if "info_BV" not in href:
                    continue
                name = a.get_text(strip=True)
                dist_m = re.search(r"(\d{3,4})", name)
                schedule.append({
                    "date": current_date,
                    "region": region,
                    "name": name,
                    "distance_label": int(dist_m.group(1)) if dist_m else None,
                    "url": BASE + href if href.startswith("/") else href,
                })

    return schedule


# ── 상세 페이지: 거리/획고/시간제한 파싱 ─────────────────────────
def parse_detail(url: str) -> dict:
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    r = {}

    title_tag = soup.select_one(".product-title h4")
    r["title"] = title_tag.get_text(strip=True) if title_tag else ""

    rate_font = soup.select_one(".product-rate font")
    if rate_font:
        raw = rate_font.get_text(strip=True)
        r["raw"] = raw
        dm = re.search(r"(\d[\d,]*)\s*km", raw, re.I)
        em = re.search(r"(\d[\d,]*)\s*m\b", raw, re.I)
        r["distance_km"] = int(dm.group(1).replace(",", "")) if dm else None
        r["elevation_m"]  = int(em.group(1).replace(",", "")) if em else None
    else:
        r["raw"] = r["distance_km"] = r["elevation_m"] = None

    text = soup.get_text(" ")
    tl = re.search(r"Time limit[^\d]*(\d+)\s*h\s*(\d+)\s*m", text, re.I)
    if not tl:
        tl = re.search(r"Time limit[^\d]*(\d+):(\d+)", text, re.I)
    if tl:
        h, m = int(tl.group(1)), int(tl.group(2))
        r["time_limit_str"]     = f"{h}h {m}m"
        r["time_limit_minutes"] = h * 60 + m
    else:
        r["time_limit_str"] = ""
        r["time_limit_minutes"] = None

    sm = re.search(r"Starts? at\s*<[Bb]>(.*?)</[Bb]>", html, re.I)
    if sm:
        r["start_times_raw"] = sm.group(1).strip()
        r["start_times"] = re.findall(r"\d+:\d+", r["start_times_raw"])
    else:
        r["start_times_raw"] = ""
        r["start_times"] = []

    up = re.search(r"Last Update\s*[:\s]*(\d{4}\.\d{2}\.\d{2})", html)
    r["last_update"] = up.group(1) if up else ""

    return r


# ── 출력 ────────────────────────────────────────────────────────
def print_table(items: list):
    print(f"\n{'날짜':<12} {'지역':<10} {'코스':<12} {'거리':>7} {'획고':>8} {'제한':>8}")
    print("-" * 65)
    for it in items:
        d = it.get
        dist = f"{d('distance_km','')} km" if d('distance_km') else "-"
        elev = f"{d('elevation_m','')} m"  if d('elevation_m') else "-"
        print(
            f"{d('date',''):<12} "
            f"{d('region',''):<10} "
            f"{d('name',''):<12} "
            f"{dist:>7} "
            f"{elev:>8} "
            f"{d('time_limit_str',''):>8}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-detail", action="store_true", help="상세 페이지 파싱 생략")
    args = ap.parse_args()

    print(f"▶ {args.year} 브레베 목록 수집 중...")
    schedule = parse_schedule(args.year)
    print(f"  총 {len(schedule)}개 항목 발견")

    if not args.no_detail:
        seen = {}
        for item in schedule:
            url = item["url"]
            if url not in seen:
                print(f"  파싱: {item['name']:<14} {url}")
                try:
                    seen[url] = parse_detail(url)
                    time.sleep(0.3)
                except Exception as e:
                    seen[url] = {}
                    print(f"    ERROR: {e}")
            item.update(seen[url])

    if args.json:
        print(json.dumps(schedule, ensure_ascii=False, indent=2))
    else:
        print_table(schedule)


if __name__ == "__main__":
    main()
