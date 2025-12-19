import os
from datetime import datetime
from dotenv import load_dotenv
from httpx import Client
from openai import OpenAI
from cycle_data_parser.model.models import Activity

dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=dotenv_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise EnvironmentError("❌ OPENAI_API_KEY not found in .env or environment variables.")

try:
    http_client = Client()
    client = OpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
    print("✅ OpenAI 클라이언트 초기화 완료 (신버전 호환)")
except Exception as e:
    raise RuntimeError(f"❌ OpenAI 클라이언트 초기화 실패: {e}")


def save_gpt_summary(db, core):
    try:
        prompt = f"""
        다음은 사용자의 자전거 라이딩 데이터입니다:

        • 파일명: {core.activity_id}
        • 거리: {core.total_distance:.1f} km
        • 상승고도: {core.total_ascent} m
        • 평균속도: {core.avg_speed:.1f} km/h
        • 총 주행시간: {core.total_time / 3600:.2f} 시간

        위 데이터를 기반으로 오늘의 라이딩을 한 단락으로 자연스럽게 요약해줘.
        주행 리듬, 코스의 특징, 날씨에 따른 컨디션을 가정해서 써줘.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 자전거 라이딩 분석 전문가입니다."},
                {"role": "user", "content": prompt},
            ],
        )

        summary_text = response.choices[0].message.content.strip()

        summary = Activity(
            activity_date=core.start_time or datetime.now(),
            activity_title=f"라이딩 요약 - {core.activity_id}",
            activity_content=summary_text,
            visibility="PRIVATE",
            device="GARMIN",
            activity_core_id=core.id,
            created_at=datetime.now(),
        )

        db.add(summary)
        db.commit()
        print(f"🧠 GPT 요약 저장 완료 → {core.activity_id}")

    except Exception as e:
        db.rollback()
        print(f"❌ GPT 요약 생성 오류: {e}")

