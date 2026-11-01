#!/usr/bin/env python3
"""
sync_pt_sheet.py

구글 시트("헬스 운동기록" > "PT운동 일정" 탭)에 적어온 PT 운동일지를
LifeMetrics의 exercise_session / exercise_log / exercise_set 테이블에 동기화하는 배치 스크립트.

- get_brevet.py / download_permanent_gpx.py 와 동일한 Google Drive OAuth
  (credentials.json / token.json)을 그대로 재사용함. 이 스크립트를 같은 폴더에 두거나,
  --credentials / --token 으로 경로를 지정하면 됨. (Drive의 drive.readonly 스코프만으로
  스프레드시트 export가 가능해서 별도 Sheets API 인증이 필요 없음)
- data_pipeline 컨벤션대로 .env의 DB_URL을 그대로 사용함(SQLAlchemy).

동작 방식
---------
1. 시트를 xlsx로 export 받아 "PT운동 일정" 탭을 읽는다.
2. session_no/body_part/date가 비어있는 행은 바로 위 세션에 속한 운동으로 간주(병합 셀 처리).
3. weight/reps 필드는 세트 수만큼 슬래시(/)로 나열되어 있으면 세트별 값으로,
   값이 하나면 전체 세트 동일 값으로 처리한다. 텍스트/오타/범위(~) 등은 최대한
   방어적으로 처리하고, 애매한 값은 세트에 넣지 않고 로그 메모에 원문을 남긴다.
   (자세한 규칙은 parse_numeric_field 참고)
4. 운동 이름(name_ko/name_en)이 기존 exercise_item과 매칭되면 그대로 쓰고,
   없으면 새 exercise_item을 만든다. 카테고리는 운동 이름의 키워드로 추정하고,
   추정이 안 되면 시트에 적힌 body_part(예: "하체 / 삼두"의 첫 토�은)로 대체한다.
   운동 자체 세션 배분은 새 부위/운동이 하나라도 생성되면 리포트에 표시되므로,
   커밋 전에 확인 가능하다.
5. exercise_session은 (user_id, session_date) 기준으로 기존 것을 찾아 재사용하고
   is_pt=1로 세팅한다(이미 있어도 PT로 표시만 갱신, 기존 로그는 안 건드림).
6. 같은 session_no가 이미 이 스크립트로 들어간 적이 있으면(exercise_log.memo에
   "[sheet#{session_no}]" 마커로 확인) 통째로 스킵한다 -> 매달 재실행해도 중복 안 됨.

사용법
------
    pip install requests sqlalchemy pymysql python-dotenv openpyxl \
        google-auth-oauthlib google-api-python-client --break-system-packages

    # 1) 먼저 무엇이 바뀌는지만 확인 (DB에 아무것도 안 씀)
    python3 sync_pt_sheet.py

    # 2) 리포트 확인 후 실제 반영
    python3 sync_pt_sheet.py --commit
"""

from __future__ import annotations

import argparse
import datetime
import io
import os
import re
from dataclasses import dataclass, field

import requests  # noqa: F401  (다른 배치 스크립트와 requirements.txt 통일용)
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── 설정 ──────────────────────────────────────────────────────────
SHEET_ID = "1NfhQvpDkp0LvcMbqPvpmBKVH5wXGI-lmdVETkxlHnUk"  # "헬스 운동기록"
SHEET_TAB_NAME = "PT운동 일정"
USER_ID = 1  # ExerciseInputPage.tsx 의 기본값과 동일

CREDENTIALS = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

MAX_SANE_WEIGHT = 500.0  # 이 이상은 오타로 간주 (예: "2008/7/6")

load_dotenv(find_dotenv())


# ════════════════════════════════════════════════════════════════
# 1) Google Drive에서 시트 xlsx 받아오기 (get_brevet.py / download_permanent_gpx.py 와 동일 패턴)
# ════════════════════════════════════════════════════════════════
def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = Flow.from_client_secrets_file(CREDENTIALS, scopes=SCOPES, redirect_uri="http://localhost:9999")
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            print("\n아래 URL을 브라우저에서 여세요:\n")
            print(auth_url)
            full_url = input("\n로그인 후 리다이렉트된 전체 URL 붙여넣기: ").strip()
            import urllib.parse
            params = urllib.parse.parse_qs(urllib.parse.urlparse(full_url).query)
            code = params.get("code", [full_url])[0]
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("  token 저장 완료")
    return build("drive", "v3", credentials=creds)


def download_sheet_xlsx(service, file_id: str) -> bytes:
    request = service.files().export_media(
        fileId=file_id,
        mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return request.execute()


def load_rows_from_xlsx(xlsx_bytes: bytes, tab_name: str) -> list[list]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb[tab_name]

    def fmt_date(v):
        if v is None:
            return None
        if isinstance(v, (datetime.datetime, datetime.date)):
            return v.strftime("%Y-%m-%d")
        return str(v).strip()

    def fmt_session_no(v):
        if v is None:
            return None
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()

    rows = []
    # 헤더는 6행(session_no|body_part|date|exercise_ko|exercise_en|weight|raps|sets|memo|영상 링크), 데이터는 7행부터
    for r in ws.iter_rows(min_row=7, max_row=ws.max_row, min_col=2, max_col=11):
        vals = [c.value for c in r]
        if all(v is None for v in vals):
            continue
        session_no, body_part, date, ex_ko, ex_en, weight, reps, sets, memo, video = vals
        rows.append([
            fmt_session_no(session_no),
            (body_part or "").strip() if body_part else None,
            fmt_date(date),
            ex_ko, ex_en,
            str(weight).strip() if weight is not None else "",
            str(reps).strip() if reps is not None else "",
            str(sets).strip() if sets is not None else "",
            (memo or "").strip() if memo else "",
            video,
        ])
    return rows


# ════════════════════════════════════════════════════════════════
# 2) 파싱
# ════════════════════════════════════════════════════════════════
TYPO_FIX = {"둥": "등", "허채": "하체"}
NON_MUSCLE_LABELS = {"서킷", "슈퍼 세트", "소근육"}

KEYWORD_CATEGORY = [
    (r"squat|런지|lunge|leg|레그|스쿼트|힙|hip|calf|카프|어덕션|abduction|adduction|deadlift|데드", "하체"),
    (r"bench|체스트|chest|플라이|fly|딥스|dip|푸쉬업|push[- ]?up", "가슴"),
    (r"row|로우|pulldown|풀다운|풀 다운|pull[- ]?up|풀업|lat\b|랫", "등"),
    (r"shoulder|숄더|delt|레터럴|lateral|프론트 레이즈|front raise|오버헤드 프레스|overhead press|military|밀리터리|비하인드|behind.*neck|업라이트|upright|face pull|페이스\s*풀", "어깨"),
    (r"curl.*bicep|바이셉|바이 셉|이두", "이두"),
    (r"extension|익스텐션|pushdown|푸쉬\s*다운|킥백|kickback|삼두|tricep", "삼두"),
    (r"sit[- ]?up|싯업|abdominal|복근|leg raise|레그 레이즈|플랭크|plank|crunch|크런치", "복근"),
]


def normalize_body_part_tokens(raw: str) -> list[str]:
    if not raw:
        return []
    s = re.sub(r"\([^)]*\)", "", raw.strip())
    tokens = []
    for p in re.split(r"[/,]", s):
        p = p.strip()
        if not p:
            continue
        for wrong, fixed in TYPO_FIX.items():
            p = p.replace(wrong, fixed)
        tokens.append(p)
    return tokens


def infer_category(name_ko: str, name_en: str, fallback_tokens: list[str]) -> tuple[str, str]:
    hay = f"{name_ko or ''} {name_en or ''}".lower()
    for pattern, cat in KEYWORD_CATEGORY:
        if re.search(pattern, hay, re.IGNORECASE):
            return cat, f"keyword:{pattern.split('|')[0]}"
    for tok in fallback_tokens:
        if tok not in NON_MUSCLE_LABELS:
            return tok, "session_body_part"
    return "전신", "default_fallback"


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _expand_token(tok: str) -> list:
    tok = tok.strip()
    if not tok:
        return []
    if "*" in tok:
        base, _, rep = tok.partition("*")
        base, rep = base.strip(), rep.strip()
        if _NUM_RE.match(base) and rep.isdigit():
            return [float(base)] * int(rep)
        return []
    if _NUM_RE.match(tok):
        return [float(tok)]
    return []


@dataclass
class ParsedNumbers:
    values: list
    raw: str
    note: str = ""


def parse_numeric_field(raw: str, sets: int) -> ParsedNumbers:
    if raw is None:
        return ParsedNumbers([None] * sets, raw="")
    s = str(raw).strip()
    if s in ("", "-", "/"):
        return ParsedNumbers([None] * sets, raw=s)

    note = ""
    m = re.fullmatch(r"(\d+(\.\d+)?)\s*~\s*(\d+(\.\d+)?)", s)
    if m:
        avg = (float(m.group(1)) + float(m.group(3))) / 2
        return ParsedNumbers([avg] * max(sets, 1), raw=s, note=f"범위값 평균 처리(원본:{s})")

    if not re.fullmatch(r"[\d/*. ]+", s):
        return ParsedNumbers([None] * sets, raw=s, note=f"숫자 아님, 원문 보존: {s}")

    tokens = [t for t in s.split("/") if t.strip() != ""]
    values: list = []
    for t in tokens:
        values.extend(_expand_token(t))

    cleaned = []
    for v in values:
        if v is not None and v > MAX_SANE_WEIGHT:
            note = (note + "; " if note else "") + f"비정상값 제외({v})"
            cleaned.append(None)
        else:
            cleaned.append(v)
    values = cleaned

    if not values:
        return ParsedNumbers([None] * sets, raw=s, note=note or "파싱 실패")

    n = max(sets, 1)
    if len(values) == n:
        return ParsedNumbers(values, raw=s, note=note)
    if len(values) == 1:
        return ParsedNumbers(values * n, raw=s, note=note)
    if len(values) > n:
        note = (note + "; " if note else "") + f"세트수({sets})보다 값이 많아 앞 {n}개만 사용"
        return ParsedNumbers(values[:n], raw=s, note=note)
    note = (note + "; " if note else "") + f"세트수({sets})보다 값이 적어 마지막 값으로 채움"
    return ParsedNumbers(values + [values[-1]] * (n - len(values)), raw=s, note=note)


def parse_int_sets(raw: str) -> int:
    if raw is None:
        return 1
    s = str(raw).strip()
    if s in ("", "-", "/"):
        return 1
    try:
        return max(1, int(float(s)))
    except ValueError:
        return 1


@dataclass
class ExerciseEntry:
    name_ko: str
    name_en: str
    weight_raw: str
    reps_raw: str
    sets: int
    memo: str
    per_set_weight: list = field(default_factory=list)
    per_set_reps: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class SessionEntry:
    session_no: str
    body_part: str
    date: str
    exercises: list = field(default_factory=list)


def build_sessions(rows: list[list]) -> list[SessionEntry]:
    sessions: list[SessionEntry] = []
    cur: SessionEntry | None = None
    for row in rows:
        session_no, body_part, date, ex_ko, ex_en, weight, reps, sets_raw, memo, video = row
        if session_no:
            cur = SessionEntry(session_no=session_no, body_part=body_part, date=date)
            sessions.append(cur)
        if cur is None or not (ex_ko or ex_en):
            continue

        sets = parse_int_sets(sets_raw)
        w = parse_numeric_field(weight, sets)
        r = parse_numeric_field(reps, sets)
        n = max(len(w.values), len(r.values), sets)
        if len(w.values) < n:
            w.values += [None] * (n - len(w.values))
        if len(r.values) < n:
            r.values += [None] * (n - len(r.values))

        cur.exercises.append(ExerciseEntry(
            name_ko=(ex_ko or "").strip(),
            name_en=(ex_en or "").strip(),
            weight_raw=weight or "",
            reps_raw=reps or "",
            sets=n,
            memo=(memo or "").strip(),
            per_set_weight=w.values,
            per_set_reps=r.values,
            notes=[x for x in (w.note, r.note) if x],
        ))
    return sessions


# ════════════════════════════════════════════════════════════════
# 3) DB 반영
# ════════════════════════════════════════════════════════════════
def get_engine():
    db_url = os.environ.get("DB_URL")
    if not db_url:
        raise RuntimeError("DB_URL 환경변수가 없습니다 (.env 확인).")
    return create_engine(db_url, pool_pre_ping=True)


def norm_name(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def sync(commit: bool, xlsx_path: str | None, reset: bool):
    if xlsx_path:
        with open(xlsx_path, "rb") as f:
            xlsx_bytes = f.read()
    else:
        service = get_drive_service()
        xlsx_bytes = download_sheet_xlsx(service, SHEET_ID)

    rows = load_rows_from_xlsx(xlsx_bytes, SHEET_TAB_NAME)
    sessions = build_sessions(rows)
    print(f"시트에서 세션 {len(sessions)}개, 운동 기록 {sum(len(s.exercises) for s in sessions)}건 파싱")

    engine = get_engine()
    with engine.begin() as conn:
        reset_logs = 0
        reset_sessions = 0
        if reset:
            # 이 스크립트가 이전에 넣은 기록만 지운다: memo에 '[sheet#'로 시작하는
            # 마커가 있는 exercise_log만 대상 (수동으로 넣은 다른 기록은 절대 건드리지 않음).
            marker_rows = conn.execute(
                text("SELECT id, session_id FROM exercise_log WHERE memo LIKE '[sheet#%'")
            ).mappings().all()
            reset_logs = len(marker_rows)
            affected_session_ids = sorted({r["session_id"] for r in marker_rows})
            if commit and reset_logs:
                # exercise_set은 exercise_log FK가 ON DELETE CASCADE라 같이 지워짐
                conn.execute(text("DELETE FROM exercise_log WHERE memo LIKE '[sheet#%'"))
                for sid in affected_session_ids:
                    remaining = conn.execute(
                        text("SELECT COUNT(*) AS c FROM exercise_log WHERE session_id=:sid"),
                        {"sid": sid},
                    ).mappings().first()["c"]
                    if remaining == 0:
                        conn.execute(text("DELETE FROM exercise_session WHERE id=:sid"), {"sid": sid})
                        reset_sessions += 1
            elif not commit:
                # dry-run: 실제로는 얼마나 비게 될지만 미리 계산
                for sid in affected_session_ids:
                    total_for_session = conn.execute(
                        text("SELECT COUNT(*) AS c FROM exercise_log WHERE session_id=:sid"),
                        {"sid": sid},
                    ).mappings().first()["c"]
                    marker_for_session = sum(1 for r in marker_rows if r["session_id"] == sid)
                    if total_for_session == marker_for_session:
                        reset_sessions += 1
            print(f"[reset] 이전에 이 스크립트가 넣은 기록: exercise_log {reset_logs}건 "
                  f"/ 비게 되어 같이 삭제될 exercise_session {reset_sessions}개 "
                  f"{'(실제 삭제함)' if commit else '(dry-run, 실제로는 안 지움)'}")

        categories = {row["name"]: row["id"] for row in
                      conn.execute(text("SELECT id, name FROM exercise_category")).mappings()}

        items = list(conn.execute(text(
            "SELECT id, category_id, name_ko, name_en FROM exercise_item")).mappings())
        items_by_ko: dict[str, int] = {}
        items_by_en: dict[str, int] = {}
        for it in items:
            if it["name_ko"]:
                items_by_ko.setdefault(norm_name(it["name_ko"]), it["id"])
            if it["name_en"]:
                items_by_en.setdefault(norm_name(it["name_en"]), it["id"])

        new_categories: dict[str, int] = {}
        new_items: list[tuple] = []  # (name_ko, name_en, category_name)
        skipped_sessions = []
        imported_sessions = []
        total_anomalies = 0
        total_logs = 0
        total_sets = 0
        anomaly_details: list[dict] = []
        preview_rows: list[dict] = []  # 리포트에 보여줄 샘플(세션당 첫 운동)

        def get_or_create_category(name: str) -> int:
            if name in categories:
                return categories[name]
            if commit:
                cid = conn.execute(
                    text("INSERT INTO exercise_category (name) VALUES (:n)"),
                    {"n": name},
                ).lastrowid
            else:
                cid = -len(categories) - 1  # dry-run 임시 id
            categories[name] = cid
            new_categories[name] = cid
            return cid

        def get_or_create_item(name_ko: str, name_en: str, category_name: str) -> int:
            key_ko = norm_name(name_ko)
            key_en = norm_name(name_en)
            if key_ko and key_ko in items_by_ko:
                return items_by_ko[key_ko]
            if key_en and key_en in items_by_en:
                return items_by_en[key_en]

            cid = get_or_create_category(category_name)
            display_name = name_ko or name_en
            if commit:
                iid = conn.execute(
                    text(
                        "INSERT INTO exercise_item (category_id, name, name_ko, name_en, is_active) "
                        "VALUES (:cid, :name, :ko, :en, 1)"
                    ),
                    {"cid": cid, "name": display_name, "ko": name_ko or None, "en": name_en or None},
                ).lastrowid
            else:
                iid = -len(items_by_ko) - len(items_by_en) - 1000

            if key_ko:
                items_by_ko[key_ko] = iid
            if key_en:
                items_by_en[key_en] = iid
            new_items.append((name_ko, name_en, category_name))
            return iid

        def get_or_create_session(session_date: str) -> int:
            row = conn.execute(
                text("SELECT id FROM exercise_session WHERE user_id=:uid AND session_date=:d"),
                {"uid": USER_ID, "d": session_date},
            ).mappings().first()
            if row:
                sid = row["id"]
                if commit:
                    conn.execute(
                        text("UPDATE exercise_session SET is_pt=1 WHERE id=:id"),
                        {"id": sid},
                    )
                return sid
            if commit:
                return conn.execute(
                    text(
                        "INSERT INTO exercise_session (user_id, session_date, is_pt) "
                        "VALUES (:uid, :d, 1)"
                    ),
                    {"uid": USER_ID, "d": session_date},
                ).lastrowid
            return -1  # dry-run

        for s in sessions:
            marker = f"[sheet#{s.session_no}]"
            # --reset을 쓰면 (dry-run이든 commit이든) 이 스크립트가 이전에 넣은 기록은
            # "처음부터 다시 넣는다"는 전제이므로 스킵 체크 자체를 건너뛴다.
            # (commit일 때는 위에서 실제로 지웠고, dry-run일 때는 지운 것처럼 시뮬레이션)
            if not reset:
                already = conn.execute(
                    text("SELECT 1 FROM exercise_log WHERE memo LIKE :m LIMIT 1"),
                    {"m": marker + "%"},
                ).first()
                if already:
                    skipped_sessions.append(s.session_no)
                    continue

            tokens = normalize_body_part_tokens(s.body_part)
            session_id = get_or_create_session(s.date)

            for idx, e in enumerate(s.exercises):
                cat_name, _reason = infer_category(e.name_ko, e.name_en, tokens)
                item_id = get_or_create_item(e.name_ko, e.name_en, cat_name)

                avg_w = [v for v in e.per_set_weight if v is not None]
                avg_r = [v for v in e.per_set_reps if v is not None]
                weight = round(sum(avg_w) / len(avg_w), 2) if avg_w else None
                reps = round(sum(avg_r) / len(avg_r)) if avg_r else None

                memo_parts = [marker]
                if e.memo:
                    memo_parts.append(e.memo)
                if e.notes:
                    memo_parts.append("(" + "; ".join(e.notes) + ")")
                memo = " ".join(memo_parts)

                total_logs += 1
                total_sets += e.sets
                if idx == 0:
                    preview_rows.append({
                        "session_no": s.session_no, "date": s.date, "name": e.name_ko or e.name_en,
                        "sets": e.sets, "weight": e.per_set_weight, "reps": e.per_set_reps,
                        "log_weight": weight, "log_reps": reps,
                    })
                if e.notes:
                    total_anomalies += 1
                    anomaly_details.append({
                        "session_no": s.session_no, "date": s.date, "name": e.name_ko or e.name_en,
                        "weight_raw": e.weight_raw, "reps_raw": e.reps_raw, "sets": e.sets,
                        "per_set_weight": e.per_set_weight, "per_set_reps": e.per_set_reps,
                        "notes": e.notes,
                    })

                if commit:
                    log_id = conn.execute(
                        text(
                            "INSERT INTO exercise_log "
                            "(session_id, exercise_item_id, weight, reps, sets, memo) "
                            "VALUES (:sid, :iid, :w, :r, :sets, :memo)"
                        ),
                        {"sid": session_id, "iid": item_id, "w": weight, "r": reps,
                         "sets": e.sets, "memo": memo},
                    ).lastrowid
                    for i in range(e.sets):
                        conn.execute(
                            text(
                                "INSERT INTO exercise_set (log_id, set_number, weight, reps) "
                                "VALUES (:lid, :n, :w, :r)"
                            ),
                            {"lid": log_id, "n": i + 1,
                             "w": e.per_set_weight[i], "r": e.per_set_reps[i]},
                        )

            imported_sessions.append(s.session_no)

        # dry-run에서는 모든 INSERT/UPDATE가 `if commit:` 분기로 막혀 있어 이 트랜잭션에는
        # 아무 변경도 없다 (SELECT만 수행) -> engine.begin() 종료 시 커밋되어도 안전함.

    print("\n" + "=" * 60)
    print(f"모드: {'COMMIT (실제 반영)' if commit else 'DRY-RUN (미리보기, DB에 안 씀)'}"
          f"{' + RESET' if reset else ''}")
    if reset:
        print(f"[reset] 지운 기존 시트-임포트 기록: exercise_log {reset_logs}건, "
              f"exercise_session {reset_sessions}개")
    print(f"가져올 세션: {len(imported_sessions)}개 / 이미 들어가 있어 스킵: {len(skipped_sessions)}개")
    if skipped_sessions:
        print(f"  스킵된 session_no: {skipped_sessions}")
    print(f"운동 기록(exercise_log): {total_logs}건 / 세트(exercise_set): {total_sets}건")
    print(f"새로 만든 카테고리: {len(new_categories)}개 -> {list(new_categories.keys())}")
    print(f"새로 만든 운동(exercise_item): {len(new_items)}개")
    for ko, en, cat in new_items[:50]:
        print(f"  + [{cat}] {ko} / {en}")
    if len(new_items) > 50:
        print(f"  ... 외 {len(new_items) - 50}개")

    print(f"\n--- 무게/세트 샘플 (세션당 첫 운동, {len(preview_rows)}개) ---")
    for p in preview_rows[:20]:
        print(f"  [{p['session_no']}] {p['date']} {p['name']}: "
              f"세트별 무게={p['weight']} 세트별 reps={p['reps']} "
              f"(log 평균 weight={p['log_weight']} reps={p['log_reps']} sets={p['sets']})")
    if len(preview_rows) > 20:
        print(f"  ... 외 {len(preview_rows) - 20}개 세션 (전체 세션 수: {len(preview_rows)})")

    print(f"\n--- 값 파싱 특이사항: {total_anomalies}건 (원문은 각 기록 memo에도 보존됨) ---")
    for a in anomaly_details:
        print(f"  [{a['session_no']}] {a['date']} {a['name']} | "
              f"원본 weight={a['weight_raw']!r} reps={a['reps_raw']!r} sets={a['sets']} "
              f"-> weight={a['per_set_weight']} reps={a['per_set_reps']} | {a['notes']}")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="실제로 DB에 반영 (기본은 dry-run)")
    ap.add_argument("--xlsx", help="디버그용: Drive 대신 로컬 xlsx 파일을 읽음")
    ap.add_argument(
        "--reset", action="store_true",
        help="이 스크립트가 이전에 넣은 기록(memo에 '[sheet#..]' 마커가 있는 exercise_log/"
             "그로 인해 비게 되는 exercise_session)을 먼저 지우고 처음부터 다시 넣는다. "
             "수동으로 넣은 다른 기록(마커 없는 것)은 절대 건드리지 않는다. "
             "--commit 없이 쓰면 뭐가 지워질지만 미리 보여준다.",
    )
    args = ap.parse_args()

    sync(commit=args.commit, xlsx_path=args.xlsx, reset=args.reset)


if __name__ == "__main__":
    main()
