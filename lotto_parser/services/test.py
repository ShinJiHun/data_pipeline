import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv


# ==============================
# 프로젝트 루트 기준 .env 로드
# ==============================
BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

KAKAO_ACCESS_TOKEN = os.getenv("KAKAO_ACCESS_TOKEN")

if not KAKAO_ACCESS_TOKEN:
    raise RuntimeError(
        f"❌ KAKAO_ACCESS_TOKEN 없음 (.env 경로: {ENV_PATH})"
    )


# ==============================
# 카카오 메시지 전송
# ==============================
def send_kakao_message(text: str) -> None:
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

    headers = {
        "Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    template_object = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": "https://developers.kakao.com",
            "mobile_web_url": "https://developers.kakao.com",
        },
        "button_title": "확인하기",
    }

    data = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }

    res = requests.post(url, headers=headers, data=data, timeout=10)

    if res.status_code == 200:
        print("✅ 카카오 알림 전송 성공")
    else:
        print("❌ 카카오 알림 실패")
        print("status:", res.status_code)
        print("response:", res.text)


# ==============================
# 테스트 실행
# ==============================
if __name__ == "__main__":
    send_kakao_message(
        "🎰 로또 파이프라인 환경변수 테스트\n\n.env 로드 성공 ✅"
    )

