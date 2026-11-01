"""카카오 '나에게 보내기'용 refresh token 발급 스크립트.

refresh token 은 콘솔에 표시되지 않고, 카카오 로그인(동의)을 한 번 거쳐야 발급된다.
REST API 키를 재발급했거나 refresh token 이 만료(약 2개월)됐을 때 다시 실행한다.

사전 준비 (developers.kakao.com → 내 애플리케이션):
  1. [카카오 로그인] 활성화 ON
  2. [카카오 로그인] → Redirect URI 에 아래 REDIRECT_URI 와 똑같은 값 등록
  3. [동의항목] → "카카오톡 메시지 전송(talk_message)" 사용 설정
  4. [보안] → Client Secret 을 '사용함'으로 두었다면 .env 에 KAKAO_CLIENT_SECRET 추가

사용법:
  venv/bin/python lotto_parser/main/get_kakao_token.py
  → 출력된 URL 을 브라우저에서 열고 동의
  → 이동한 주소(페이지가 안 열려도 됨)의 ?code=... 전체 주소나 code 값만 붙여넣기
  → .env 의 KAKAO_REFRESH_TOKEN 이 새 값으로 바뀐다 (기존 .env 는 백업)
"""
import os
import re
import shutil
import sys
import urllib.parse
from datetime import datetime

import requests
from dotenv import find_dotenv, load_dotenv

ENV_PATH = find_dotenv()
load_dotenv(ENV_PATH)

REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "http://localhost:9999")


def build_authorize_url() -> str:
    params = {
        "response_type": "code",
        "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "scope": "talk_message",
    }
    return "https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(params)


def extract_code(text: str) -> str:
    text = text.strip()
    if "code=" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        return urllib.parse.parse_qs(query)["code"][0]
    return text


def exchange_code(code: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET
    res = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
    if res.status_code != 200:
        sys.exit(f"❌ 토큰 발급 실패: {res.text}")
    return res.json()


def save_refresh_token(token: str) -> None:
    backup = f"{ENV_PATH}.bak_{datetime.now():%Y%m%d%H%M%S}"
    shutil.copy2(ENV_PATH, backup)
    with open(ENV_PATH, encoding="utf-8") as f:
        content = f.read()
    line = f"KAKAO_REFRESH_TOKEN={token}"
    if re.search(r"^KAKAO_REFRESH_TOKEN=.*$", content, flags=re.M):
        content = re.sub(r"^KAKAO_REFRESH_TOKEN=.*$", line, content, flags=re.M)
    else:
        content = content.rstrip("\n") + "\n" + line + "\n"
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ {ENV_PATH} 의 KAKAO_REFRESH_TOKEN 갱신 (백업: {backup})")


def main():
    print("1) 아래 URL 을 브라우저에서 열고 동의하세요:\n")
    print("   " + build_authorize_url() + "\n")
    print(f"2) {REDIRECT_URI}?code=... 로 이동합니다 (페이지가 안 열려도 정상).")
    raw = input("   주소창의 전체 주소 또는 code 값을 붙여넣으세요: ")
    tokens = exchange_code(extract_code(raw))
    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit(f"❌ 응답에 refresh_token 이 없음: {list(tokens)}")
    save_refresh_token(refresh)
    print(f"   scope: {tokens.get('scope')} / refresh token 유효기간: 약 {tokens.get('refresh_token_expires_in', 0) // 86400}일")


if __name__ == "__main__":
    main()
