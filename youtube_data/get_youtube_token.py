"""
YouTube OAuth 최초 인증 스크립트 (서버에서 실행, 브라우저/터널 불필요 버전)

동작 방식:
1. 인증 URL을 출력한다
2. 그 URL을 아무 브라우저(맥이든 어디든)에서 열어 로그인/동의한다
3. 리다이렉트된 후 브라우저 주소창에 뜨는 전체 URL을 통째로 복사해서
   (설령 "연결할 수 없음" 에러 페이지가 떠도 주소창의 URL 자체는 유효함)
   이 스크립트의 입력창에 붙여넣는다
4. 스크립트가 그 URL에서 인증 코드를 추출해 토큰을 발급받는다

SSH 포트포워딩이 전혀 필요 없다.

사전 준비:
    pip install google-auth-oauthlib python-dotenv

사용법:
    python3 get_youtube_token.py
"""

import os

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # http://localhost 리다이렉트 허용

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

load_dotenv("/mnt/200gb/project/data_pipeline/.env")

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
REDIRECT_URI = "http://localhost:8090/"

CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError(
        "❌ .env에 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET이 없습니다."
    )

CLIENT_CONFIG = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}


def main():
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, redirect_uri=REDIRECT_URI)

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # 매번 refresh_token을 확실히 받기 위해
    )

    print("\n" + "=" * 60)
    print("1) 아래 URL을 브라우저에서 열어 로그인/동의하세요:")
    print("=" * 60)
    print(auth_url)
    print("\n2) 동의 후 리다이렉트된 페이지가 '연결할 수 없음' 에러여도 괜찮습니다.")
    print("   주소창에 있는 전체 URL(http://localhost:8090/?...code=... 포함)을")
    print("   통째로 복사해서 아래에 붙여넣으세요.\n")

    redirected_url = input("리다이렉트된 전체 URL 붙여넣기: ").strip()

    flow.fetch_token(authorization_response=redirected_url)
    creds = flow.credentials

    print("\n" + "=" * 60)
    print("✅ 인증 완료! 아래 값들을 서버의 .env 파일에 추가하세요.")
    print("=" * 60)
    print(f'YOUTUBE_CLIENT_ID="{CLIENT_ID}"')
    print(f'YOUTUBE_CLIENT_SECRET="{CLIENT_SECRET}"')
    print(f'YOUTUBE_REFRESH_TOKEN="{creds.refresh_token}"')
    print("=" * 60)


if __name__ == "__main__":
    main()
