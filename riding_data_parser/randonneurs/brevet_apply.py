import os

# Chrome temp 경로 강제 설정
os.makedirs("/mnt/200gb/tmp", exist_ok=True)
os.makedirs("/mnt/200gb/selenium_cache", exist_ok=True)
os.makedirs("/mnt/200gb/selenium_userdata", exist_ok=True)
os.environ["TMPDIR"] = "/mnt/200gb/tmp"
os.environ["TEMP"] = "/mnt/200gb/tmp"
os.environ["TMP"] = "/mnt/200gb/tmp"
os.environ["SE_CACHE_PATH"] = "/mnt/200gb/selenium_cache"

import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# ========== 설정 ==========
USER_ID = "20190"
USER_PW = "tho881@naver.com"
TARGET_EVENT_ID = "2261"
TARGET_EVENT_NAME = "서울 300K-서"
CHECK_INTERVAL = 30
BASE_URL = "http://www.korearandonneurs.kr/reg"

SELENIUM_USERDATA = "/mnt/200gb/selenium_userdata"
CHROME_ENV = {
    **os.environ,
    "TMPDIR": "/mnt/200gb/tmp",
    "TEMP": "/mnt/200gb/tmp",
    "TMP": "/mnt/200gb/tmp",
}


def get_session_via_selenium():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument(f"--user-data-dir={SELENIUM_USERDATA}")
    options.add_argument("--crash-dumps-dir=/mnt/200gb/tmp")
    options.add_argument("--window-size=1280,800")
    options.binary_location = "/usr/bin/chromium"

    service = Service(
        executable_path="/usr/bin/chromedriver",
        env=CHROME_ENV
    )

    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get(f"{BASE_URL}/login.php")
        time.sleep(2)

        driver.find_element(By.NAME, "memberID").send_keys(USER_ID)
        driver.find_element(By.NAME, "email").send_keys(USER_PW)
        driver.find_element(By.XPATH, "//button[contains(text(), 'Login')]").click()
        time.sleep(3)

        cookies = driver.get_cookies()
        cookie_dict = {c['name']: c['value'] for c in cookies}
        print(f"로그인 성공, 쿠키: {cookie_dict}")
        return cookies

    except Exception as e:
        print(f"Selenium 오류: {e}")
        raise
    finally:
        driver.quit()


def make_session(cookies):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/register.php"
    })
    for c in cookies:
        session.cookies.set(c['name'], c['value'])
    return session


def check_and_apply(session):
    try:
        res = session.get(f"{BASE_URL}/register.php", timeout=10)
    except Exception as e:
        print(f"요청 실패: {e}")
        return None

    # 세션 만료 확인
    if "안녕하세요" not in res.text:
        print("세션 만료됨. 재로그인 필요.")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    apply_btn = soup.find("button", {"class": "btn_apply", "id": TARGET_EVENT_ID})

    if apply_btn:
        print(f"[{time.strftime('%H:%M:%S')}] ★★★ 자리 발견! 즉시 신청! ★★★")
        try:
            res = session.post(f"{BASE_URL}/event_apply.php", data={
                "event_id": TARGET_EVENT_ID
            }, timeout=10)
            result = res.text.strip()
            print(f"신청 결과: {result}")
            if result == "success":
                return True
            elif result == "full":
                print("타이밍 아쉽게 다시 찼습니다.")
            elif result == "duplicate":
                print("이미 신청되어 있습니다.")
                return True
            else:
                print(f"알 수 없는 응답: {result}")
        except Exception as e:
            print(f"신청 요청 실패: {e}")
        return False
    else:
        print(f"[{time.strftime('%H:%M:%S')}] [{TARGET_EVENT_NAME}] 만석 유지 중...")
        return False


def main():
    print("=" * 50)
    print(f"브레베 자동 신청 모니터링 시작")
    print(f"대상: {TARGET_EVENT_NAME} (event_id: {TARGET_EVENT_ID})")
    print(f"체크 주기: {CHECK_INTERVAL}초")
    print("=" * 50)

    print("\nSelenium으로 로그인 중...")
    cookies = get_session_via_selenium()
    session = make_session(cookies)
    print(f"모니터링 시작!\n")

    while True:
        result = check_and_apply(session)

        if result is None:
            print("재로그인 중...")
            try:
                cookies = get_session_via_selenium()
                session = make_session(cookies)
            except Exception as e:
                print(f"재로그인 실패: {e}, {CHECK_INTERVAL}초 후 재시도")
        elif result:
            print("\n✅ 신청 완료! 이메일 확인하세요.")
            break

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
