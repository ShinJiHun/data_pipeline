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
    DART_CACHE_DIR (corp_code 캐시, 기본 INVEST_LOG_DIR/cache)
    INVEST_LOG_DIR (로그 폴더, 기본 /mnt/200gb/logs/invest)
    STOCK_NEWS_SORT (date 기본 / sim), STOCK_NEWS_MATCH (strict 기본 / any)
    종목별 검색어·키워드·제외어: invest/stock_news_aliases.json

로컬(DB 없이) 확인:
    python -m invest.stock_news --dry-run --targets "005930:삼성전자,000660:SK하이닉스"
    → 보유 종목을 DB 대신 --targets(또는 .env STOCK_TARGETS)에서 읽는다. 저장하려면 DB 가 있어야 한다.

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

# 회사망처럼 HTTPS 를 중간에서 검사하는 네트워크에서는 파이썬 기본 인증서 목록으로 검증이 실패한다.
# truststore 가 설치돼 있으면 OS(Windows·macOS) 인증서 저장소로 검증한다. 검증을 끄지는 않는다.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from invest.db import get_env, resolve_db_name, get_connection
from invest.notify import notify_slack

KST = ZoneInfo("Asia/Seoul")
# 서버 기본값. 로컬(Windows 등)에서는 .env 의 INVEST_LOG_DIR 로 바꾼다
LOG_DIR = Path(os.environ.get("INVEST_LOG_DIR", "/mnt/200gb/logs/invest"))
logger = logging.getLogger("stock_news")

NAVER_NEWS_URL = os.environ.get("NAVER_NEWS_URL", "https://openapi.naver.com/v1/search/news.json")
NAVER_ID_HEADER = os.environ.get("NAVER_ID_HEADER", "X-Naver-Client-Id")
NAVER_SECRET_HEADER = os.environ.get("NAVER_SECRET_HEADER", "X-Naver-Client-Secret")
NAVER_DISPLAY = 20
# 정렬: date(최신순, 기본) / sim(관련도순)
NEWS_SORT = os.environ.get("STOCK_NEWS_SORT", "date")
# 걸러내기: strict(제목에 있거나 요약에 2번 이상, 기본) / any(제목·요약 어디든 한 번)
NEWS_MATCH = os.environ.get("STOCK_NEWS_MATCH", "strict")
# 종목별 검색어·키워드·제외어 (흔한 말과 겹치는 종목명, 약칭 등)
ALIASES_FILE = Path(os.environ.get("STOCK_NEWS_ALIASES", str(Path(__file__).with_name("stock_news_aliases.json"))))

DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DART_DAYS = 7
CORP_CODE_DEADLINE = 120  # corpCode.xml 다운로드 전체 제한(초)
DART_CACHE_DIR = Path(os.environ.get("DART_CACHE_DIR", str(LOG_DIR / "cache")))

REQUEST_TIMEOUT = 15
CALL_SLEEP = 0.3          # 호출 사이 대기(초)
NOTIFY_MAX_LINES = 20
TITLE_MAX, URL_MAX, PUBLISHER_MAX = 500, 700, 100

TAG_RE = re.compile(r"<[^>]+>")


def safe_error(e: Exception) -> str:
    """로그용 에러 설명. requests 예외(ConnectionError 는 OSError 라 EnvironmentError 이기도 함) 문자열에는
    키가 든 URL 이 섞이므로 종류와 원인 종류만 남긴다."""
    if isinstance(e, requests.RequestException):
        cause = e.__context__ or e.__cause__
        reason = getattr(getattr(e, "args", [None])[0], "reason", None) if e.args else None
        hint = type(reason).__name__ if reason is not None else (type(cause).__name__ if cause else "")
        if "SSL" in hint or "SSL" in type(e).__name__:
            hint += " (TLS 검증 실패 — 회사망이면 pip install truststore)"
        return f"{type(e).__name__}{': ' + hint if hint else ''}"
    if isinstance(e, (RuntimeError, EnvironmentError)):
        return str(e)
    return type(e).__name__


# 로그에 찍히기 직전에 키를 한 번 더 가린다 (에러 처리에서 놓친 경우까지 막는 마지막 안전장치)
SECRET_ENV_KEYS = ("DART_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
                   "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DB_PASSWORD", "SLACK_WEBHOOK_URL")
SECRET_PARAM_RE = re.compile(r"(?i)((?:crtfc_key|client_secret|client_id|api_key|token)=)[^&\s'\")]+")


def mask_secrets(text: str) -> str:
    text = SECRET_PARAM_RE.sub(r"\1***", text)
    for key in SECRET_ENV_KEYS:
        val = os.environ.get(key)
        if val and len(val) >= 6:
            text = text.replace(val, "***")
    return text


class SecretMaskFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        masked = mask_secrets(msg)
        if masked != msg:
            record.msg, record.args = masked, None
        return True


def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(LOG_DIR / "stock_news.log", encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.addFilter(SecretMaskFilter())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0, tzinfo=None)


# ─────────────────────────────────────────
# 대상 종목
# ─────────────────────────────────────────
def parse_targets(spec: str, symbol: str | None) -> list[dict]:
    """'005930:삼성전자,000660:SK하이닉스' → [{symbol, name}]. DB 없이 로컬에서 돌릴 때."""
    rows = []
    for part in spec.split(","):
        sym, _, name = part.strip().partition(":")
        if sym.strip() and name.strip():
            rows.append({"symbol": sym.strip(), "name": name.strip()})
    if symbol:
        rows = [r for r in rows if r["symbol"] == symbol]
    return rows


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


def load_aliases() -> dict:
    """종목별 검색어·키워드·제외어 (invest/stock_news_aliases.json, 없으면 빈 설정)."""
    try:
        return json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        logger.warning("%s 형식 오류 — 별칭 없이 진행: %s", ALIASES_FILE.name, e)
        return {}


def _count(text: str, words: list[str]) -> int:
    """키워드가 나온 횟수. 'SK하이닉스' 와 '하이닉스' 처럼 겹치는 키워드는 한 번으로 센다(긴 것 우선)."""
    ws = sorted({_norm(w) for w in words if w}, key=len, reverse=True)
    if not ws:
        return 0
    return len(re.findall("|".join(map(re.escape, ws)), _norm(text)))


def match_article(title: str, summary: str, keywords: list[str], exclude: list[str],
                  mode: str | None = None, context: list[str] | None = None) -> str | None:
    """남기면 None, 버리면 이유. 제목에 키워드가 있거나, 요약에 2번 이상 나와야 '이 종목 기사'로 본다.
    (요약에 한 번 스치듯 나오는 기사 — 다른 회사 기사 속 '삼성전자와 협력' 같은 것 — 를 거른다)"""
    if exclude and _count(title + " " + summary, exclude):
        return "제외어"
    # 흔한 말과 겹치는 종목명(예: 우리로)은 주식 관련 말이 같이 있어야 이 종목 이야기로 본다
    if context and not _count(title + " " + summary, context):
        return "주식 맥락 없음" if _count(title + " " + summary, keywords) else "키워드 없음"
    if _count(title, keywords):
        return None
    hits = _count(summary, keywords)
    if (mode or NEWS_MATCH) == "any" and hits:
        return None
    if hits >= 2:
        return None
    return "요약에만 1번" if hits == 1 else "키워드 없음"


def fetch_naver(http: requests.Session, target: dict, aliases: dict | None = None) -> tuple[list[dict], int, list[tuple[str, str]]]:
    """(저장 대상 기사, 받은 전체 건수, 걸러낸 기사 [(이유, 제목)])."""
    cfg = (aliases or {}).get(target["symbol"], {})
    query = cfg.get("query") or target["name"]
    keywords = cfg.get("keywords") or [target["name"]]
    exclude = cfg.get("exclude") or []
    res = http.get(
        NAVER_NEWS_URL,
        params={"query": query, "display": int(cfg.get("display") or NAVER_DISPLAY),
                "sort": cfg.get("sort") or NEWS_SORT},
        headers={NAVER_ID_HEADER: get_env("NAVER_CLIENT_ID"), NAVER_SECRET_HEADER: get_env("NAVER_CLIENT_SECRET")},
        timeout=(10, REQUEST_TIMEOUT),
    )
    if res.status_code != 200:
        try:
            body = res.json()
            detail = f"errorCode={body.get('errorCode')} {body.get('errorMessage')}"
        except ValueError:
            detail = ""
        raise RuntimeError(f"네이버 뉴스 HTTP {res.status_code} {detail}".strip())

    items = res.json().get("items") or []
    out, dropped = [], []
    for it in items:
        title = clean_text(it.get("title"))
        summary = clean_text(it.get("description"))
        reason = match_article(title, summary, keywords, exclude, cfg.get("match"), cfg.get("context"))
        if reason:
            dropped.append((reason, title))
            continue
        url = (it.get("originallink") or it.get("link") or "").strip()
        published = _pub_kst(it.get("pubDate"))
        if not url or len(url) > URL_MAX or not title or published is None:
            continue
        out.append({
            "source": "NAVER", "title": title[:TITLE_MAX], "summary": summary or None, "url": url,
            "publisher": _publisher(url), "published_at": published, "dart_rcept_no": None,
        })
    return out, len(items), dropped


# ─────────────────────────────────────────
# DART 공시
# ─────────────────────────────────────────
def load_corp_codes(http: requests.Session) -> dict[str, str]:
    """종목코드(6자리) → corp_code. 하루 1회만 받아 JSON 으로 캐시한다."""
    cache = DART_CACHE_DIR / "dart_corp_codes.json"
    today = datetime.now(KST).date()
    if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime, KST).date() == today:
        return json.loads(cache.read_text(encoding="utf-8"))

    logger.info("DART corp_code 파일 다운로드 중 (하루 1회, 수 MB)…")
    started = time.monotonic()
    res = http.get(DART_CORP_CODE_URL, params={"crtfc_key": get_env("DART_API_KEY")},
                   timeout=(10, 30), stream=True)
    chunks = []
    for chunk in res.iter_content(64 * 1024):
        chunks.append(chunk)
        if time.monotonic() - started > CORP_CODE_DEADLINE:
            res.close()
            raise RuntimeError(f"DART corpCode 다운로드가 {CORP_CODE_DEADLINE}초를 넘어 중단 (서버→DART 네트워크 확인)")
    data = b"".join(chunks)
    logger.info("DART corp_code 다운로드 완료 (%.1f MB, %.0f초)", len(data) / 1e6, time.monotonic() - started)
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
        timeout=(10, REQUEST_TIMEOUT),
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
def print_items(target: dict, naver: list[dict], naver_total: int | None, dart: list[dict] | None,
                dropped: list[tuple[str, str]] | None = None) -> None:
    print(f"\n■ {target['name']} ({target['symbol']})")
    if naver_total is not None:
        print(f"  [네이버] 받은 {naver_total}건 중 남긴 {len(naver)}건 · 거른 {len(dropped or [])}건")
        for it in naver:
            print(f"   - {it['published_at']:%m-%d %H:%M} {it['publisher'] or '-'} | {it['title']}")
            print(f"     {it['url']}")
        if dropped:
            print("  [거른 기사]")
            for reason, title in dropped:
                print(f"   x ({reason}) {title[:60]}")
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
    targets_spec = args.targets or os.environ.get("STOCK_TARGETS")
    holdings_db = "-" if targets_spec else resolve_db_name(args.db, args.allow_prod)
    news_db = None if args.dry_run else resolve_db_name(
        args.news_db, args.allow_prod, env_key="STOCK_NEWS_DB", default="journal_db_dev")
    logger.info("시작: dry_run=%s holdings_db=%s news_db=%s symbol=%s",
                args.dry_run, holdings_db, news_db or "-", args.symbol or "전체")

    if targets_spec:
        targets = parse_targets(targets_spec, args.symbol)
    else:
        conn = get_connection(holdings_db)
        try:
            targets = load_targets(conn, args.symbol)
        finally:
            conn.close()
    if not targets:
        logger.warning("대상 KR 보유 종목이 없습니다%s.", f" (--symbol {args.symbol})" if args.symbol else "")
        return 0
    logger.info("대상 %d종목: %s", len(targets), ", ".join(t["symbol"] for t in targets))

    aliases = load_aliases()
    http = requests.Session()
    today = datetime.now(KST).date()
    corp_codes = None
    if not args.skip_dart:
        try:
            corp_codes = load_corp_codes(http)
        except Exception as e:
            logger.error("DART 매핑 실패 — 공시는 건너뜀: %s",
                         safe_error(e))

    news_conn = None if args.dry_run else get_connection(news_db)
    collected_at = now_kst()
    new_by_target: list[tuple[dict, list[dict]]] = []
    failed = 0
    try:
        for target in targets:
            logger.info("%s(%s) 수집 중…", target["name"], target["symbol"])
            naver, naver_total, dart, dropped = [], None, None, []
            try:
                if not args.skip_naver:
                    naver, naver_total, dropped = fetch_naver(http, target, aliases)
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
                detail = safe_error(e)
                logger.error("%s(%s) 수집 실패 — 건너뜀: %s", target["name"], target["symbol"], detail)
                continue

            items = _dedupe(naver + (dart or []))
            if args.dry_run:
                print_items(target, naver, naver_total, dart, dropped)
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
    parser.add_argument("--targets", help='DB 대신 대상 종목 직접 지정 "005930:삼성전자,000660:SK하이닉스" (.env STOCK_TARGETS)')
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
                  else safe_error(e))
        logger.error("실패: %s", detail)
        sys.exit(1)


if __name__ == "__main__":
    main()
