"""
토스증권 스냅샷 — 직전 스냅샷 대비 수량·손익 변화 계산 (LifeMetrics 종합 분석 "investing" 입력용)

비교 기준: 같은 계좌·같은 세션의 직전 스냅샷 (KR_CLOSE 는 KR_CLOSE 끼리, US_CLOSE 는 US_CLOSE 끼리).
주말·휴일로 전날 스냅샷이 없으면 그 이전 가장 가까운 날과 비교하고 prev_snapshot_date 에 날짜를 적는다.
US_CLOSE 의 snapshot_date 는 실행일(KST)이며, 직전 미국 거래일 장을 반영한다.

사용법 (읽기 전용):
    venv/bin/python -m invest.snapshot_diff                                  # 오늘 investing 입력 JSON
    venv/bin/python -m invest.snapshot_diff --date 2026-11-27 --session KR_CLOSE
    venv/bin/python -m invest.snapshot_diff --db riding_db --allow-prod
"""

import argparse
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from invest.db import resolve_db_name, get_connection

KST = ZoneInfo("Asia/Seoul")

ACCOUNT_DIFF_FIELDS = [
    "eval_krw", "eval_usd", "pnl_krw", "pnl_usd", "pnl_rate", "daily_pnl_krw", "daily_pnl_usd",
    "daily_pnl_rate", "purchase_krw", "purchase_usd", "holding_count",
]
ITEM_DIFF_FIELDS = ["quantity", "avg_price", "current_price", "eval_amount", "pnl", "pnl_rate"]


def _num(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(round(v, 6))
    return v


def _change(cur, prev) -> dict:
    out = {"current": _num(cur), "previous": _num(prev), "change": None}
    if cur is not None and prev is not None:
        out["change"] = _num(Decimal(str(cur)) - Decimal(str(prev)))
    return out


def _fetch_account(cur, snapshot_date, session, suffix, before=False):
    if before:
        cur.execute(
            "SELECT * FROM invest_account_snapshot WHERE account_suffix=%s AND session=%s AND snapshot_date < %s "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (suffix, session, snapshot_date),
        )
    else:
        cur.execute(
            "SELECT * FROM invest_account_snapshot WHERE account_suffix=%s AND session=%s AND snapshot_date = %s",
            (suffix, session, snapshot_date),
        )
    return cur.fetchone()


def _fetch_items(cur, account_snapshot_id) -> dict:
    cur.execute("SELECT * FROM invest_snapshot WHERE account_snapshot_id=%s", (account_snapshot_id,))
    return {(r["market"], r["symbol"]): r for r in cur.fetchall()}


TRADE_TYPES = ("BUY", "SELL", "SECURITIES_TRANSFER_IN", "SECURITIES_TRANSFER_OUT")
CASH_EVENT_TYPES = ("DIVIDEND", "INTEREST", "DEPOSIT", "WITHDRAWAL", "EXCHANGE_IN", "EXCHANGE_OUT", "FEE")


def _tx_dict(r) -> dict:
    return {
        "type": r["transaction_type"], "settled": bool(r["settled"]), "cancellation": bool(r["is_cancellation"]),
        "settlement_date": str(r["settlement_date"]), "market": r["market"], "currency": r["currency"],
        "symbol": r["symbol"], "quantity": _num(r["quantity"]), "filled_price": _num(r["filled_price"]),
        "amount": _num(r["amount"]), "net_amount": _num(r["net_amount"]),
    }


def _fetch_activity(cur, suffix, prev_date, snapshot_date) -> tuple[dict, list[dict]]:
    """
    (종목별 매매, 현금 이벤트) 를 돌려준다.
    - 매매: 미결제 전체 + (prev_date, snapshot_date] 에 결제된 매매. 국내는 T+2 결제라 당일 매매는 미결제로 잡힌다.
    - 현금 이벤트(배당·입출금·환전·이자·수수료): (prev_date, snapshot_date] 결제분.
    invest_transaction 은 최신 상태만 들고 있으므로(미결제는 매 실행 교체) 오늘자 분석에 맞춘 기준이다.
    """
    lower = prev_date or (snapshot_date - timedelta(days=7))
    try:
        cur.execute(
            "SELECT * FROM invest_transaction WHERE account_suffix=%s AND "
            "(settled=0 OR (settlement_date > %s AND settlement_date <= %s)) "
            "ORDER BY settlement_date, id",
            (suffix, lower, snapshot_date),
        )
    except Exception:  # 거래내역 테이블이 아직 없는 DB
        return {}, []
    trades: dict = {}
    events: list[dict] = []
    for r in cur.fetchall():
        if r["transaction_type"] in TRADE_TYPES and r["symbol"]:
            trades.setdefault((r["market"], r["symbol"]), []).append(_tx_dict(r))
        elif r["transaction_type"] in CASH_EVENT_TYPES and r["settled"]:
            events.append(_tx_dict(r))
    return trades, events


def compute_snapshot_diff(conn, snapshot_date: date, session: str, account_suffix: str | None = None) -> list[dict]:
    """
    snapshot_date·session 의 스냅샷을 직전 스냅샷과 비교한다. 계좌별 dict 목록을 돌려준다.

    반환 예:
    [{
      "account": "1234", "session": "KR_CLOSE", "snapshot_date": "2026-11-27", "prev_snapshot_date": "2026-11-26",
      "totals": {"eval_krw": {"current":..., "previous":..., "change":...}, ...},
      "holdings": [{"market":"KR","symbol":"005930","name":"삼성전자","currency":"KRW",
                    "status":"CHANGED|NEW|CLOSED|UNCHANGED",
                    "quantity": {...}, "eval_amount": {...}, "pnl": {...}, "pnl_rate": {...}, ...}],
      "summary": {"new": ["005930"], "closed": [...], "quantity_changed": [...]}
    }]
    """
    results = []
    with conn.cursor() as cur:
        if account_suffix:
            suffixes = [account_suffix]
        else:
            cur.execute(
                "SELECT DISTINCT account_suffix FROM invest_account_snapshot WHERE snapshot_date=%s AND session=%s",
                (snapshot_date, session),
            )
            suffixes = [r["account_suffix"] for r in cur.fetchall()]

        for suffix in suffixes:
            current = _fetch_account(cur, snapshot_date, session, suffix)
            if not current:
                continue
            prev = _fetch_account(cur, snapshot_date, session, suffix, before=True)
            cur_items = _fetch_items(cur, current["id"])
            prev_items = _fetch_items(cur, prev["id"]) if prev else {}
            trades, cash_events = _fetch_activity(cur, suffix, prev["snapshot_date"] if prev else None, snapshot_date)

            holdings, new, closed, qty_changed = [], [], [], []
            for key in sorted(set(cur_items) | set(prev_items)):
                c, p = cur_items.get(key), prev_items.get(key)
                base = c or p
                if not prev:
                    status = "NEW"  # 첫 스냅샷이면 비교 대상 없음
                elif c and not p:
                    status = "NEW"
                    new.append(key[1])
                elif p and not c:
                    status = "CLOSED"
                    closed.append(key[1])
                elif c["quantity"] != p["quantity"]:
                    status = "CHANGED"
                    qty_changed.append(key[1])
                else:
                    status = "UNCHANGED"
                entry = {
                    "market": base["market"], "symbol": base["symbol"], "name": base["name"],
                    "currency": base["currency"], "status": status,
                }
                for f in ITEM_DIFF_FIELDS:
                    entry[f] = _change(c[f] if c else (Decimal("0") if f in ("quantity", "eval_amount") else None),
                                       p[f] if p else None)
                entry["daily_pnl"] = _num(c["daily_pnl"]) if c else None
                entry["daily_pnl_rate"] = _num(c["daily_pnl_rate"]) if c else None
                entry["trades"] = trades.pop(key, [])
                holdings.append(entry)

            results.append({
                "account": suffix,
                "session": session,
                "snapshot_date": str(snapshot_date),
                "snapshot_at": current["snapshot_at"].isoformat() if current.get("snapshot_at") else None,
                "prev_snapshot_date": str(prev["snapshot_date"]) if prev else None,
                "totals": {f: _change(current[f], prev[f] if prev else None) for f in ACCOUNT_DIFF_FIELDS},
                "holdings": holdings,
                "summary": {"new": new, "closed": closed, "quantity_changed": qty_changed},
                # 보유 목록에 없는 종목의 매매(당일 사고 판 종목 등)
                "other_trades": [t for ts in trades.values() for t in ts],
                "cash_events": cash_events,
            })
    return results


def build_investing_input(conn, target_date: date) -> dict:
    """종합 분석용 묶음: target_date 의 KR_CLOSE·US_CLOSE 스냅샷 각각의 직전 대비 변화."""
    return {
        "date": str(target_date),
        "rate_unit": "ratio (0.0123 = 1.23%)",
        "kr_close": compute_snapshot_diff(conn, target_date, "KR_CLOSE"),
        "us_close": compute_snapshot_diff(conn, target_date, "US_CLOSE"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="토스 스냅샷 직전 대비 변화 (읽기 전용)")
    parser.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    parser.add_argument("--session", choices=("KR_CLOSE", "US_CLOSE"), help="미지정 시 두 세션 모두")
    parser.add_argument("--db", help="DB 이름 (기본 TOSS_SNAPSHOT_DB 또는 riding_db_dev)")
    parser.add_argument("--allow-prod", action="store_true")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    conn = get_connection(resolve_db_name(args.db, args.allow_prod))
    try:
        if args.session:
            data = compute_snapshot_diff(conn, target, args.session)
        else:
            data = build_investing_input(conn, target)
    finally:
        conn.close()
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
