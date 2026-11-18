"""
블로그(blog.tho881.me, 티스토리) 글·이미지를 페르소나별로 NAS 에 보관한다.

저장 구조:
    /mnt/200gb/NAS/persona/blog/
        index.json                          전체 목록(번호·제목·카테고리·페르소나·경로·이미지 수)
        <persona>/<번호 3자리>_<제목>/
            post.md       본문 마크다운(이미지는 images/ 상대 경로)
            post.html     원문 본문 HTML(이미지 src 를 로컬 경로로 바꿈)
            meta.json     url, 제목, 날짜, 카테고리, 태그, 페르소나, 분류 근거, 이미지 목록
            images/       본문 이미지 원본

페르소나 분류: 블로그 카테고리 최상위(일부는 하위까지) 기준 규칙. 규칙에 없는 글은 persona="review" 로 두고
index.json 의 needs_review 로 표시한다(사람이 확인해서 PERSONA_OVERRIDES 에 넣으면 다음 실행에 반영).

사용법:
    venv/bin/python persona/blog_archive.py              # 1..MAX_NO 번 전부 확인(없는 번호는 건너뜀)
    venv/bin/python persona/blog_archive.py --only 48 66 # 특정 글만
다시 실행해도 안전하다(같은 글 폴더를 새로 덮어씀, 페르소나가 바뀌면 옛 폴더를 지움).
"""
import argparse
import json
import mimetypes
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://blog.tho881.me"
OUT = Path("/mnt/200gb/NAS/persona/blog")
MAX_NO = 80
DELAY = 1.0
UA = {"User-Agent": "Mozilla/5.0 (personal blog archiver)"}

# 카테고리 → 페르소나 (앞에서부터 가장 긴 접두사가 먼저 맞도록 정렬해서 사용)
CATEGORY_PERSONA = {
    "IT 기술 및 용어": "developer",
    "자료구조 및 알고리즘": "developer",
    "네트워크": "developer",
    "DataBase": "developer",
    "FrameWork": "developer",
    "IT 공부 독서 정리": "developer",
    "개인/프로젝트": "developer",
    "자전거": "athlete",
    "운동": "athlete",
    "독서": "human",
    "인문학": "human",
    "개인/일기": "human",
}
# 사람이 확인한 개별 글 지정: {글번호: "developer" | "athlete" | "human"}
PERSONA_OVERRIDES: dict[int, str] = {
    2: "developer",   # "기술 블로그를 쓰기 앞서..." (카테고리 "시작...")
}


def classify(no: int, category: str) -> tuple[str, str]:
    if no in PERSONA_OVERRIDES:
        return PERSONA_OVERRIDES[no], "수동 지정"
    for key in sorted(CATEGORY_PERSONA, key=len, reverse=True):
        if category == key or category.startswith(key + "/"):
            return CATEGORY_PERSONA[key], f"카테고리 '{key}'"
    return "review", f"규칙 없음(카테고리 '{category or '-'}')"


def slugify(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\[\]#%{}^~`]+", " ", title)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:60].rstrip("._") or "untitled"


# ── HTML → Markdown (티스토리 본문에 나오는 태그만 단순 변환) ──────────────
def md(node, depth=0) -> str:
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    name = node.name
    inner = lambda: "".join(md(c, depth) for c in node.children)
    if name in ("script", "style", "noscript", "button"):
        return ""
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"\n\n{'#' * int(name[1])} {inner().strip()}\n\n"
    if name == "p":
        t = inner().strip()
        return f"\n\n{t}\n\n" if t else "\n"
    if name == "br":
        return "  \n"
    if name in ("strong", "b"):
        t = inner().strip()
        return f"**{t}**" if t else ""
    if name in ("em", "i"):
        t = inner().strip()
        return f"*{t}*" if t else ""
    if name == "code" and node.parent and node.parent.name != "pre":
        return f"`{node.get_text()}`"
    if name == "pre":
        code = node.find("code")
        lang = ""
        if code and code.get("class"):
            lang = next((c.replace("language-", "") for c in code["class"] if c.startswith("language-")), "")
        return f"\n\n```{lang}\n{node.get_text().rstrip()}\n```\n\n"
    if name == "a":
        t = inner().strip()
        href = node.get("href", "")
        return f"[{t}]({href})" if t and href and not href.startswith("javascript") else t
    if name == "img":
        alt = node.get("alt") or node.get("data-filename") or ""
        return f"\n\n![{alt}]({node.get('src', '')})\n\n"
    if name in ("ul", "ol"):
        out = []
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            mark = f"{i}." if name == "ol" else "-"
            body = "".join(md(c, depth + 1) for c in li.children).strip()
            out.append("  " * depth + f"{mark} {body}")
        return "\n\n" + "\n".join(out) + "\n\n"
    if name == "blockquote":
        t = inner().strip()
        return "\n\n" + "\n".join("> " + l for l in t.splitlines()) + "\n\n"
    if name == "hr":
        return "\n\n---\n\n"
    if name == "table":
        rows = []
        for tr in node.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ")).strip().replace("|", "\\|") for c in tr.find_all(["td", "th"])]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            n = rows[0].count("|") - 1
            rows.insert(1, "|" + " --- |" * n)
        return "\n\n" + "\n".join(rows) + "\n\n"
    if name in ("figure", "div", "span", "section", "figcaption"):
        t = inner()
        return f"\n\n*{t.strip()}*\n\n" if name == "figcaption" and t.strip() else t
    return inner()


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def fetch(session, url):
    r = session.get(url, headers=UA, timeout=30)
    time.sleep(DELAY)
    return r


def ext_for(url, content_type, filename):
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[1].lower()[:5]
    guess = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) if content_type else None
    if guess:
        return ".jpg" if guess == ".jpe" else guess
    path = urlparse(url).path
    return Path(path).suffix[:5] or ".bin"


def archive_post(session, no: int):
    url = f"{BASE}/{no}"
    r = fetch(session, url)
    if r.status_code != 200:
        return {"no": no, "status": r.status_code}
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("#article-view") or soup.select_one(".tt_article_useless_p_margin")
    if article is None:
        return {"no": no, "status": "no-article"}

    og = lambda p: (soup.find("meta", property=p) or {}).get("content", "")
    title = og("og:title") or (soup.title.string if soup.title else f"post {no}")
    published = og("article:published_time")
    m = re.search(r'"categoryLabel":"((?:[^"\\]|\\.)*)"', html)
    category = json.loads(f'"{m.group(1)}"') if m else ""
    tags = [a.get_text(strip=True) for a in soup.select('a[rel="tag"]')]
    persona, reason = classify(no, category)

    folder = OUT / persona / f"{no:03d}_{slugify(title)}"
    for old in OUT.glob(f"*/{no:03d}_*"):          # 페르소나·제목이 바뀌었으면 옛 폴더 정리
        if old != folder:
            shutil.rmtree(old)
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "images").mkdir(parents=True)

    images = []
    for img in article.find_all("img"):
        src = img.get("src") or ""
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http") or "no-image" in src:
            continue
        idx = len(images) + 1
        try:
            ir = fetch(session, src)
            ir.raise_for_status()
        except Exception as e:                       # 이미지 하나 실패해도 글은 저장
            images.append({"index": idx, "source": src.split("?")[0], "error": str(e)[:200]})
            continue
        name = f"{idx:02d}{ext_for(src, ir.headers.get('Content-Type'), img.get('data-filename'))}"
        (folder / "images" / name).write_bytes(ir.content)
        images.append({
            "index": idx, "file": f"images/{name}", "bytes": len(ir.content),
            "original_name": img.get("data-filename"), "source": src.split("?")[0],
            "width": img.get("data-origin-width") or img.get("width"),
            "height": img.get("data-origin-height") or img.get("height"),
        })
        img["src"] = f"images/{name}"
        for attr in ("srcset", "onerror"):
            img.attrs.pop(attr, None)

    body_md = tidy(md(article))
    (folder / "post.md").write_text(
        f"# {title}\n\n> {published[:10]} · {category} · [원문]({url})\n\n{body_md}", encoding="utf-8")
    (folder / "post.html").write_text(str(article), encoding="utf-8")
    meta = {
        "no": no, "url": url, "title": title, "published_at": published, "category": category,
        "tags": tags, "persona": persona, "persona_reason": reason,
        "chars": len(article.get_text(" ", strip=True)), "images": images,
        "archived_at": datetime.now().isoformat(timespec="seconds"),
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": 200, **{k: meta[k] for k in ("no", "title", "published_at", "category", "tags", "persona", "persona_reason", "chars")},
            "path": str(folder.relative_to(OUT)), "image_count": sum(1 for i in images if "file" in i),
            "image_errors": sum(1 for i in images if "error" in i),
            "image_bytes": sum(i.get("bytes", 0) for i in images)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", type=int)
    args = ap.parse_args()
    nos = args.only or list(range(1, MAX_NO + 1))
    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "index.json"
    index = {p["no"]: p for p in json.loads(index_path.read_text()).get("posts", [])} if index_path.exists() and args.only else {}
    skipped = {}
    s = requests.Session()
    for no in nos:
        try:
            res = archive_post(s, no)
        except Exception as e:
            res = {"no": no, "status": f"error: {e}"}
        if res.get("status") == 200:
            res.pop("status")
            index[no] = res
            print(f"✅ {no:3d} [{res['persona']}] {res['title'][:40]} (이미지 {res['image_count']})", flush=True)
        else:
            skipped[no] = res.get("status")
            index.pop(no, None)
    posts = sorted(index.values(), key=lambda p: p["no"])
    summary = {
        "source": BASE, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(posts),
        "by_persona": {k: sum(1 for p in posts if p["persona"] == k) for k in ("developer", "athlete", "human", "review")},
        "image_count": sum(p["image_count"] for p in posts),
        "image_bytes": sum(p["image_bytes"] for p in posts),
        "needs_review": [p["no"] for p in posts if p["persona"] == "review"],
        "skipped": skipped, "posts": posts,
    }
    index_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "posts"}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
