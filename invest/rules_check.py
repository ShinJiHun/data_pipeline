"""
내 투자 규칙 점검 — 보유 종목이 미리 정한 규칙(목표 비중 · 추가 매수 조건 · 쉬는 조건)에 걸리는지 매일 확인한다.

판단(사라/팔아라)은 하지 않는다. 내가 정한 규칙의 조건이 맞는지만 계산해서 보여 준다.

규칙 파일: invest/invest_rules.json (git 에 올리지 않음, 형식은 invest_rules.example.json)
  - targets           종목코드별 목표 비중(0.40 = 40%). "CASH" 는 현금 목표 비중
  - add_rules         drop_from_avg_step(평단 대비 하락 단계, 0.10 = -10%마다), drop_from_52w_high(52주 고점 대비, 0.20)
  - pause_rules       monthly_buy_limit_krw(이번 달 매수 한도, 원), over_target_pause(목표 비중 이상이면 추가 매수 쉼)
  - cash_krw          현금(원). DB 에 현금이 없어서 지금은 직접 적는다. null 이면 현금 없이 비중 계산
  - usd_krw           달러 종목 원화 환산 환율

사용법 (저장소 루트에서):
    python -m invest.rules_check --holdings-file invest/fixtures/holdings_sample.json   # DB 없이 (로컬 확인)
    venv/bin/python -m invest.rules_check                                               # DB 최신 스냅샷 기준 (riding_db_dev)
    venv/bin/python -m invest.rules_check --db riding_db --allow-prod --notify --json   # 운영 + Slack + JSON 출력

보유 파일 형식(--holdings-file):
    {"holdings": [{"market","symbol","name","currency","quantity","avg_price","current_price","eval_amount",
                   "high_52w"(선택)}],
     "month_buys": [{"currency","amount"}]}   # 이번 달 매수 (선택)
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
LOG_DIR = Path(os.environ.get("INVEST_LOG_DIR", "/mnt/200gb/logs/invest"))
RULES_FILE = Path(os.environ.get("INVEST_RULES_FILE", str(Path(__file__).with_name("invest_rules.json"))))
logger = logging.getLogger("rules_check")

BUY, PAUSED, WAIT = "BUY_SIGNAL", "PAUSED", "WAIT"
STATUS_ICON = {BUY: "✅", PAUSED: "⚠️", WAIT: "⏸"}


def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.insert(0, logging.FileHandler(LOG_DIR / "rules_check.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


def _f(v) -> float | None:
    return None if v is None else float(v)


# ─────────────────────────────────────────
# 계산 (DB·API 와 무관한 순수 로직)
# ─────────────────────────────────────────
@dataclass
class Check:
    symbol: str
    name: str
    status: str
    weight: float | None = None
    target: float | None = None
    drop_from_avg: float | None = None
    drop_from_high: float | None = None
    met: list[str] = field(default_factory=list)       # 충족한 추가 매수 조건
    paused_by: list[str] = field(default_factory=list)  # 쉬는 이유
    missing: list[str] = field(default_factory=list)    # 데이터가 없어 못 본 조건


def evaluate(rules: dict, holdings: list[dict], month_buys: list[dict]) -> dict:
    usd_krw = float(rules.get("usd_krw") or 0) or None
    cash = _f(rules.get("cash_krw"))
    add = rules.get("add_rules") or {}
    pause = rules.get("pause_rules") or {}
    targets = {k: float(v) for k, v in (rules.get("targets") or {}).items()}

    def to_krw(amount: float | None, currency: str) -> float | None:
        if amount is None:
            return None
        if currency == "KRW":
            return amount
        return amount * usd_krw if usd_krw else None

    evals = {h["symbol"]: to_krw(_f(h.get("eval_amount")), h.get("currency", "KRW")) for h in holdings}
    total = sum(v for v in evals.values() if v is not None) + (cash or 0)

    month_spent = sum(to_krw(_f(b.get("amount")), b.get("currency", "KRW")) or 0 for b in month_buys)
    limit = _f(pause.get("monthly_buy_limit_krw"))
    month_paused = limit is not None and month_spent >= limit

    checks = []
    for h in holdings:
        sym, cur = h["symbol"], h.get("currency", "KRW")
        c = Check(symbol=sym, name=h.get("name") or sym, status=WAIT, target=targets.get(sym))
        ev = evals.get(sym)
        c.weight = ev / total if (ev is not None and total) else None

        price, avg, high = _f(h.get("current_price")), _f(h.get("avg_price")), _f(h.get("high_52w"))
        step = _f(add.get("drop_from_avg_step"))
        if step and price and avg:
            c.drop_from_avg = price / avg - 1
            level = int(-c.drop_from_avg // step) if c.drop_from_avg < 0 else 0
            if level >= 1:
                c.met.append(f"평단 대비 {c.drop_from_avg:+.1%} ({level}단계, {step:.0%}마다)")
        elif step:
            c.missing.append("평단·현재가")

        drop_high = _f(add.get("drop_from_52w_high"))
        if drop_high and price and high:
            c.drop_from_high = price / high - 1
            if c.drop_from_high <= -drop_high:
                c.met.append(f"52주 고점 대비 {c.drop_from_high:+.1%} (기준 -{drop_high:.0%})")
        elif drop_high:
            c.missing.append("52주 고점")

        if pause.get("over_target_pause", True) and c.target is not None and c.weight is not None \
                and c.weight >= c.target:
            c.paused_by.append(f"비중 {c.weight:.1%} ≥ 목표 {c.target:.0%}")
        if c.target is None:
            c.paused_by.append("목표 비중 없음(규칙 파일에 없는 종목)")
        if month_paused:
            c.paused_by.append(f"이번 달 매수 {month_spent:,.0f}원 ≥ 한도 {limit:,.0f}원")

        if c.met:
            c.status = PAUSED if c.paused_by else BUY
        checks.append(c)

    cash_weight = cash / total if (cash is not None and total) else None
    return {
        "date": datetime.now(KST).date().isoformat(),
        "total_krw": round(total),
        "cash_krw": cash,
        "cash_weight": cash_weight,
        "cash_target": targets.get("CASH"),
        "month_buy_krw": round(month_spent),
        "month_buy_limit_krw": limit,
        "checks": [asdict(c) for c in checks],
    }


def render(result: dict) -> str:
    lines = [f"📌 {result['date']} 내 규칙 점검 (총 {result['total_krw']:,}원)"]
    for c in result["checks"]:
        w = f"{c['weight']:.1%}" if c["weight"] is not None else "-"
        t = f"{c['target']:.0%}" if c["target"] is not None else "-"
        head = f"{STATUS_ICON[c['status']]} {c['name']} ({c['symbol']}) 비중 {w} / 목표 {t}"
        if c["drop_from_avg"] is not None:
            head += f" · 평단 대비 {c['drop_from_avg']:+.1%}"
        lines.append(head)
        for m in c["met"]:
            lines.append(f"    조건 충족: {m}")
        for p in c["paused_by"]:
            lines.append(f"    쉼: {p}")
        if c["missing"]:
            lines.append(f"    데이터 없음: {', '.join(c['missing'])}")
    if result["cash_weight"] is not None:
        ct = f" / 목표 {result['cash_target']:.0%}" if result["cash_target"] is not None else ""
        lines.append(f"💵 현금 {result['cash_weight']:.1%}{ct}")
    if result["month_buy_limit_krw"] is not None:
        lines.append(f"🧾 이번 달 매수 {result['month_buy_krw']:,}원 / 한도 {result['month_buy_limit_krw']:,.0f}원")
    lines.append("※ 규칙 조건 계산일 뿐, 매수 권유가 아니에요.")
    return "\n".join(lines)


# ─────────────────────────────────────────
# 입력 (규칙 파일 · 보유 종목)
# ─────────────────────────────────────────
def load_rules(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"규칙 파일이 없습니다: {path} (invest_rules.example.json 을 복사해서 만드세요)")
    return json.loads(path.read_text(encoding="utf-8"))


def load_from_file(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("holdings") or [], data.get("month_buys") or []


def load_from_db(db_name: str) -> tuple[list[dict], list[dict]]:
    from invest.db import get_connection  # DB 없이 돌릴 때 pymysql 이 없어도 되게 여기서 import

    month_start = date.today().replace(day=1)
    conn = get_connection(db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.market, s.symbol, s.name, s.currency, s.quantity, s.avg_price, s.current_price, "
                "       s.eval_amount "
                "FROM invest_snapshot s "
                "JOIN (SELECT account_suffix, MAX(snapshot_at) AS max_at FROM invest_account_snapshot "
                "      GROUP BY account_suffix) latest "
                "  ON latest.account_suffix = s.account_suffix AND latest.max_at = s.snapshot_at "
                "ORDER BY s.market, s.eval_amount DESC"
            )
            holdings = list(cur.fetchall())
            cur.execute(
                "SELECT currency, COALESCE(net_amount, amount) AS amount FROM invest_transaction "
                "WHERE transaction_type = 'BUY' AND is_cancellation = 0 AND settlement_date >= %s",
                (month_start,),
            )
            month_buys = list(cur.fetchall())
    finally:
        conn.close()
    # 같은 종목을 여러 계좌에 가진 경우 합친다
    merged: dict[str, dict] = {}
    for h in holdings:
        m = merged.get(h["symbol"])
        if not m:
            merged[h["symbol"]] = dict(h)
            continue
        q1, q2 = float(m["quantity"]), float(h["quantity"])
        if m.get("avg_price") is not None and h.get("avg_price") is not None and q1 + q2:
            m["avg_price"] = (float(m["avg_price"]) * q1 + float(h["avg_price"]) * q2) / (q1 + q2)
        m["quantity"] = q1 + q2
        m["eval_amount"] = float(m["eval_amount"] or 0) + float(h["eval_amount"] or 0)
    return list(merged.values()), month_buys


def run(args) -> int:
    rules = load_rules(Path(args.rules) if args.rules else RULES_FILE)
    if args.holdings_file:
        holdings, month_buys = load_from_file(Path(args.holdings_file))
        source = args.holdings_file
    else:
        from invest.db import resolve_db_name
        source = resolve_db_name(args.db, args.allow_prod)
        holdings, month_buys = load_from_db(source)
    logger.info("보유 %d종목 (%s)", len(holdings), source)
    if not holdings:
        logger.warning("보유 종목이 없습니다.")
        return 0

    result = evaluate(rules, holdings, month_buys)
    text = render(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str) if args.json else text)
    if args.notify:
        from invest.notify import notify_slack
        notify_slack(text)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="내 투자 규칙 점검 (조건 계산만, 매매 없음)")
    parser.add_argument("--rules", help="규칙 파일 경로 (기본 invest/invest_rules.json 또는 INVEST_RULES_FILE)")
    parser.add_argument("--holdings-file", help="DB 대신 보유 종목 JSON 파일 (로컬 확인용)")
    parser.add_argument("--db", help="보유 종목을 읽을 DB (기본 TOSS_SNAPSHOT_DB 또는 riding_db_dev)")
    parser.add_argument("--allow-prod", action="store_true", help="*_dev 가 아닌 DB 사용 허용")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력 (LifeMetrics 연동용)")
    parser.add_argument("--notify", action="store_true", help="결과를 Slack 으로 보냄")
    args = parser.parse_args()
    setup_logging()
    try:
        sys.exit(run(args))
    except Exception as e:
        logger.error("실패: %s", str(e) if isinstance(e, (RuntimeError, ValueError)) else type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
