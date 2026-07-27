# backend/common/llm/claude_client.py

import os
import anthropic

client = anthropic.Anthropic(
    api_key=os.getenv("CLAUDE_API_KEY")
)


def call_claude(prompt: str) -> str:
    model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20240620")

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0.3,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    return response.content[0].text.strip()

