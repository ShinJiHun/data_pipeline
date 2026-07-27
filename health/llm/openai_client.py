# health/llm/openai_client.py

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 🔑 api_key는 env에서 자동 로딩
client = OpenAI()

def call_openai(prompt: str, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a professional body composition analysis AI.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.4,
    )

    return response.choices[0].message.content

