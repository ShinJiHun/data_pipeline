"""
블로그 라이딩 후기 사진을 그날의 라이딩 기록(activity_core)에 연결해 activity_image 에 넣는다.

입력: /mnt/200gb/NAS/persona/blog/athlete/<글>/ (blog_archive.py 결과: meta.json, post.html, images/)
출력:
  - DB activity_image (source='BLOG') — 글 단위로 지우고 다시 넣으므로 여러 번 실행해도 안전
  - 같은 글 폴더에 thumbs/(긴 변 360px), web/(긴 변 1600px) JPEG — 지도 마커·팝업용 경량 이미지

위치 정하는 방법:
  EXIF_TIME       사진 촬영 시각(EXIF, KST) → 그 시각의 라이딩 좌표(activity_point 보간). 라이딩 전후 3시간 이내는 시작/끝 지점.
  ORDER_ESTIMATE  촬영 시각이 없지만 글에서 앞뒤 사진에 시각이 있으면, 순서 비율로 시각을 추정해 같은 방식으로 좌표.
  NONE            단서 없음 — 지도에는 안 올리고 사진 목록에만.
  (블로그 사진의 EXIF GPS 는 업로드 과정에서 0 으로 지워져 있어 쓰지 않는다.)

글 → 활동: 제목의 날짜(예: "2024-04-06", "24-04-04")와 같은 날 user_id=1 활동 중 가장 긴 것.

사용법:
  venv/bin/python persona/blog_photo_link.py --target dev               # 기본: 제목에 '백두대간' + 날짜가 있는 athlete 글
  venv/bin/python persona/blog_photo_link.py --target prod --posts 29 32
  --dry-run 이면 DB 는 건드리지 않고 결과만 출력.
"""
import argparse
import bisect
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pymysql
from bs4 import BeautifulSoup, NavigableString
from PIL import Image, ImageOps

BLOG_ROOT = Path("/mnt/200gb/NAS/persona/blog")
ATHLETE = BLOG_ROOT / "athlete"
ENV = {"dev": ("/mnt/200gb/apps-dev/.env.db", "riding_db_dev"),
       "prod": ("/mnt/200gb/project/data_pipeline/.env", "riding_db")}
EDGE_TOLERANCE = timedelta(hours=3)
THUMB, WEB = 360, 1600
DATE_RE = re.compile(r"(20\d{2}|\d{2})[-./](\d{2})[-./](\d{2})")


def read_env(path):
    env = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def connect(target):
    env_path, schema = ENV[target]
    env = read_env(env_path)
    return pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                           database=schema, charset="utf8mb4", autocommit=False)


def post_date(title):
    m = DATE_RE.search(title)
    if not m:
        return None
    y = int(m.group(1))
    y = y + 2000 if y < 100 else y
    return datetime(y, int(m.group(2)), int(m.group(3))).date()


def exif_time(path):
    try:
        ex = Image.open(path).getexif()
        t = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        return datetime.strptime(t, "%Y:%m:%d %H:%M:%S") if t else None
    except Exception:
        return None


def captions(post_html):
    """각 이미지에 붙일 문장. 티스토리는 사진 여러 장(묶음) 뒤에 figcaption 하나를 다는 구조라,
    묶음 뒤 첫 figcaption 을 묶음 전체에 쓰고, 캡션 없이 글 문단이 이어지면 묶음 바로 앞 문단을 쓴다."""
    soup = BeautifulSoup(post_html, "lxml")
    clean = lambda el: re.sub(r"\s+", " ", el.get_text(" ")).strip()
    result, last_text, pending = {}, "", []

    def flush(text):
        for src in pending:
            result[src] = text[:300]
        pending.clear()

    for el in soup.descendants:
        if isinstance(el, NavigableString) or el.name is None:
            continue
        if el.name == "img":
            pending.append(el.get("src", ""))
        elif el.name == "figcaption":
            t = clean(el)
            if t and pending:
                flush(t)
        elif el.name in ("p", "h2", "h3", "h4", "li", "blockquote") and not el.find("img") and not el.find_parent("figcaption"):
            t = clean(el)
            if t:
                flush(last_text)      # 캡션 없이 끝난 묶음 → 앞 문단
                last_text = t
    flush(last_text)
    return result


def resize(src, dst, size):
    dst.parent.mkdir(exist_ok=True)
    with Image.open(src) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im.convert("RGB"))
        im.thumbnail((size, size))
        im.save(dst, "JPEG", quality=82, optimize=True)
        return im.size


class Track:
    def __init__(self, rows):
        self.t = [r[0] for r in rows]
        self.rows = rows

    def at(self, when):
        """그 시각의 (lat, lon, distance). 범위 밖이면 허용 범위 안에서 시작/끝 지점."""
        if not self.t:
            return None
        if when < self.t[0]:
            return self.rows[0][1:] if self.t[0] - when <= EDGE_TOLERANCE else None
        if when > self.t[-1]:
            return self.rows[-1][1:] if when - self.t[-1] <= EDGE_TOLERANCE else None
        i = bisect.bisect_left(self.t, when)
        if self.t[i] == when or i == 0:
            return self.rows[i][1:]
        a, b = self.rows[i - 1], self.rows[i]
        span = (b[0] - a[0]).total_seconds() or 1
        f = (when - a[0]).total_seconds() / span
        lerp = lambda x, y: x + (y - x) * f if x is not None and y is not None else (x if x is not None else y)
        return lerp(a[1], b[1]), lerp(a[2], b[2]), lerp(a[3], b[3])


def link_post(cur, folder: Path, dry_run: bool):
    meta = json.loads((folder / "meta.json").read_text())
    day = post_date(meta["title"])
    if not day:
        return f"건너뜀(제목에 날짜 없음): {meta['title']}"
    cur.execute("""SELECT id FROM activity_core WHERE user_id = 1 AND DATE(start_time) = %s
                   ORDER BY total_distance DESC LIMIT 1""", (day,))
    row = cur.fetchone()
    if not row:
        return f"건너뜀({day} 라이딩 기록 없음): {meta['title']}"
    activity_id = row[0]
    cur.execute("""SELECT point_time, lat, lon, distance FROM activity_point
                   WHERE activity_core_id = %s AND lat IS NOT NULL AND point_time IS NOT NULL ORDER BY seq""",
                (activity_id,))
    track = Track(cur.fetchall())
    caps = captions((folder / "post.html").read_text())

    photos = [i for i in meta["images"] if "file" in i]
    times = [exif_time(folder / p["file"]) for p in photos]
    # 시각 없는 사진: 앞뒤로 시각 있는 사진이 모두 있으면 순서 비율로 추정
    est = list(times)
    known = [i for i, t in enumerate(times) if t]
    for i, t in enumerate(times):
        if t:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is not None and nxt is not None and times[nxt] >= times[prev]:
            est[i] = times[prev] + (times[nxt] - times[prev]) * ((i - prev) / (nxt - prev))

    records, stats = [], {"EXIF_TIME": 0, "ORDER_ESTIMATE": 0, "NONE": 0}
    for i, p in enumerate(photos):
        src = folder / p["file"]
        rel = src.relative_to(BLOG_ROOT)
        thumb = folder / "thumbs" / (src.stem + ".jpg")
        web = folder / "web" / (src.stem + ".jpg")
        try:
            resize(src, thumb, THUMB)
            w, h = resize(src, web, WEB)
        except Exception as e:
            print(f"  ⚠️ 이미지 변환 실패 {rel}: {e}")
            continue
        when = est[i]
        pos = track.at(when) if when and when.date() == day else None
        source = "NONE" if not pos else ("EXIF_TIME" if times[i] else "ORDER_ESTIMATE")
        stats[source] += 1
        records.append((
            activity_id, str(rel), "image/jpeg", "BLOG", i + 1, str(thumb.relative_to(BLOG_ROOT)),
            when if pos else times[i], pos[0] if pos else None, pos[1] if pos else None, pos[2] if pos else None,
            source, caps.get(p["file"], "") or None, w, h, meta["no"], meta["title"][:255], meta["url"],
        ))

    if not dry_run:
        cur.execute("DELETE FROM activity_image WHERE source = 'BLOG' AND blog_post_no = %s", (meta["no"],))
        cur.executemany("""INSERT INTO activity_image
            (activity_core_id, image_path, image_type, source, sort_order, thumb_path, taken_at, lat, lon, distance_m,
             location_source, caption, width, height, blog_post_no, blog_post_title, blog_post_url)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", records)
    return (f"#{meta['no']} {meta['title'][:30]} → 활동 {activity_id} ({day}) 사진 {len(records)}장: "
            f"시각 {stats['EXIF_TIME']} / 추정 {stats['ORDER_ESTIMATE']} / 위치없음 {stats['NONE']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=ENV, required=True)
    ap.add_argument("--posts", nargs="*", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.posts:
        folders = [f for n in a.posts for f in ATHLETE.glob(f"{n:03d}_*")]
    else:
        folders = [f for f in sorted(ATHLETE.iterdir()) if "백두대간" in f.name and DATE_RE.search(f.name)]
    conn = connect(a.target)
    try:
        with conn.cursor() as cur:
            for f in folders:
                print(link_post(cur, f, a.dry_run), flush=True)
        conn.rollback() if a.dry_run else conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
