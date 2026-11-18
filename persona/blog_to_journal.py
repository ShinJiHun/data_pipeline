"""
보관한 블로그 라이딩 글(NAS persona/blog/athlete)을 휴먼 블로그 "자전거 일주기"(journal_post)로 옮긴다.

- 본문: post.html 을 단순 HTML 로 정리(허용 태그만, 속성은 src/alt/href 만), 이미지는 본문용 크기(긴 변 960px WebP)로
  /api/blog-archive/file?path=... 주소로 바꾼다(앱 BlogArchiveController 가 NAS 에서 서빙).
- 글 맨 위: 그날의 라이딩 기록(거리·획득고도) → 활동 상세(지도·사진) 링크, 원문 링크.
- 작성일: 원래 블로그 발행 시각. 공개(public).
- 다시 실행하면 같은 글(본문의 data-blog-no 표식, 없으면 같은 메뉴의 같은 제목)을 갱신한다 — 중복 생성 안 함.

사용법:
  venv/bin/python persona/blog_to_journal.py --target dev [--dry-run]
  venv/bin/python persona/blog_to_journal.py --target prod --posts 29 32
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pymysql
from bs4 import BeautifulSoup, Comment
from PIL import Image, ImageOps

BLOG_ROOT = Path("/mnt/200gb/NAS/persona/blog")
ATHLETE = BLOG_ROOT / "athlete"
ENV = {"dev": ("/mnt/200gb/apps-dev/.env.db", "journal_db_dev", "riding_db_dev"),
       "prod": ("/mnt/200gb/project/data_pipeline/.env", "journal_db", "riding_db")}
CATEGORY = "자전거 일주기"
# 블로그 글 번호 → 휴먼 블로그 하위 메뉴
SUB_MENU = {
    "투어링": [16, 14, 15, 29, 32, 44, 13, 20, 21],
    "랜도너스": [31, 34],
}
# 블로그 날짜와 라이딩 기록 날짜가 어긋나 자동 연결하면 틀리는 글(영산강·제주: 5/1 기록이 1일차 경로)
NO_ACTIVITY_LINK = {20, 21}
POST_SIZE = 960
DATE_RE = re.compile(r"(20\d{2}|\d{2})[-./](\d{2})[-./](\d{2})")
KEEP = {"p", "h2", "h3", "h4", "figure", "figcaption", "img", "ul", "ol", "li", "blockquote", "a", "strong", "b",
        "em", "i", "br", "hr", "table", "thead", "tbody", "tr", "td", "th", "pre", "code"}
UNWRAP = {"div", "span", "section", "font", "u", "s", "sup", "sub"}


def read_env(path):
    env = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def post_image(folder: Path, rel_src: str):
    """images/NN.ext → post/NN.webp (본문 폭에 맞춘 긴 변 960px WebP, 없으면 만든다).
    돌려주는 값: (보관 폴더 기준 상대 경로, 가로, 세로) — 크기는 화면이 자리를 미리 잡게 img 에 넣는다."""
    src = folder / rel_src
    if not src.is_file():
        return None
    dst = folder / "post" / (src.stem + ".webp")
    if not dst.exists():
        dst.parent.mkdir(exist_ok=True)
        with Image.open(src) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im.convert("RGB"))
            im.thumbnail((POST_SIZE, POST_SIZE))
            im.save(dst, "WEBP", quality=78, method=6)
    with Image.open(dst) as im:
        w, h = im.size
    return str(dst.relative_to(BLOG_ROOT)), w, h


def clean_body(folder: Path) -> tuple[str, int]:
    soup = BeautifulSoup((folder / "post.html").read_text(), "lxml")
    root = soup.select_one("#article-view") or soup.body
    for el in root.select(".another_category, .container_postbtn, .postbtn_like, .wrap_btn, .layer_post, "
                          ".revenue_unit_wrap, [data-tistory-react-app]"):
        el.decompose()                                   # 티스토리 공유 버튼·"카테고리의 다른 글" 표 등
    images = 0
    for c in root.find_all(string=lambda t: isinstance(t, Comment)):   # <!-- System - START --> 등
        c.extract()
    for el in list(root.find_all(True)):
        if el.name in ("script", "style", "noscript", "button", "iframe"):
            el.decompose()
    for el in list(root.find_all(True)):
        if el.name in UNWRAP:
            el.unwrap()
        elif el.name not in KEEP:
            el.unwrap()
    for el in root.find_all(True):
        keep = {}
        if el.name == "img":
            img = post_image(folder, el.get("src", ""))
            if not img:
                el.decompose()
                continue
            path, w, h = img
            keep = {"src": "/api/blog-archive/file?path=" + quote(path), "alt": el.get("alt") or "",
                    "width": str(w), "height": str(h), "loading": "lazy", "decoding": "async"}
            images += 1
        elif el.name == "a" and el.get("href", "").startswith("http"):
            keep = {"href": el["href"], "target": "_blank", "rel": "noopener noreferrer"}
        el.attrs = keep
    out = "".join(str(c) for c in root.children)
    out = re.sub(r"\s*\n\s*", " ", out)                       # 화면이 pre-wrap 이라 줄바꿈 제거
    out = re.sub(r"(<p>(\s|&nbsp;|\xa0|<br/?>)*</p>\s*){2,}", "<p><br></p>", out)  # 빈 문단 여러 개 → 하나
    return out.strip(), images


def activity_box(cur_riding, no, title, url):
    lines = []
    m = DATE_RE.search(title)
    if m and no not in NO_ACTIVITY_LINK:
        y = int(m.group(1)); y = y + 2000 if y < 100 else y
        day = f"{y}-{m.group(2)}-{m.group(3)}"
        cur_riding.execute("""SELECT id, total_distance, total_ascent FROM activity_core
                              WHERE user_id = 1 AND DATE(start_time) = %s ORDER BY total_distance DESC LIMIT 1""", (day,))
        r = cur_riding.fetchone()
        if r:
            km = f"{(r[1] or 0) / 1000:.1f}km"
            up = f" · 획득고도 {r[2]:,.0f}m" if r[2] else ""
            lines.append(f'<p>🚴 <strong>{day} 라이딩 기록</strong> · {km}{up} — '
                         f'<a href="/records/riding/{r[0]}">지도와 사진 보기 →</a></p>')
    lines.append(f'<p data-blog-no="{no}">📝 원문: <a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">'
                 f'{html.escape(url.replace("https://", ""))}</a></p>')
    return "<blockquote>" + "".join(lines) + "</blockquote>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=ENV, required=True)
    ap.add_argument("--posts", nargs="*", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env_path, journal_db, riding_db = ENV[a.target]
    env = read_env(env_path)
    conn = lambda db: pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                                      database=db, charset="utf8mb4", autocommit=False)
    j, r = conn(journal_db), conn(riding_db)
    try:
        jc, rc = j.cursor(), r.cursor()
        jc.execute("""SELECT s.name, s.id FROM journal_sub_menu s JOIN journal_category c ON c.id = s.category_id
                      WHERE c.name = %s AND c.persona = 'human'""", (CATEGORY,))
        subs = dict(jc.fetchall())
        missing = [s for s in SUB_MENU if s not in subs]
        if missing:
            sys.exit(f"'{CATEGORY}' 아래 하위 메뉴 없음: {missing} (있는 메뉴: {list(subs)})")
        for sub_name, nos in SUB_MENU.items():
            for no in nos:
                if a.posts and no not in a.posts:
                    continue
                folder = next(ATHLETE.glob(f"{no:03d}_*"), None)
                if not folder:
                    print(f"  ⚠️ #{no} 보관 폴더 없음"); continue
                meta = json.loads((folder / "meta.json").read_text())
                body, n_img = clean_body(folder)
                content = activity_box(rc, no, meta["title"], meta["url"]) + body
                created = datetime.fromisoformat(meta["published_at"]).replace(tzinfo=None) if meta.get("published_at") else datetime.now()
                sub_id = subs[sub_name]
                jc.execute("SELECT id FROM journal_post WHERE content LIKE %s LIMIT 1", (f'%data-blog-no="{no}"%',))
                row = jc.fetchone()
                if not row:
                    jc.execute("SELECT id FROM journal_post WHERE sub_id = %s AND title = %s LIMIT 1", (sub_id, meta["title"]))
                    row = jc.fetchone()
                action = "갱신" if row else "추가"
                if not a.dry_run:
                    if row:
                        jc.execute("""UPDATE journal_post SET sub_id=%s, title=%s, content=%s, created_at=%s,
                                      updated_at=NOW(6), visibility='public' WHERE id=%s""",
                                   (sub_id, meta["title"], content, created, row[0]))
                    else:
                        jc.execute("""INSERT INTO journal_post (sub_id, title, content, created_at, updated_at, visibility)
                                      VALUES (%s,%s,%s,%s,NOW(6),'public')""", (sub_id, meta["title"], content, created))
                linked = "라이딩 연결" if "/records/riding/" in content else "연결 없음"
                print(f"  {action} [{sub_name}] #{no} {meta['title'][:34]} · 사진 {n_img} · {linked} · {len(content)//1024}KB")
        (j.rollback if a.dry_run else j.commit)()
    finally:
        j.close(); r.close()


if __name__ == "__main__":
    sys.exit(main())
