import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# 1. 설정 (본인의 정보를 입력하세요)
USER_ID = "20190"
USER_PW = "tho881@naver.com"
TARGET_BREVET_NAME = "서울 300K-서"  # 확인하려는 대회명

def check_registration_status():
    # 크롬 드라이버 자동 설정
    options = webdriver.ChromeOptions()
    # options.add_argument('--headless') # 창을 띄우지 않고 실행하려면 주석 해제
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        # 란도너스 로그인 페이지 접속
        driver.get("http://www.korearandonneurs.kr/reg/login.jsp")
        time.sleep(2)

        # 로그인 수행
        driver.find_element(By.NAME, "memberID").send_keys(USER_ID)
        driver.find_element(By.NAME, "email").send_keys(USER_PW)
        driver.find_element(By.XPATH, "//button[contains(text(), 'Login')]").click()
        time.sleep(2)

        # 신청 페이지 이동 (로그인 후 자동으로 이동되나 확정적 이동을 위해)
        driver.get("http://www.korearandonneurs.kr/reg/register.jsp")
        time.sleep(2)

        # 페이지 내에서 TARGET_BREVET_NAME을 포함하는 섹션 찾기
        brevets = driver.find_elements(By.CLASS_NAME, "brevet-item") # 실제 사이트 구조에 맞춰 클래스명 확인 필요
        
        # 간단한 방법: 전체 페이지 텍스트에서 매진 문구 확인
        page_text = driver.page_source
        
        full_msg = "이 대회의 제한 인원이 다 찼습니다"
        
        if TARGET_BREVET_NAME in page_text:
            if full_msg in page_text:
                print(f"[{time.strftime('%H:%M:%S')}] {TARGET_BREVET_NAME}: 아직 자리가 없습니다.")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] ★★★ {TARGET_BREVET_NAME}: 빈 자리가 발생했을 가능성이 높습니다! 확인하세요! ★★★")
                # 여기에 텔레그램 봇이나 알림 기능을 추가하면 좋습니다.
        else:
            print("해당 대회명을 찾을 수 없습니다. 이름을 다시 확인해주세요.")

    except Exception as e:
        print(f"오류 발생: {e}")
    finally:
        driver.quit()

# 실행
if __name__ == "__main__":
    check_registration_status()
