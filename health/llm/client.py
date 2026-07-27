# health/llm/client.py
import os
from typing import Dict, Any, List

from health.llm.openai_client import call_openai
from health.llm.gemini_client import call_gemini
from health.llm.claude_client import call_claude
from health.llm.prompt_builder import build_body_analysis_prompt


def run_llm_analysis(
    provider: str,
    records: List[Any],
    goal_type: str = "DIET",
    body_type: str | None = None,
) -> Dict[str, Any]:
    """
    공통 LLM 실행 엔트리 포인트
    """

    prompt = build_body_analysis_prompt(
        records=records,
        goal_type=goal_type,
        body_type=body_type,
    )

    if provider == "openai":
        return call_openai(prompt)

    elif provider == "gemini":
        return call_gemini(prompt)

    elif provider == "claude":
        return call_claude(prompt)

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

