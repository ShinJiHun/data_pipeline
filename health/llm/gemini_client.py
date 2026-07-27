# backend/common/llm/gemini_client.py

import os
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def call_gemini(prompt: str) -> str:
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

    model = genai.GenerativeModel(model_name)

    response = model.generate_content(
        prompt,
        generation_config={
            "temperature": 0.3,
        },
    )

    return response.text.strip()

