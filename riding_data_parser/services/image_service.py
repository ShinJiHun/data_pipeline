import os
import requests
import polyline
from urllib.parse import quote
from datetime import datetime
from PIL import Image
from io import BytesIO
from riding_data_parser.model.models import ActivityImage, ActivityCore

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
IMAGE_SAVE_DIR = "/home/tho881/project/NAS/images/maps/"  # 저장 경로 수정 가능


def save_activity_image(db, core: ActivityCore):
    """
    ✅ Google StaticMap API로 활동 경로 이미지를 생성 및 저장
    """
    try:
        if not GOOGLE_MAPS_API_KEY:
            print("⚠️ GOOGLE_MAPS_API_KEY 환경 변수가 설정되지 않았습니다.")
            return

        if not core.polyline:
            print(f"⚠️ ActivityImage 생략: {core.activity_id} (polyline 없음)")
            return

        # 저장 폴더 생성
        os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)

        # polyline decoding
        decoded_points = decode_polyline_safe(core.polyline)
        simplified_points = decoded_points[::10] if len(decoded_points) > 1000 else decoded_points

        encoded = polyline.encode(simplified_points)
        encoded_safe = quote(encoded)

        # Google Static Map URL
        map_url = (
            f"https://maps.googleapis.com/maps/api/staticmap?"
            f"size=800x600&path=enc:{encoded_safe}&key={GOOGLE_MAPS_API_KEY}"
        )

        response = requests.get(map_url)
        if response.status_code != 200:
            print(f"⚠️ Google StaticMap 호출 실패: {response.status_code}")
            return

        # 파일명 생성 (activity_id 사용)
        base_name = core.activity_id.replace(".fit", "")
        image_filename = f"{base_name}.png"
        image_path = os.path.join(IMAGE_SAVE_DIR, image_filename)

        # 이미지 파일 저장
        image = Image.open(BytesIO(response.content))
        image.save(image_path)

        # DB 등록
        image_entity = ActivityImage(
            image_url=image_path,
            activity_core_id=core.id,
            created_at=datetime.now(),
        )
        db.add(image_entity)
        db.commit()

        print(f"🗺 지도 이미지 저장 완료 → {image_filename}")

    except Exception as e:
        print(f"❌ 이미지 생성 중 오류: {e}")
        db.rollback()


def decode_polyline_safe(polyline_str: str):
    """Polyline 문자열을 안전하게 디코드 (잘못된 경우 빈 리스트 반환)"""
    try:
        return polyline.decode(polyline_str)
    except Exception:
        return []

