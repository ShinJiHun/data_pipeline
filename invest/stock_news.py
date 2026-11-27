"""
보유 종목 소식 수집 — 네이버 뉴스 검색 + DART 공시 → journal_db.stock_news

대상: 계좌별 최신 invest_account_snapshot 의 invest_snapshot 중 market='KR' 종목 (US 는 추후)

사용법 (저장소 루트에서):
    venv/bin/python -m invest.stock_news --dry-run --symbol 005930   # 한 종목, 저장 없이 출력
    venv/bin/python -m invest.stock_news                             # 전체 보유 KR 종목 → journal_db_dev
    venv/bin/python -m invest.stock_news --notify                    # 새 소식 있으면 Slack 요약 1회
    venv/bin/python -m invest.stock_news --db riding_db --news-db journal_db --allow-prod   # 운영 (확인 후)

crontab 예시 (평일 08:30, 12:30, 18:30 KST — 적용은 관리자 확인 후):
    30 8,12,18 * * 1-5  cd /mnt/200gb/project/data_pipeline && venv/bin/python -m invest.stock_news --notify --db riding_db --news-db journal_db --allow-prod >> /mnt/200gb/logs/invest/stock_news_cron.log 2>&1

필요 환경변수(.env):
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
      (NAVER API HUB 키 등으로 바뀌면 NAVER_NEWS_URL, NAVER_ID_HEADER, NAVER_SECRET_HEADER 로 덮어씀)
    DART_API_KEY
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD
    SLACK_WEBHOOK_URL (--notify 시)
    STOCK_NEWS_DB (기본 journal_db_dev), TOSS_SNAPSHOT_DB (보유 종목 읽을 DB, 기본 riding_db_dev)
    DART_CACHE_DIR (corp_code 캐시, 기본 /mnt/200gb/logs/invest/cache)

안전 규칙:
- API 키는 로그·출력에 남기지 않는다. DART 키는 쿼리스트링에 들어가므로 requests 예외는 타입만 기록한다.
- url UNIQUE + INSERT IGNORE 라 재실행해도 중복 저장되지 않는다.
- 실패한 종목·소스는 건너뛰고 계속한다.
"""

import argparse
import html
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from invest.db import get_env, resolve_db_name, get_connection
from invest.notify import notify_slack

KST = ZoneInfo("Asia/Seoul")
LOG_DIR = Path("/mnt/200gb/logs/invest")
logger = logging.getLogger("stock_news")

NAVER_NEWS_URL = os.environ.get("NAVER_NEWS_URL", "https://openapi.naver.com/v1/search/news.json")
NAVER_ID_HEADER = os.environ.get("NAVER_ID_HEADER", "X-Naver-Client-Id")
NAVER_SECRET_HEADER = os.environ.get("NAVER_SECRET_HEADER", "X-Naver-Client-Secret")
NAVER_DISPLAY = 20

DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DART_DAYS = 7
DART_CACHE_DIR = Path(os.environ.get("DART_CACHE_DIR", "/mnt/200gb/logs/invest/cache"))

REQUEST_TIMEOUT = 15
CALL_SLEEP = 0.3          # 호출 사이 대기(초)
NOTIFY_MAX_LINES = 20
TITLE_MAX, URL_MAX, PUBLISHER_MAX = 500, 700, 100

TAG_RE = re.compile(r"<[^>]+>")


def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(LOG_DIR / "stock_news.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0, tzinfo=None)


# ─────────────────────────────────────────
# 대상 종목
# ─────────────────────────────────────────
def load_targets(conn, symbol: str | None) -> list[dict]:
    """계좌별 최신 스냅샷의 KR 보유 종목 (symbol, name). 계좌가 여럿이면 합집합."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT s.symbol, s.name FROM invest_snapshot s "
            "JOIN (SELECT account_suffix, MAX(snapshot_at) AS max_at FROM invest_account_snapshot "
            "      GROUP BY account_suffix) latest "
            "  ON latest.account_suffix = s.account_suffix AND latest.max_at = s.snapshot_at "
            "WHERE s.market = 'KR' ORDER BY s.symbol"
        )
        rows = [{"symbol": r["symbol"], "name": (r["name"] or "").strip()} for r in cur.fetchall()]
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol]
    return [r for r in rows if r["name"]]


# ─────────────────────────────────────────
# 네이버 뉴스
# ─────────────────────────────────────────
def clean_text(s: str | None) -> str:
    return html.unescape(TAG_RE.sub("", s or "")).strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def _publisher(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    host = host[4:] if host.startswith("www.") else host
    return host[:PUBLISHER_MAX] or None


def _pub_kst(pub: str | None) -> datetime | None:
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).replace(tzinfo=None)


def fetch_naver(http: requests.Session, target: dict) -> tuple[list[dict], int]:
    """(저장 대상 기사, 받은 전체 건수). 종목명이 제목/요약에 실제로 들어간 기사만 남긴다."""
    res = http.get(
        NAVER_NEWS_URL,
        params={"query": target["name"], "display": NAVER_DISPLAY, "sort": "date"},
        headers={NAVER_ID_HEADER: get_env("NAVER_CLIENT_ID"), NAVER_SECRET_HEADER: get_env("NAVER_CLIENT_SECRET")},
        timeout=REQUEST_TIMEOUT,
    )
    if res.status_code != 200:
        try:
            body = res.json()
            detail = f"errorCode={body.get('errorCode')} {body.get('errorMessage')}"
        except ValueError:
            detail = ""
        raise RuntimeError(f"네이버 뉴스 HTTP {res.status_code} {detail}".strip())

    items = res.json().get("items") or []
    key = _norm(target["name"])
    out = []
    for it in items:
        title = clean_text(it.get("title"))
        summary = clean_text(it.get("description"))
        if key not in _norm(title) and key not in _norm(summary):
            continue
        url = (it.get("originallink") or it.get("link") or "").strip()
        published = _pub_kst(it.get("pubDate"))
        if not url or len(url) > URL_MAX or not title or published is None:
            continue
        out.append({
            "source": "NAVER", "title": title[:TITLE_MAX], "summary": summary or None, "url": url,
            "publisher": _publisher(url), "published_at": published, "dart_rcept_no": None,
        })
    return out, len(items)


# ─────────────────────────────────────────
# DART 공시
# ─────────────────────────────────────────
def load_corp_codes(http: requests.Session) -> dict[str, str]:
    """종목코드(6자리) → corp_code. 하루 1회만 받아 JSON 으로 캐시한다."""
    cache = DART_CACHE_DIR / "dart_corp_codes.json"
    today = datetime.now(KST).date()
    if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime, KST).date() == today:
        return json.loads(cache.read_text(encoding="utf-8"))

    res = http.get(DART_CORP_CODE_URL, params={"crtfc_key": get_env("DART_API_KEY")}, timeout=60)
    data = res.content
    if res.status_code != 200 or not zipfile.is_zipfile(io.BytesIO(data)):
        # 키 오류 등은 zip 대신 XML/JSON 상태 메시지로 온다
        status = re.search(rb"<status>(\w+)</status>", data)
        message = re.search(rb"<message>(.*?)</message>", data, re.S)
        raise RuntimeError(
            f"DART corpCode 실패 HTTP {res.status_code} status={status.group(1).decode() if status else '?'} "
            f"{message.group(1).decode('utf-8', 'replace') if message else ''}".strip()
        )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(zf.read(xml_name))
    mapping = {}
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        corp = (el.findtext("corp_code") or "").strip()
        if stock and corp:
            mapping[stock] = corp
    if not mapping:
        raise RuntimeError("DART corpCode 응답에 상장 종목 매핑이 없습니다.")
    try:
        DART_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(mapping), encoding="utf-8")
    except OSError as e:
        logger.warning("corp_code 캐시 저장 실패(%s) — 다음 실행 때 다시 받음", type(e).__name__)
    logger.info("DART corp_code 매핑 %d건 갱신", len(mapping))
    return mapping


def fetch_dart(http: requests.Session, corp_code: str, today: date) -> list[dict]:
    res = http.get(
        DART_LIST_URL,
        params={
            "crtfc_key": get_env("DART_API_KEY"), "corp_code": corp_code,
            "bgn_de": (today - timedelta(days=DART_DAYS)).strftime("%Y%m%d"), "end_de": today.strftime("%Y%m%d"),
            "page_count": 100,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if res.status_code != 200:
        raise RuntimeError(f"DART list HTTP {res.status_code}")
    body = res.json()
    status = body.get("status")
    if status == "013":  # 조회된 데이터 없음
        return []
    if status != "000":
        raise RuntimeError(f"DART list status={status} {body.get('message')}")
    out = []
    for it in body.get("list") or []:
        rcept_no = (it.get("rcept_no") or "").strip()
        title = (it.get("report_nm") or "").strip()
        try:
            published = datetime.strptime(it.get("rcept_dt") or "", "%Y%m%d")
        except ValueError:
            continue
        if not rcept_no or not title:
            continue
        flr = (it.get("flr_nm") or "").strip()
        out.append({
            "source": "DART", "title": re.sub(r"\s+", " ", title)[:TITLE_MAX],
            "summary": f"제출인: {flr}" if flr else None, "url": DART_VIEW_URL.format(rcept_no),
            "publisher": "DART", "published_at": published, "dart_rcept_no": rcept_no,
        })
    return out


# ─────────────────────────────────────────
# 저장
# ─────────────────────────────────────────
INSERT_SQL = (
    "INSERT IGNORE INTO stock_news "
    "(market, symbol, name, source, title, summary, url, publisher, published_at, collected_at, dart_rcept_no) "
    "VALUES ('KR', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def save_items(conn, target: dict, items: list[dict], collected_at: datetime) -> list[dict]:
    """새로 들어간 행만 돌려준다 (url 중복은 INSERT IGNORE 로 건너뜀)."""
    new = []
    with conn.cursor() as cur:
        for it in items:
            cur.execute(INSERT_SQL, (
                target["symbol"], target["name"], it["source"], it["title"], it["summary"], it["url"],
                it["publisher"], it["published_at"], collected_at, it["dart_rcept_no"],
            ))
            if cur.rowcount == 1:
                new.append(it)
    conn.commit()
    return new


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


# ─────────────────────────────────────────
# 출력·알림
# ─────────────────────────────────────────
def print_items(target: dict, naver: list[dict], naver_total: int | None, dart: list[dict] | None) -> None:
    print(f"\n■ {target['name']} ({target['symbol']})")
    if naver_total is not None:
        print(f"  [네이버] 받은 {naver_total}건 중 종목명 포함 {len(naver)}건")
        for it in naver:
            print(f"   - {it['published_at']:%m-%d %H:%M} {it['publisher'] or '-'} | {it['title']}")
            print(f"     {it['url']}")
    if dart is not None:
        print(f"  [DART] 최근 {DART_DAYS}일 공시 {len(dart)}건")
        for it in dart:
            print(f"   - {it['published_at']:%m-%d} | {it['title']} ({it['summary'] or ''})")
            print(f"     {it['url']}")


def send_notify(new_by_target: list[tuple[dict, list[dict]]]) -> None:
    lines = []
    total = sum(len(items) for _, items in new_by_target)
    for target, items in new_by_target:
        for it in sorted(items, key=lambda x: x["published_at"], reverse=True):
            tag = "[공시] " if it["source"] == "DART" else ""
            lines.append(f"• {target['name']}: {tag}{it['title']}")
    shown = lines[:NOTIFY_MAX_LINES]
    more = f"\n…외 {len(lines) - len(shown)}건" if len(lines) > len(shown) else ""
    notify_slack(f":newspaper: 보유 종목 새 소식 {total}건\n" + "\n".join(shown) + more)


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def run(args) -> int:
    holdings_db = resolve_db_name(args.db, args.allow_prod)
    news_db = None if args.dry_run else resolve_db_name(
        args.news_db, args.allow_prod, env_key="STOCK_NEWS_DB", default="journal_db_dev")
    logger.info("시작: dry_run=%s holdings_db=%s news_db=%s symbol=%s",
                args.dry_run, holdings_db, news_db or "-", args.symbol or "전체")

    conn = get_connection(holdings_db)
    try:
        targets = load_targets(conn, args.symbol)
    finally:
        conn.close()
    if not targets:
        logger.warning("대상 KR 보유 종목이 없습니다%s.", f" (--symbol {args.symbol})" if args.symbol else "")
        return 0
    logger.info("대상 %d종목: %s", len(targets), ", ".join(t["symbol"] for t in targets))

    http = requests.Session()
    today = datetime.now(KST).date()
    corp_codes = None
    if not args.skip_dart:
        try:
            corp_codes = load_corp_codes(http)
        except Exception as e:
            logger.error("DART 매핑 실패 — 공시는 건너뜀: %s",
                         str(e) if isinstance(e, (RuntimeError, EnvironmentError)) else type(e).__name__)

    news_conn = None if args.dry_run else get_connection(news_db)
    collected_at = now_kst()
    new_by_target: list[tuple[dict, list[dict]]] = []
    failed = 0
    try:
        for target in targets:
            naver, naver_total, dart = [], None, None
            try:
                if not args.skip_naver:
                    naver, naver_total = fetch_naver(http, target)
                    time.sleep(CALL_SLEEP)
                if corp_codes is not None:
                    corp = corp_codes.get(target["symbol"])
                    if corp:
                        dart = fetch_dart(http, corp, today)
                        time.sleep(CALL_SLEEP)
                    else:
                        dart = []
                        logger.info("%s: DART corp_code 없음 (ETF 등)", target["symbol"])
            except Exception as e:
                failed += 1
                # requests 예외 문자열에는 키가 든 URL 이 섞일 수 있어 타입만 남긴다
                detail = str(e) if isinstance(e, (RuntimeError, EnvironmentError)) else type(e).__name__
                logger.error("%s(%s) 수집 실패 — 건너뜀: %s", target["name"], target["symbol"], detail)
                continue

            items = _dedupe(naver + (dart or []))
            if args.dry_run:
                print_items(target, naver, naver_total, dart)
                continue
            try:
                new = save_items(news_conn, target, items, collected_at)
            except Exception as e:
                failed += 1
                news_conn.rollback()
                logger.error("%s(%s) 저장 실패 — 건너뜀: %s", target["name"], target["symbol"], type(e).__name__)
                continue
            logger.info("%s(%s): 네이버 %d / DART %d → 신규 %d건", target["name"], target["symbol"],
                        len(naver), len(dart or []), len(new))
            if new:
                new_by_target.append((target, new))
    finally:
        if news_conn:
            news_conn.close()

    if args.dry_run:
        print("\n(dry-run: DB 저장 안 함)")
    else:
        total_new = sum(len(n) for _, n in new_by_target)
        logger.info("완료: 대상 %d종목, 신규 %d건, 실패 %d종목", len(targets), total_new, failed)
        if args.notify and total_new:
            send_notify(new_by_target)
    return 1 if failed == len(targets) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="보유 종목 소식 수집 (네이버 뉴스 + DART 공시)")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 출력만")
    parser.add_argument("--symbol", help="한 종목만 (예: 005930)")
    parser.add_argument("--notify", action="store_true", help="새 소식이 있으면 Slack 요약 1회")
    parser.add_argument("--db", help="보유 종목을 읽을 DB (기본 TOSS_SNAPSHOT_DB 또는 riding_db_dev)")
    parser.add_argument("--news-db", help="stock_news 저장 DB (기본 STOCK_NEWS_DB 또는 journal_db_dev)")
    parser.add_argument("--allow-prod", action="store_true", help="*_dev 가 아닌 DB 사용 허용")
    parser.add_argument("--skip-naver", action="store_true")
    parser.add_argument("--skip-dart", action="store_true")
    args = parser.parse_args()

    setup_logging()
    try:
        sys.exit(run(args))
    except Exception as e:
        code = getattr(e, "args", [None])[0] if type(e).__module__.startswith("pymysql") else None
        detail = (f"DB 오류 code={code}" if code is not None
                  else str(e) if isinstance(e, (RuntimeError, EnvironmentError)) else type(e).__name__)
        logger.error("실패: %s", detail)
        sys.exit(1)


if __name__ == "__main__":
    main()
