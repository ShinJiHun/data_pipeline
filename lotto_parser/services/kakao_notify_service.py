import os
import json
import requests
import random
from dotenv import load_dotenv, find_dotenv

# ===============================
# ✅ 카카오 REST API 인증 정보
# ===============================
# 값은 data_pipeline/.env 에 둔다 (KAKAO_REST_API_KEY, KAKAO_REFRESH_TOKEN)
load_dotenv(find_dotenv())
REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN = os.environ["KAKAO_REFRESH_TOKEN"]


# ===============================
# ✅ Access Token 자동 갱신
# ===============================
def get_access_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    }
    res = requests.post(url, data=data)
    if res.status_code != 200:
        raise Exception(f"Kakao token refresh failed: {res.text}")
    token = res.json().get("access_token")
    return token


# ===============================
# ✅ 텍스트 메시지 보내기
# ===============================
def notify_text(message: str):
    token = get_access_token()
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"  # ✅ 변경된 endpoint
    headers = {"Authorization": f"Bearer {token}"}
    data = {
        "template_object": json.dumps({
            "object_type": "text",
            "text": message,
            "link": {"web_url": "https://developers.kakao.com"},
            "button_title": "확인"
        })
    }
    res = requests.post(url, headers=headers, data=data)
    print("📨 TEXT Notify:", res.status_code, res.text)


# ===============================
# ✅ 로또 추천 번호 5세트 생성
# ===============================
def generate_lotto_sets(count=5):
    sets = [sorted(random.sample(range(1, 46), 6)) for _ in range(count)]
    lines = [f"{i+1:02d}회: " + "  ".join(f"{n:02d}" for n in nums) for i, nums in enumerate(sets)]
    message = "🎰 이번 주 로또 추천번호\n\n" + "\n".join(lines) + "\n\n#자동추천 #Lotto"
    return message


# ===============================
# ✅ 테스트용 실행
# ===============================
if __name__ == "__main__":
    msg = generate_lotto_sets()
    print("📦 전송할 메시지:\n", msg)
    notify_text(msg)

