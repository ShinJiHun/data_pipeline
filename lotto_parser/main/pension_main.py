from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text
import time
import socket
import sys
from datetime import datetime, timedelta

# ======================================================
# ✅ DB 연결 설정 (Docker / Local 자동 인식)
# ======================================================
def detect_db_env():
    try:
        socket.gethostbyname("lotto-db")
        return "lotto-db", 3306, "🐳 Docker"
    except socket.gaierror:
        return "127.0.0.1", 3306, "☁️ Local(GCP VM)"


def build_db_url(host, port):
    return f"mysql+pymysql://tho881:ride1234!@{host}:{port}/lotto_db"


# ======================================================
# ✅ 연금복권 회차별 방송 날짜 계산
# ======================================================
def calculate_draw_date(round_num):
    """
    331회차 = 2024-09-03 (목요일) 기준으로 역산
    매주 목요일 방송이므로 7일씩 감소
    """
    latest_round = 331
    latest_date = datetime(2024, 9, 3)  # 331회차 = 2024-09-03

    days_diff = (latest_round - round_num) * 7
    draw_date = latest_date - timedelta(days=days_diff)

    return draw_date.strftime("%Y-%m-%d")


# ======================================================
# ✅ 연금복권 데이터 크롤링 (배치 저장)
# ======================================================
def scrape_and_save_pension_lottery(engine, batch_size=30):
    """
    Playwright로 연금복권 사이트에서 크롤링하고 배치 단위로 저장
    30개씩 수집 → 저장 → 반복
    """
    total_inserted = 0
    total_skipped = 0
    batch_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto("https://www.dhlottery.co.kr/pt720/result", timeout=30000)
            page.wait_for_selector(".swiper-slide", timeout=10000)

            print("🔄 연금복권 크롤링 시작...")
            no_new_rounds = 0
            max_no_new = 5  # 연속으로 새 데이터가 없으면 중단

            while no_new_rounds < max_no_new:
                html_content = page.content()
                soup = BeautifulSoup(html_content, 'html.parser')

                # 현재 화면에 로드된 슬라이드들 탐색
                slides = soup.find_all('div', class_='swiper-slide')
                slides_before = len(batch_data)

                for slide in slides:
                    epsd_elem = slide.find('span', class_='psltEpsd')
                    if not epsd_elem:
                        continue

                    round_text = epsd_elem.text.strip()  # "330회차" 형식

                    # 중복 수집 방지
                    if any(r['round_no'] == round_text for r in batch_data):
                        continue

                    wf_balls = slide.find_all('div', class_='result-wfBall')
                    if len(wf_balls) < 2:
                        continue

                    try:
                        # 1등 번호
                        first_wrap = wf_balls[0]
                        jo_elem = first_wrap.find('span', class_='pension-jo')
                        jo_num_text = jo_elem.text.strip() if jo_elem else "1"

                        # "4조" → 4
                        jo_num = int(jo_num_text.replace('조', '').strip()) if jo_num_text else 1

                        first_numbers = [
                            span.text.strip()
                            for span in first_wrap.find_all('span', class_=lambda x: x and x.startswith('wf-') and x != 'pension-jo')
                        ]

                        # 보너스 번호
                        bonus_wrap = wf_balls[1]
                        bonus_numbers = [
                            span.text.strip()
                            for span in bonus_wrap.find_all('span', class_=lambda x: x and x.startswith('wf-'))
                        ]

                        # 회차번호로 추첨 날짜 계산 (매주 목요일)
                        round_num = int(round_text.replace('회차', ''))
                        draw_date = calculate_draw_date(round_num)

                        batch_data.append({
                            "round_no": round_text,
                            "round_num": round_num,
                            "winning_jo": jo_num,
                            "winning_numbers": ''.join(first_numbers),
                            "bonus_number": ''.join(bonus_numbers),
                            "draw_date": draw_date
                        })

                        # 배치 크기에 도달하면 저장
                        if len(batch_data) >= batch_size:
                            inserted, skipped = save_batch(engine, batch_data)
                            total_inserted += inserted
                            total_skipped += skipped
                            print(f"  💾 배치 저장: +{inserted} (누계: {total_inserted}), 스킵: +{skipped}")
                            batch_data = []

                    except Exception as e:
                        print(f"⚠️  회차 파싱 오류: {round_text} - {str(e)}")
                        continue

                # 새로운 데이터가 수집되었는지 확인
                if len(batch_data) == slides_before:
                    no_new_rounds += 1
                    print(f"  새 데이터 없음 ({no_new_rounds}/{max_no_new})")
                else:
                    no_new_rounds = 0
                    print(f"  수집 중: {len(batch_data)}개 대기중...")

                # 이전 회차 버튼 클릭
                try:
                    prev_btn = page.locator(".swiper-button-prev")
                    if prev_btn.is_visible():
                        prev_btn.click()
                        time.sleep(0.3)
                    else:
                        print("  이전 버튼 없음 - 크롤링 종료")
                        break
                except Exception as e:
                    print(f"  버튼 클릭 실패: {str(e)}")
                    break

            # 남은 데이터 저장 (배치 미만)
            if batch_data:
                inserted, skipped = save_batch(engine, batch_data)
                total_inserted += inserted
                total_skipped += skipped
                print(f"  💾 최종 배치 저장: +{inserted}, 스킵: +{skipped}")

            browser.close()
            print(f"\n✅ 크롤링 완료!")

        except Exception as e:
            print(f"❌ 크롤링 오류: {str(e)}")
            browser.close()
            sys.exit(1)

    return total_inserted, total_skipped


# ======================================================
# ✅ 배치 저장 함수
# ======================================================
def save_batch(engine, batch_data):
    """
    배치 데이터를 DB에 저장
    """
    inserted = 0
    skipped = 0

    with engine.begin() as conn:
        for item in batch_data:
            # 이미 존재하는 회차는 스킵
            existing = conn.execute(
                text("SELECT id FROM pension_numbers WHERE round_number = :round"),
                {"round": item['round_num']}
            ).scalar()

            if existing:
                skipped += 1
                continue

            try:
                conn.execute(text("""
                    INSERT INTO pension_numbers (
                        id, round_number, winning_jo, winning_numbers, bonus_number, draw_date, created_at
                    ) VALUES (
                        :round_num, :round_num, :winning_jo, :winning_nums, :bonus_num, :draw_date, :created_at
                    )
                """), {
                    "round_num": item['round_num'],
                    "winning_jo": item['winning_jo'],
                    "winning_nums": item['winning_numbers'],
                    "bonus_num": item['bonus_number'],
                    "draw_date": item['draw_date'],
                    "created_at": datetime.now()
                })

                inserted += 1

            except Exception as e:
                print(f"⚠️  저장 오류 [{item['round_no']}]: {str(e)}")
                continue

    return inserted, skipped


# ======================================================
# ✅ 통계 정보
# ======================================================
def get_pension_stats(engine):
    """
    pension_numbers 테이블의 통계 조회
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            SELECT
                COUNT(*) as total_rounds,
                MAX(round_number) as latest_round,
                MIN(round_number) as oldest_round
            FROM pension_numbers
        """)).fetchone()

        return {
            "total_rounds": result[0],
            "latest_round": result[1],
            "oldest_round": result[2]
        }


# ======================================================
# ✅ main
# ======================================================
def main():
    print("=" * 60)
    print("🎰 연금복권 배치 저장 스크립트")
    print("=" * 60)

    # DB 연결
    host, port, env = detect_db_env()
    print(f"\n🛰️  DB 환경: {env} → {host}:{port}")

    engine = create_engine(build_db_url(host, port), echo=False)

    # 크롤링 및 배치 저장
    print("\n🌐 크롤링 및 배치 저장 시작... (30개씩)\n")
    inserted, skipped = scrape_and_save_pension_lottery(engine, batch_size=30)

    # 최종 통계
    print("\n📊 최종 통계:")
    stats = get_pension_stats(engine)
    print(f"   총 회차 수: {stats['total_rounds']}")
    print(f"   최신 회차: {stats['latest_round']}회차")
    print(f"   가장 오래된 회차: {stats['oldest_round']}회차")
    print(f"   이번 실행: +{inserted} 신규, {skipped} 스킵")

    print("\n🎉 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
