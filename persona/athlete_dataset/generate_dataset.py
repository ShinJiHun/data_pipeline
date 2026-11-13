"""
애슐리트 페르소나 파인튜닝 데이터셋 생성 (A안: 컨텍스트·답변은 LifeMetrics 백엔드가 만든다)

  generate : 날짜 × 질문 × 모드 조합으로 백엔드 /api/athlete/dataset/sample 을 호출해
             data/athlete_dataset/raw_<mode>.jsonl 에 한 줄씩 쌓는다. 이미 있는 조합은 건너뛴다(재실행 안전).
  export   : raw_*.jsonl 을 학습용 messages 형식 train.jsonl / val.jsonl 로 변환한다.
             raw 에서 "skip": true 인 줄은 빼고, "answer" 를 손으로 고쳤으면 고친 값을 쓴다.

환경변수(.env): LIFEMETRICS_API_BASE, ATHLETE_DATASET_TOKEN
자세한 내용은 같은 폴더 README.md 참고.
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
OUT_DIR = REPO_ROOT / "data" / "athlete_dataset"  # data/ 는 .gitignore 대상
MODES = ("public", "admin")
ENDPOINT = "/api/athlete/dataset/sample"
TIMEOUT = 180
MAX_RETRY = 3


def load_questions(mode):
    path = HERE / f"questions_{mode}.txt"
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        q = line.strip()
        if not q or q.startswith("#") or q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def pick_dates(start, end, n):
    """start~end 사이에서 n 개 날짜를 고르게(양 끝 포함) 고른다."""
    if end < start:
        raise SystemExit("--end 가 --start 보다 빠릅니다.")
    span = (end - start).days
    if n <= 1 or span == 0:
        return [end]
    n = min(n, span + 1)
    step = span / (n - 1)
    return sorted({start + timedelta(days=round(i * step)) for i in range(n)})


def sample_key(mode, d, question):
    return hashlib.sha1(f"{mode}|{d}|{question}".encode("utf-8")).hexdigest()[:16]


def load_done_keys(path):
    keys = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    keys.add(json.loads(line)["key"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return keys


def call_sample(session, base, token, mode, d, question):
    url = base.rstrip("/") + ENDPOINT
    payload = {"mode": mode, "date": d.isoformat(), "question": question}
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = session.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            err = str(e)
        else:
            if r.status_code == 200:
                body = r.json()
                if not body.get("system") or not body.get("answer"):
                    raise RuntimeError(f"응답에 system/answer 가 없습니다: {str(body)[:200]}")
                return body
            if r.status_code in (401, 403, 404):
                # 토큰·엔드포인트 문제는 재시도해도 소용없다
                raise SystemExit(f"❌ {r.status_code} {url} — 토큰(ATHLETE_DATASET_TOKEN)이나 백엔드 API 를 확인하세요.")
            err = f"HTTP {r.status_code}: {r.text[:200]}"
        wait = 5 * attempt
        print(f"   ⚠️ 실패({attempt}/{MAX_RETRY}) {err} → {wait}초 후 재시도")
        time.sleep(wait)
    raise RuntimeError(err)


def cmd_generate(args):
    base = os.getenv("LIFEMETRICS_API_BASE")
    token = os.getenv("ATHLETE_DATASET_TOKEN")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    dates = pick_dates(start, end, args.dates)
    questions = load_questions(args.mode)
    if args.limit:
        questions = questions[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"raw_{args.mode}.jsonl"
    done = load_done_keys(out_path)

    todo = [(d, q) for d in dates for q in questions if sample_key(args.mode, d, q) not in done]
    print(f"📋 모드={args.mode} 날짜 {len(dates)}개 × 질문 {len(questions)}개 = {len(dates) * len(questions)}건, "
          f"이미 생성 {len(dates) * len(questions) - len(todo)}건, 이번에 {len(todo)}건")
    print(f"   날짜: {', '.join(d.isoformat() for d in dates)}")
    print(f"   저장: {out_path}")
    if args.dry_run or not todo:
        return

    if not base or not token:
        raise SystemExit("❌ .env 에 LIFEMETRICS_API_BASE / ATHLETE_DATASET_TOKEN 을 설정하세요.")

    ok = fail = 0
    session = requests.Session()
    with out_path.open("a", encoding="utf-8") as f:
        for i, (d, q) in enumerate(todo, 1):
            try:
                body = call_sample(session, base, token, args.mode, d, q)
            except RuntimeError as e:
                fail += 1
                print(f"   ❌ [{i}/{len(todo)}] {d} {q} — {e}")
                continue
            row = {
                "key": sample_key(args.mode, d, q),
                "mode": args.mode,
                "date": d.isoformat(),
                "question": q,
                "system": body["system"],
                "answer": body["answer"],
                "model": body.get("model"),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            ok += 1
            print(f"   ✅ [{i}/{len(todo)}] {d} {q[:40]}")
            time.sleep(args.sleep)
    print(f"🏁 완료: 성공 {ok}, 실패 {fail} (실패분은 다시 실행하면 이어서 생성)")


def cmd_export(args):
    rows = []
    for mode in MODES:
        path = OUT_DIR / f"raw_{mode}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("skip") or not r.get("answer", "").strip():
                    continue
                rows.append(r)
    if not rows:
        raise SystemExit(f"❌ {OUT_DIR} 에 raw_*.jsonl 데이터가 없습니다.")

    random.Random(args.seed).shuffle(rows)
    n_val = int(len(rows) * args.val_ratio)
    splits = {"val": rows[:n_val], "train": rows[n_val:]}

    for name, items in splits.items():
        path = OUT_DIR / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps({"messages": [
                    {"role": "system", "content": r["system"]},
                    {"role": "user", "content": r["question"]},
                    {"role": "assistant", "content": r["answer"].strip()},
                ]}, ensure_ascii=False) + "\n")
        by_mode = {m: sum(1 for r in items if r["mode"] == m) for m in MODES}
        print(f"💾 {path} — {len(items)}건 {by_mode}")


def main():
    p = argparse.ArgumentParser(description="애슐리트 페르소나 파인튜닝 데이터셋")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="백엔드를 호출해 raw JSONL 생성")
    g.add_argument("--mode", choices=MODES, required=True)
    g.add_argument("--start", required=True, help="기준 날짜 범위 시작 (YYYY-MM-DD)")
    g.add_argument("--end", help="기준 날짜 범위 끝 (기본: 오늘)")
    g.add_argument("--dates", type=int, default=1, help="범위에서 고르게 고를 날짜 수")
    g.add_argument("--limit", type=int, default=0, help="질문 앞에서 N개만 (테스트용)")
    g.add_argument("--sleep", type=float, default=1.0, help="호출 간 대기(초)")
    g.add_argument("--dry-run", action="store_true", help="계획만 출력하고 호출하지 않음")
    g.set_defaults(func=cmd_generate)

    e = sub.add_parser("export", help="raw JSONL → 학습용 train/val JSONL")
    e.add_argument("--val-ratio", type=float, default=0.1)
    e.add_argument("--seed", type=int, default=42)
    e.set_defaults(func=cmd_export)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
