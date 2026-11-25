"""
기술 문서 자동 갱신 실행기 (D1)

사용법 (저장소 루트에서):
    venv-agent/bin/python -m agent.run_doc_update                 # 제안만 저장 (cron 용, 저장소 안 건드림)
    venv-agent/bin/python -m agent.run_doc_update --days 14
    venv-agent/bin/python -m agent.run_doc_update --apply         # diff 확인 후 y 면 문서 파일 덮어쓰기
    venv-agent/bin/python -m agent.run_doc_update --apply --repo /path/to/다른/clone

--apply 는 운영 실행용 체크아웃(/mnt/200gb/project/data_pipeline)에 쓰지 마세요.
파일이 수정된 채로 남아 다음 [운영 반영] pull 과 충돌합니다. 별도 clone 에서 쓰고 커밋하세요.
"""
import argparse
import sys
import uuid
from pathlib import Path

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent import config
from agent.graphs.doc_update import build_graph


def main() -> int:
    p = argparse.ArgumentParser(description="data_pipeline 기술 문서 자동 갱신")
    p.add_argument("--days", type=int, default=7, help="참고할 최근 커밋 기간(일)")
    p.add_argument("--doc", default="docs/data_pipeline.md", help="갱신할 문서 (repo 기준 경로)")
    p.add_argument("--repo", default=str(config.REPO_ROOT), help="대상 저장소 경로")
    p.add_argument("--apply", action="store_true", help="승인 후 문서 파일을 실제로 덮어씀")
    args = p.parse_args()

    llm = ChatOpenAI(model=config.model_name(), temperature=0)
    graph = build_graph(llm, checkpointer=MemorySaver())
    run_cfg = {"configurable": {"thread_id": f"doc-update-{uuid.uuid4().hex[:8]}"}}

    state = graph.invoke({
        "repo_root": str(Path(args.repo).resolve()),
        "doc_path": args.doc,
        "days": args.days,
        "db_url": config.db_url(),
        "output_dir": str(config.output_dir()),
        "apply": args.apply,
    }, run_cfg)

    # review 노드의 interrupt 처리 (사람 승인)
    while "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        print("\n===== 요약 =====\n" + payload["summary"])
        print("\n===== diff =====\n" + payload["diff"])
        ok = sys.stdin.isatty() and input("\n문서에 반영할까요? [y/N] ").strip().lower() == "y"
        state = graph.invoke(Command(resume=ok), run_cfg)

    if not state.get("changed"):
        print(f"변경 없음 (HEAD {state.get('head')})\n{state.get('summary', '')}")
        return 0

    print(f"제안 저장: {state['proposal_path']}.md / .diff / .summary.txt")
    if args.apply:
        print("문서 반영 완료 — git diff 확인 후 커밋하세요." if state.get("applied") else "반영 취소됨.")
    else:
        print(state["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
