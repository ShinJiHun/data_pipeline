"""
D1. 기술 문서 자동 갱신 그래프 (docs/data_pipeline.md)

    START → collect → analyze ─(변경 없음)→ END
                         │
                         └→ save_proposal ─(apply 아님)→ END
                                  │
                                  └→ review(사람 승인, interrupt) → apply → END

- collect:       git 커밋·파일 목록, DB 테이블 요약, 현재 문서 읽기 (읽기 전용)
- analyze:       LLM 이 근거에 맞춰 문서 수정안 작성 (구조화 출력)
- save_proposal: 수정안·diff·요약을 AGENT_OUTPUT_DIR 에 저장 (저장소는 건드리지 않음)
- review:        apply 모드일 때만, 사람에게 diff 를 보여 주고 승인 대기
- apply:         승인되면 문서 파일을 덮어씀 (git commit/push 는 하지 않음)
"""
import difflib
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.tools import db_tools, git_tools


class DocState(TypedDict, total=False):
    # 입력
    repo_root: str
    doc_path: str          # repo_root 기준 상대 경로
    days: int
    db_url: str | None
    output_dir: str
    apply: bool
    # collect 결과
    head: str
    commits: str
    files: str
    tables: str
    current_doc: str
    # analyze 결과
    changed: bool
    summary: str
    new_doc: str
    # 이후 단계
    diff: str
    proposal_path: str
    approved: bool
    applied: bool


class DocUpdate(BaseModel):
    changed: bool = Field(description="문서를 고칠 필요가 있으면 true, 그대로 두면 false")
    summary: str = Field(description="무엇을 왜 고쳤는지 한국어 3~6줄 요약 (변경 없으면 그 이유)")
    new_markdown: str = Field(description="수정된 문서 전체 마크다운 (changed=false 면 빈 문자열)")


SYSTEM_PROMPT = """당신은 data_pipeline 저장소의 기술 문서 관리자입니다.
주어진 근거(최근 커밋, 추적 파일 목록, DB 테이블 요약)만 보고 '현재 상태' 기술 문서를 최신으로 고칩니다.

규칙:
- 근거로 확인되는 사실만 고칩니다. 추측으로 기능을 추가하거나 지우지 마세요.
- 문서의 섹션 구조와 문체(표, 짧은 설명)를 유지하고, 바뀐 부분만 최소로 고칩니다.
- 로드맵·할 일·우선순위는 쓰지 않습니다 (통합 기획서 담당).
- 비밀번호·토큰·API 키·개인 데이터 값은 절대 쓰지 않습니다.
- 맨 위 '기준:' 줄은 오늘 날짜와 HEAD 커밋으로 바꿉니다.
- 근거상 바뀐 게 없으면 changed=false 로 답합니다 ('기준:' 줄만 바뀌는 경우도 false).
"""


def collect(state: DocState) -> DocState:
    repo = Path(state["repo_root"])
    doc_file = repo / state["doc_path"]
    tables = "(AGENT_DB_URL 없음 — DB 요약 생략)"
    if state.get("db_url"):
        try:
            tables = db_tools.table_summary(state["db_url"])
        except Exception as e:  # DB 가 죽어도 문서 갱신은 진행
            tables = f"(DB 조회 실패: {type(e).__name__})"
    return {
        "head": git_tools.head_info(repo),
        "commits": git_tools.recent_commits(repo, state.get("days", 7)),
        "files": git_tools.tracked_files(repo),
        "tables": tables,
        "current_doc": doc_file.read_text(encoding="utf-8") if doc_file.exists() else "",
    }


def make_analyze(llm: BaseChatModel):
    structured = llm.with_structured_output(DocUpdate)

    def analyze(state: DocState) -> DocState:
        today = datetime.now().strftime("%Y-%m-%d")
        human = f"""오늘: {today}
HEAD: {state['head']}

## 현재 문서 ({state['doc_path']})
{state['current_doc'] or '(문서 없음 — 새로 작성)'}

## 최근 {state.get('days', 7)}일 커밋
{state['commits']}

## 추적 파일 목록
{state['files']}

## DB 테이블 요약
{state['tables']}
"""
        result: DocUpdate = structured.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]
        )
        return {
            "changed": result.changed and bool(result.new_markdown.strip()),
            "summary": result.summary,
            "new_doc": result.new_markdown,
        }

    return analyze


def save_proposal(state: DocState) -> DocState:
    diff = "".join(difflib.unified_diff(
        state["current_doc"].splitlines(keepends=True),
        state["new_doc"].splitlines(keepends=True),
        fromfile=f"a/{state['doc_path']}", tofile=f"b/{state['doc_path']}",
    ))
    out = Path(state["output_dir"]) / "doc_update"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out / stamp
    base.with_suffix(".md").write_text(state["new_doc"], encoding="utf-8")
    base.with_suffix(".diff").write_text(diff, encoding="utf-8")
    base.with_suffix(".summary.txt").write_text(
        f"HEAD {state['head']}\n\n{state['summary']}\n", encoding="utf-8")
    return {"diff": diff, "proposal_path": str(base)}


def review(state: DocState) -> DocState:
    # 실행을 멈추고 호출 쪽(run_doc_update.py)에 diff 를 넘긴다. Command(resume=True/False) 로 재개.
    answer = interrupt({"summary": state["summary"], "diff": state["diff"]})
    return {"approved": bool(answer)}


def apply(state: DocState) -> DocState:
    if not state.get("approved"):
        return {"applied": False}
    doc_file = Path(state["repo_root"]) / state["doc_path"]
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text(state["new_doc"], encoding="utf-8")
    return {"applied": True}


def build_graph(llm: BaseChatModel, checkpointer=None):
    g = StateGraph(DocState)
    g.add_node("collect", collect)
    g.add_node("analyze", make_analyze(llm))
    g.add_node("save_proposal", save_proposal)
    g.add_node("review", review)
    g.add_node("apply", apply)

    g.add_edge(START, "collect")
    g.add_edge("collect", "analyze")
    g.add_conditional_edges("analyze", lambda s: "save_proposal" if s.get("changed") else END)
    g.add_conditional_edges("save_proposal", lambda s: "review" if s.get("apply") else END)
    g.add_edge("review", "apply")
    g.add_edge("apply", END)
    return g.compile(checkpointer=checkpointer)
