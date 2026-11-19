"""
보관한 블로그 책 리뷰(NAS persona/blog/human)를 휴먼 블로그 "독서" 메뉴(journal_post)로 옮긴다.

- 본문 정리·이미지 변환은 blog_to_journal.clean_body 를 그대로 쓴다(허용 태그만, 이미지는 긴 변 960px WebP,
  /api/blog-archive/file?path=... 주소).
- 글 끝: 원문(티스토리) 링크 + 표식 data-blog-no.
- 작성일: 원래 블로그 발행 시각. 공개(public).
- 다시 실행하면 같은 글(data-blog-no 표식, 없으면 같은 메뉴의 같은 제목)을 갱신한다 — 중복 생성 안 함.

사용법:
  venv/bin/python persona/blog_books_to_journal.py --target dev [--dry-run]
  venv/bin/python persona/blog_books_to_journal.py --target prod --posts 25
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blog_to_journal import BLOG_ROOT, ENV, clean_body, read_env  # noqa: E402

HUMAN = BLOG_ROOT / "human"
CATEGORY = "독서"
# 블로그 글 번호 → "독서" 하위 메뉴. #7(제정신이라는 착각)은 이미 직접 옮겨 둔 글이 있어 뺐다.
# #47(진중권 - 존재의 미학)은 강의(GMC) 정리라 책 리뷰가 아님 — 사용자 확인 전까지 제외.
SUB_MENU = {
    "인문학": [25, 50],
}


def footer(no: int, url: str, published: datetime) -> str:
    return (f'<p><br></p><p data-blog-no="{no}">📝 원문: <a href="{html.escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">티스토리 ({published:%Y-%m-%d})</a></p>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=ENV, required=True)
    ap.add_argument("--posts", nargs="*", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env_path, journal_db, _ = ENV[a.target]
    env = read_env(env_path)
    j = pymysql.connect(host="127.0.0.1", port=3306, user=env["DB_USER"], password=env["DB_PASSWORD"],
                        database=journal_db, charset="utf8mb4", autocommit=False)
    try:
        jc = j.cursor()
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
                folder = next(HUMAN.glob(f"{no:03d}_*"), None)
                if not folder:
                    print(f"  ⚠️ #{no} 보관 폴더 없음"); continue
                meta = json.loads((folder / "meta.json").read_text())
                body, n_img = clean_body(folder)
                # 본문 첫 제목(h1)은 clean_body 가 태그를 벗겨 맨 글자로 남는다 → h2 로 감싼다.
                m = re.match(r"\s*([^<]+?)\s*(?=<)", body)
                if m and m.group(1).strip():
                    body = f"<h2>{m.group(1).strip()}</h2>" + body[m.end():]
                created = datetime.fromisoformat(meta["published_at"]).replace(tzinfo=None)
                content = body + footer(no, meta["url"], created)
                sub_id = subs[sub_name]
                jc.execute("SELECT id FROM journal_post WHERE content LIKE %s LIMIT 1", (f'%data-blog-no="{no}"%',))
                row = jc.fetchone()
                if not row:
                    jc.execute("SELECT id FROM journal_post WHERE sub_id = %s AND title = %s LIMIT 1",
                               (sub_id, meta["title"]))
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
                print(f"  {action} [{sub_name}] #{no} {meta['title'][:34]} · {created:%Y-%m-%d %H:%M} · 사진 {n_img} · {len(content)//1024}KB")
        (j.rollback if a.dry_run else j.commit)()
    finally:
        j.close()


if __name__ == "__main__":
    sys.exit(main())
