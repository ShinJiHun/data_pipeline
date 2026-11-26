"""
토스증권 Open API → 계좌·보유 종목 스냅샷 적재 (조회 전용)

사용법 (저장소 루트에서):
    venv/bin/python -m invest.toss_snapshot --dry-run                # API 조회만, DB 저장 없이 요약 출력
    venv/bin/python -m invest.toss_snapshot                          # riding_db_dev 에 저장 (세션 자동 판별)
    venv/bin/python -m invest.toss_snapshot --session US_CLOSE       # 세션 지정
    venv/bin/python -m invest.toss_snapshot --db riding_db --allow-prod   # 운영 DB (관리자 확인 후에만)
    venv/bin/python -m invest.toss_snapshot --tx-from 2026-01-01     # 거래내역 과거분 백필 (처음 1회)

거래내역: 매 실행 시 결제일 최근 10일 + 미결제 전체를 받아 그 범위를 교체 저장(invest_transaction).

필요 환경변수(.env):
    TOSS_CLIENT_ID, TOSS_CLIENT_SECRET
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD   (DB 이름은 --db / TOSS_SNAPSHOT_DB / 기본 riding_db_dev)
    SLACK_WEBHOOK_URL (선택, 실패 알림)

API 명세: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (v1.2.28 기준)
    POST /oauth2/token      OAuth2 client credentials (form-urlencoded)
    GET  /api/v1/accounts   계좌 목록 → accountSeq
    GET  /api/v1/holdings   보유 주식 (헤더 X-Tossinvest-Account: accountSeq)
    GET  /api/v1/transactions  거래내역 (매매·배당·입출금 등, 커서 페이지네이션)

안전 규칙:
- 조회 전용. 주문·정정·취소 등 다른 엔드포인트는 이 코드에 없다. GET 은 ALLOWED_GET_PATHS 만 허용.
- 키·토큰·계좌번호(accountNo)는 로그·출력·DB 어디에도 남기지 않는다. 계좌는 accountSeq 끝 4자리로만 표기.
- 토큰은 client 당 1개만 유효(재발급 시 이전 토큰 즉시 무효)하므로 실행당 1회 발급해 끝까지 쓴다.
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql
import requests

from invest.db import get_env, resolve_db_name, get_connection
from invest.notify import notify_slack

KST = ZoneInfo("Asia/Seoul")
LOG_DIR = Path("/mnt/200gb/logs/invest")
logger = logging.getLogger("toss_snapshot")

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
ACCOUNTS_PATH = "/api/v1/accounts"
HOLDINGS_PATH = "/api/v1/holdings"
TRANSACTIONS_PATH = "/api/v1/transactions"
# 조회 전용 허용 목록. 여기에 없는 경로는 호출하지 않는다.
ALLOWED_GET_PATHS = frozenset({ACCOUNTS_PATH, HOLDINGS_PATH, TRANSACTIONS_PATH})

REQUEST_TIMEOUT = 15
MAX_RATE_LIMIT_RETRIES = 3
TX_PAGE_LIMIT = 100          # API 최대
TX_MAX_PAGES = 50            # 안전장치 (최대 5,000건)
TX_DEFAULT_DAYS = 10         # 매 실행 시 다시 받는 결제일 구간 (휴일·결제 지연 여유)
SESSIONS = ("KR_CLOSE", "US_CLOSE")

DB_ERROR_HINTS = {
    1044: "DB_USER 에 이 DB 권한 없음 — GRANT 필요",
    1045: "DB_USER/DB_PASSWORD 인증 실패",
    1049: "DB 가 없음 — DB 이름·DB_HOST 확인",
    1146: "테이블 없음 — invest/schema.sql 을 이 DB 에 적용",
    2003: "DB_HOST/DB_PORT 접속 불가",
    2005: "DB_HOST 이름을 찾을 수 없음",
}


# ─────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────
def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(LOG_DIR / "toss_snapshot.log", encoding="utf-8"))
    except OSError:
        pass  # 로그 폴더를 못 만들면 콘솔만
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


# ─────────────────────────────────────────
# API 클라이언트 (조회 전용)
# ─────────────────────────────────────────
class TossApiError(Exception):
    """메시지에는 HTTP 상태·에러 code·message·requestId 만 담는다 (비밀값 없음)."""


def _error_summary(res: requests.Response) -> str:
    try:
        body = res.json()
    except ValueError:
        return f"HTTP {res.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):  # BFF 공통 에러 envelope
        return f"HTTP {res.status_code} code={err.get('code')} message={err.get('message')} requestId={err.get('requestId')}"
    if isinstance(err, str):  # OAuth2 표준 에러 (/oauth2/token)
        return f"HTTP {res.status_code} error={err} description={body.get('error_description')}"
    return f"HTTP {res.status_code}"


def _error_code(res: requests.Response) -> str | None:
    try:
        err = res.json().get("error")
        return err.get("code") if isinstance(err, dict) else None
    except (ValueError, AttributeError):
        return None


class TossReadOnlyClient:
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._http = requests.Session()

    def __repr__(self) -> str:  # 실수로 출력돼도 비밀값이 안 보이게
        return "TossReadOnlyClient(<redacted>)"

    def _issue_token(self) -> None:
        res = self._http.post(
            BASE_URL + TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        if res.status_code != 200:
            hint = " (허용 IP 미등록 — WTS 설정 > Open API > 허용 IP 관리)" if res.status_code == 403 else ""
            raise TossApiError(f"토큰 발급 실패: {_error_summary(res)}{hint}")
        token = res.json().get("access_token")
        if not token:
            raise TossApiError("토큰 발급 응답에 access_token 이 없습니다.")
        self._token = token
        logger.info("토큰 발급 완료")

    def _get(self, path: str, account_seq: int | None = None, params: dict | None = None):
        if path not in ALLOWED_GET_PATHS:
            raise ValueError(f"허용되지 않은 경로: {path}")
        if self._token is None:
            self._issue_token()

        reissued = False
        rate_retries = 0
        server_retried = False
        while True:
            headers = {"Authorization": f"Bearer {self._token}"}
            if account_seq is not None:
                headers["X-Tossinvest-Account"] = str(account_seq)
            res = self._http.get(BASE_URL + path, headers=headers, params=params, timeout=REQUEST_TIMEOUT)

            if res.status_code == 200:
                return res.json().get("result")

            if res.status_code == 401 and not reissued and _error_code(res) in ("expired-token", "token-revoked"):
                logger.warning("토큰 무효(%s) — 1회 재발급 후 재시도", _error_code(res))
                reissued = True
                self._issue_token()
                continue

            if res.status_code == 429 and rate_retries < MAX_RATE_LIMIT_RETRIES:
                rate_retries += 1
                try:
                    wait = min(int(res.headers.get("Retry-After", "1")), 10)
                except ValueError:
                    wait = 1
                logger.warning("요청 한도 초과 — %ss 후 재시도 (%d/%d)", wait, rate_retries, MAX_RATE_LIMIT_RETRIES)
                time.sleep(max(wait, 1))
                continue

            if res.status_code >= 500 and not server_retried:
                server_retried = True
                logger.warning("서버 일시 오류(%s) — 3s 후 1회 재시도", _error_summary(res))
                time.sleep(3)
                continue

            raise TossApiError(f"{path} 조회 실패: {_error_summary(res)}")

    def get_accounts(self) -> list[dict]:
        return self._get(ACCOUNTS_PATH) or []

    def get_holdings(self, account_seq: int) -> dict:
        return self._get(HOLDINGS_PATH, account_seq=account_seq) or {}

    def get_transactions(self, account_seq: int, from_date: date) -> list[dict]:
        """from_date(결제일) 이후 결제분 + 미결제 전체. `to` 를 생략해야 미결제가 포함된다."""
        out: list[dict] = []
        cursor = None
        for _ in range(TX_MAX_PAGES):
            params = {"from": from_date.isoformat(), "limit": TX_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            page = self._get(TRANSACTIONS_PATH, account_seq=account_seq, params=params) or {}
            out.extend(page.get("transactions") or [])
            if not page.get("hasNext") or not page.get("nextCursor"):
                return out
            cursor = page["nextCursor"]
        raise TossApiError(f"거래내역 페이지가 {TX_MAX_PAGES}쪽을 넘습니다. --tx-from 구간을 줄이세요.")


# ─────────────────────────────────────────
# 응답 → 행 변환
# ─────────────────────────────────────────
def dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _dig(obj: dict, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def account_suffix(account_seq) -> str:
    return str(account_seq)[-4:]


def parse_holdings(overview: dict) -> tuple[dict, list[dict]]:
    """HoldingsOverview → (계좌 합계 행, 종목 행 목록). 키 이름은 명세의 스키마 그대로."""
    items = overview.get("items") or []
    account = {
        "purchase_krw": dec(_dig(overview, "totalPurchaseAmount", "krw")),
        "purchase_usd": dec(_dig(overview, "totalPurchaseAmount", "usd")),
        "eval_krw": dec(_dig(overview, "marketValue", "amount", "krw")),
        "eval_usd": dec(_dig(overview, "marketValue", "amount", "usd")),
        "eval_after_cost_krw": dec(_dig(overview, "marketValue", "amountAfterCost", "krw")),
        "eval_after_cost_usd": dec(_dig(overview, "marketValue", "amountAfterCost", "usd")),
        "pnl_krw": dec(_dig(overview, "profitLoss", "amount", "krw")),
        "pnl_usd": dec(_dig(overview, "profitLoss", "amount", "usd")),
        "pnl_after_cost_krw": dec(_dig(overview, "profitLoss", "amountAfterCost", "krw")),
        "pnl_after_cost_usd": dec(_dig(overview, "profitLoss", "amountAfterCost", "usd")),
        "pnl_rate": dec(_dig(overview, "profitLoss", "rate")),
        "pnl_rate_after_cost": dec(_dig(overview, "profitLoss", "rateAfterCost")),
        "daily_pnl_krw": dec(_dig(overview, "dailyProfitLoss", "amount", "krw")),
        "daily_pnl_usd": dec(_dig(overview, "dailyProfitLoss", "amount", "usd")),
        "daily_pnl_rate": dec(_dig(overview, "dailyProfitLoss", "rate")),
        "holding_count": len(items),
    }
    rows = []
    for it in items:
        rows.append({
            "market": it.get("marketCountry") or "",
            "symbol": it.get("symbol"),
            "name": it.get("name"),
            "currency": it.get("currency") or "",
            "quantity": dec(it.get("quantity")) or Decimal("0"),
            "avg_price": dec(it.get("averagePurchasePrice")),
            "current_price": dec(it.get("lastPrice")),
            "purchase_amount": dec(_dig(it, "marketValue", "purchaseAmount")),
            "eval_amount": dec(_dig(it, "marketValue", "amount")),
            "eval_after_cost": dec(_dig(it, "marketValue", "amountAfterCost")),
            "pnl": dec(_dig(it, "profitLoss", "amount")),
            "pnl_after_cost": dec(_dig(it, "profitLoss", "amountAfterCost")),
            "pnl_rate": dec(_dig(it, "profitLoss", "rate")),
            "pnl_rate_after_cost": dec(_dig(it, "profitLoss", "rateAfterCost")),
            "daily_pnl": dec(_dig(it, "dailyProfitLoss", "amount")),
            "daily_pnl_rate": dec(_dig(it, "dailyProfitLoss", "rate")),
            "commission": dec(_dig(it, "cost", "commission")),
            "tax": dec(_dig(it, "cost", "tax")),
        })
    return account, rows


# ─────────────────────────────────────────
# DB 저장
# ─────────────────────────────────────────
ACCOUNT_COLS = [
    "purchase_krw", "purchase_usd", "eval_krw", "eval_usd", "eval_after_cost_krw", "eval_after_cost_usd",
    "pnl_krw", "pnl_usd", "pnl_after_cost_krw", "pnl_after_cost_usd", "pnl_rate", "pnl_rate_after_cost",
    "daily_pnl_krw", "daily_pnl_usd", "daily_pnl_rate", "holding_count",
]
ITEM_COLS = [
    "market", "symbol", "name", "currency", "quantity", "avg_price", "current_price",
    "purchase_amount", "eval_amount", "eval_after_cost", "pnl", "pnl_after_cost", "pnl_rate",
    "pnl_rate_after_cost", "daily_pnl", "daily_pnl_rate", "commission", "tax",
]


def save_snapshot(conn, snapshot_at: datetime, session: str, suffix: str, account: dict, rows: list[dict]) -> int:
    """같은 (날짜, 세션, 계좌) 재실행 시 합계는 갱신, 종목은 통째로 교체한다."""
    snapshot_date = snapshot_at.date()
    key_cols = ["snapshot_at", "snapshot_date", "session", "account_suffix"]
    cols = key_cols + ACCOUNT_COLS
    values = [snapshot_at, snapshot_date, session, suffix] + [account[c] for c in ACCOUNT_COLS]
    update = ", ".join(f"{c}=VALUES({c})" for c in ["snapshot_at"] + ACCOUNT_COLS)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO invest_account_snapshot ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))}) ON DUPLICATE KEY UPDATE {update}",
            values,
        )
        cur.execute(
            "SELECT id FROM invest_account_snapshot WHERE snapshot_date=%s AND session=%s AND account_suffix=%s",
            (snapshot_date, session, suffix),
        )
        acct_id = cur.fetchone()["id"]
        cur.execute("DELETE FROM invest_snapshot WHERE account_snapshot_id=%s", (acct_id,))
        if rows:
            item_cols = ["account_snapshot_id"] + key_cols + ITEM_COLS
            cur.executemany(
                f"INSERT INTO invest_snapshot ({', '.join(item_cols)}) VALUES ({', '.join(['%s'] * len(item_cols))})",
                [[acct_id, snapshot_at, snapshot_date, session, suffix] + [r[c] for c in ITEM_COLS] for r in rows],
            )
    return acct_id


TX_COLS = [
    "settlement_date", "settled", "transaction_time", "transaction_type", "is_cancellation", "market",
    "currency", "asset_class", "symbol", "isin_code", "quantity", "filled_price", "amount", "commission",
    "tax", "net_amount", "exchange_rate",
]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(KST).replace(tzinfo=None)
    except ValueError:
        return None


def parse_transactions(txs: list[dict]) -> list[dict]:
    rows = []
    for t in txs:
        rows.append({
            "settlement_date": date.fromisoformat(t["settlementDate"]),
            "settled": 1 if t.get("settled") else 0,
            "transaction_time": _parse_dt(t.get("transactionTime")),
            "transaction_type": t.get("transactionType") or "",
            "is_cancellation": 1 if t.get("isCancellation") else 0,
            "market": t.get("marketCountry") or "",
            "currency": t.get("currency") or "",
            "asset_class": t.get("assetClass"),
            "symbol": t.get("symbol"),
            "isin_code": t.get("isinCode"),
            "quantity": dec(t.get("quantity")),
            "filled_price": dec(t.get("filledPrice")),
            "amount": dec(t.get("amount")),
            "commission": dec(t.get("commission")),
            "tax": dec(t.get("tax")),
            "net_amount": dec(t.get("netAmount")),
            "exchange_rate": dec(t.get("exchangeRate")),
        })
    return rows


def save_transactions(conn, fetched_at: datetime, suffix: str, from_date: date, rows: list[dict]) -> None:
    """API 에 거래 ID 가 없어, 조회 범위(미결제 전체 + from_date 이후 결제분)를 지우고 다시 넣는다."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM invest_transaction WHERE account_suffix=%s AND (settled=0 OR settlement_date >= %s)",
            (suffix, from_date),
        )
        if rows:
            cols = ["account_suffix", "fetched_at"] + TX_COLS
            cur.executemany(
                f"INSERT INTO invest_transaction ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                [[suffix, fetched_at] + [r[c] for c in TX_COLS] for r in rows],
            )


# ─────────────────────────────────────────
# 출력
# ─────────────────────────────────────────
def print_transactions(suffix: str, from_date: date, rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["transaction_type"]] = counts.get(r["transaction_type"], 0) + 1
    unsettled = sum(1 for r in rows if not r["settled"])
    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "없음"
    print(f"\n  거래내역(****{suffix}, 결제일 {from_date}~ + 미결제 {unsettled}건): {summary}")
    trades = [r for r in rows if r["transaction_type"] in ("BUY", "SELL", "DIVIDEND")][:10]
    for r in trades:
        d = 0 if r["currency"] == "KRW" else 2
        flag = "미결제" if not r["settled"] else "결제"
        cancel = " (취소)" if r["is_cancellation"] else ""
        qty = "-" if r["quantity"] is None else _fmt(r["quantity"], 4).rstrip("0").rstrip(".")
        print(f"  - {r['settlement_date']} {flag:3} {r['transaction_type']:<8}{cancel} {r['market']:2} "
              f"{(r['symbol'] or '-'):<8} 수량 {qty:>8}  금액 {_fmt(r['amount'], d):>14} {r['currency']}")


def _fmt(v, digits=0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:+.2f}%"


def print_summary(session: str, suffix: str, account: dict, rows: list[dict]) -> None:
    print(f"\n[{session}] 계좌 ****{suffix} — 보유 {account['holding_count']}종목")
    print(f"  평가금액  KRW {_fmt(account['eval_krw'])} / USD {_fmt(account['eval_usd'], 2)}")
    print(f"  손익      KRW {_fmt(account['pnl_krw'])} / USD {_fmt(account['pnl_usd'], 2)}  "
          f"(손익률 {_pct(account['pnl_rate'])}, 일간 {_pct(account['daily_pnl_rate'])})")
    for r in sorted(rows, key=lambda x: (x["market"], x["symbol"] or "")):
        d = 0 if r["currency"] == "KRW" else 2
        print(f"  - {r['market']:2} {r['symbol']:<8} {(r['name'] or '')[:16]:<16} "
              f"수량 {_fmt(r['quantity'], 4).rstrip('0').rstrip('.'):>10}  "
              f"평가 {_fmt(r['eval_amount'], d):>14} {r['currency']}  손익 {_fmt(r['pnl'], d):>12} ({_pct(r['pnl_rate'])})")


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def detect_session(now_kst: datetime) -> str:
    # 07:10 실행 → 미국 마감 후, 16:10 실행 → 국내 마감 후
    return "US_CLOSE" if now_kst.hour < 12 else "KR_CLOSE"


def run(args) -> int:
    now_kst = datetime.now(KST).replace(microsecond=0, tzinfo=None)
    session = args.session or detect_session(datetime.now(KST))
    db_name = None if args.dry_run else resolve_db_name(args.db, args.allow_prod)
    logger.info("시작: session=%s dry_run=%s db=%s", session, args.dry_run, db_name or "-")

    client = TossReadOnlyClient(get_env("TOSS_CLIENT_ID"), get_env("TOSS_CLIENT_SECRET"))
    accounts = client.get_accounts()
    logger.info("계좌 %d개 조회", len(accounts))
    if not accounts:
        logger.warning("조회 가능한 계좌가 없습니다.")
        return 0

    tx_from = (date.fromisoformat(args.tx_from) if args.tx_from
               else now_kst.date() - timedelta(days=TX_DEFAULT_DAYS))

    snapshots = []
    for acc in accounts:
        seq = acc.get("accountSeq")  # accountNo 는 읽지도 저장하지도 않는다
        if seq is None:
            continue
        suffix = account_suffix(seq)
        overview = client.get_holdings(seq)
        account, rows = parse_holdings(overview)
        logger.info("계좌 ****%s: 보유 %d종목", suffix, len(rows))
        tx_rows = None
        if not args.no_transactions:
            tx_rows = parse_transactions(client.get_transactions(seq, tx_from))
            logger.info("계좌 ****%s: 거래내역 %d건 (결제일 %s~ + 미결제)", suffix, len(tx_rows), tx_from)
        snapshots.append((suffix, account, rows, tx_rows))

    if args.dry_run:
        for suffix, account, rows, tx_rows in snapshots:
            print_summary(session, suffix, account, rows)
            if tx_rows is not None:
                print_transactions(suffix, tx_from, tx_rows)
        print("\n(dry-run: DB 저장 안 함)")
        return 0

    conn = get_connection(db_name)
    try:
        for suffix, account, rows, tx_rows in snapshots:
            acct_id = save_snapshot(conn, now_kst, session, suffix, account, rows)
            logger.info("저장 완료: ****%s account_snapshot_id=%s 종목 %d", suffix, acct_id, len(rows))
            if tx_rows is not None:
                save_transactions(conn, now_kst, suffix, tx_from, tx_rows)
                logger.info("거래내역 저장: ****%s %d건", suffix, len(tx_rows))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="토스증권 계좌 스냅샷 수집 (조회 전용)")
    parser.add_argument("--dry-run", action="store_true", help="DB 저장 없이 조회 결과 요약만 출력")
    parser.add_argument("--session", choices=SESSIONS, help="미지정 시 실행 시각으로 판별 (12시 전 US_CLOSE)")
    parser.add_argument("--db", help="저장할 DB 이름 (기본 TOSS_SNAPSHOT_DB 또는 riding_db_dev)")
    parser.add_argument("--allow-prod", action="store_true", help="*_dev 가 아닌 DB 에 저장 허용")
    parser.add_argument("--tx-from", help=f"거래내역 결제일 시작 YYYY-MM-DD (기본 {TX_DEFAULT_DAYS}일 전, 처음 1회는 과거부터 백필)")
    parser.add_argument("--no-transactions", action="store_true", help="거래내역 조회 생략")
    args = parser.parse_args()

    setup_logging()
    try:
        sys.exit(run(args))
    except Exception as e:
        # TossApiError 메시지는 code/message/requestId 만 담는다. DB 오류는 에러 번호와 힌트만,
        # 그 밖의 예외는 타입만 알린다 (접속 정보가 메시지에 섞일 수 있어서).
        if isinstance(e, pymysql.err.MySQLError):
            code = e.args[0] if e.args else None
            detail = f"DB 오류 {type(e).__name__} code={code} ({DB_ERROR_HINTS.get(code, '서버에서 직접 접속해 확인')})"
        elif isinstance(e, (TossApiError, EnvironmentError, RuntimeError)):
            detail = str(e)
        else:
            detail = type(e).__name__
        logger.error("실패: %s", detail)
        if not args.dry_run:
            notify_slack(f":warning: 토스 계좌 스냅샷 실패 ({os.uname().nodename}): {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
