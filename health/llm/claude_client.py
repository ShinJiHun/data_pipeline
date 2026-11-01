# health/llm/claude_client.py
import sys

sys.path.insert(0, "/mnt/200gb/project")
from data_pipeline.claude_config import client, CLAUDE_MODEL, extract_text


def call_claude(prompt: str) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        temperature=0.3,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    return extract_text(response).strip()
