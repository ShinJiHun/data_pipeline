import os
import json
import shutil
import base64
import io

from PIL import Image
from pathlib import Path
from sqlalchemy import create_engine, text
import anthropic
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

# ========== 설정 ==========
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DB_URL = (
    f"mysql+pymysql://{os.getenv('DB_USERNAME', 'tho881')}:{os.getenv('DB_PASSWORD', 'ride1234!')}"
    f"@{os.getenv('DB_HOST', '172.17.0.1')}:3306/riding_db"
)

FITDAYS_RAW_DIR       = "/mnt/200gb/NAS/fitdays/raw"
FITDAYS_PROCESSED_DIR = "/mnt/200gb/NAS/fitdays/processed"
FITDAYS_FAILED_DIR    = "/mnt/200gb/NAS/fitdays/failed"

os.makedirs(FITDAYS_RAW_DIR,       exist_ok=True)
os.makedirs(FITDAYS_PROCESSED_DIR, exist_ok=True)
os.makedirs(FITDAYS_FAILED_DIR,    exist_ok=True)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
engine = create_engine(DB_URL, pool_pre_ping=True)


def resize_image(image_path: str, max_height: int = 3000) -> tuple[bytes, str]:
    with Image.open(image_path) as img:
        if img.mode == "RGBA":
            img = img.convert("RGB")
        w, h = img.size
        if h > max_height:
            ratio = max_height / h
            new_w = int(w * ratio)
            img = img.resize((new_w, max_height), Image.LANCZOS)
            print(f"[리사이즈] {w}x{h} → {new_w}x{max_height}")
        else:
            print(f"[이미지] {w}x{h} (리사이즈 불필요)")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), "image/png"


def call_claude(image_path: str) -> dict:
    print(f"[Claude] FitDays 이미지 분석 시작: {image_path}")

    image_bytes, media_type = resize_image(image_path)
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """이 FitDays 체성분 결과 화면에서 다음 정보를 추출해서 JSON으로 반환해주세요:

- record_date: 화면 상단의 날짜 (YYYY-MM-DD 형식, 예: 2026/03/25 → 2026-03-25)
- weight: 체중 (kg)
- bmi: BMI
- body_fat_percentage: 체지방률 (%)
- body_fat_mass: 체지방량 (kg)
- lean_body_mass: 제지방체중 (kg)
- skeletal_muscle_mass: 골격근량 (kg)
- muscle_mass: 근육량 (kg)
- bone_mass: 골질량 (kg)
- total_body_water: 체수분 (kg)
- protein: 체단백질 (kg)
- mineral: 무기질 (kg)
- visceral_fat_index: 내장 지방지수 (숫자)
- bmr: 기초대사량 (kcal, 숫자만)
- body_age: 신체나이
- waist_hip_ratio: 복부비만율
- standard_weight: 표준 체중 (kg)
- obesity_degree: 비만도 (%, 숫자만)

숫자만 추출하고 단위는 제외하세요.
없는 항목은 null로 반환하세요.
JSON만 반환하고 다른 텍스트는 포함하지 마세요."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    try:
        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            raw_text = raw_text.rsplit("```", 1)[0]
        data = json.loads(raw_text)
        result = data[0] if isinstance(data, list) else data
        print(f"[Claude] 추출 성공: {result}")
        return result
    except Exception as e:
        print(f"[Claude] ⚠️ 응답 파싱 실패: {e}")
        print(f"[Claude] 원본 응답: {response.content[0].text}")
        raise e


def save_to_db(data: dict, filename: str, user_id: int = 1):
    """user_fitdays_record 테이블에 저장"""
    def to_float(val):
        try:
            return float(val) if val is not None else None
        except:
            return None

    def to_int(val):
        try:
            return int(float(val)) if val is not None else None
        except:
            return None

    sql = text("""
        INSERT INTO user_fitdays_record
        (user_id, record_date,
         weight, bmi, body_fat_percentage, body_fat_mass,
         lean_body_mass, skeletal_muscle_mass, muscle_mass, bone_mass,
         total_body_water, protein, mineral,
         visceral_fat_index, bmr, body_age,
         waist_hip_ratio, standard_weight, obesity_degree,
         source, raw_filename, raw_llm_json)
        VALUES
        (:user_id, :record_date,
         :weight, :bmi, :pbf, :fat,
         :lbm, :smm, :muscle, :bone,
         :water, :protein, :mineral,
         :vfi, :bmr, :body_age,
         :whr, :std_weight, :obesity,
         'FITDAYS_OCR', :fname, :raw_llm_json)
        ON DUPLICATE KEY UPDATE
          weight               = :weight,
          bmi                  = :bmi,
          body_fat_percentage  = :pbf,
          body_fat_mass        = :fat,
          lean_body_mass       = :lbm,
          skeletal_muscle_mass = :smm,
          muscle_mass          = :muscle,
          bone_mass            = :bone,
          total_body_water     = :water,
          protein              = :protein,
          mineral              = :mineral,
          visceral_fat_index   = :vfi,
          bmr                  = :bmr,
          body_age             = :body_age,
          waist_hip_ratio      = :whr,
          standard_weight      = :std_weight,
          obesity_degree       = :obesity,
          raw_filename         = :fname,
          raw_llm_json         = :raw_llm_json
    """)

    with engine.begin() as conn:
        conn.execute(sql, {
            "user_id":      user_id,
            "record_date":  data.get("record_date"),
            "weight":       to_float(data.get("weight")),
            "bmi":          to_float(data.get("bmi")),
            "pbf":          to_float(data.get("body_fat_percentage")),
            "fat":          to_float(data.get("body_fat_mass")),
            "lbm":          to_float(data.get("lean_body_mass")),
            "smm":          to_float(data.get("skeletal_muscle_mass")),
            "muscle":       to_float(data.get("muscle_mass")),
            "bone":         to_float(data.get("bone_mass")),
            "water":        to_float(data.get("total_body_water")),
            "protein":      to_float(data.get("protein")),
            "mineral":      to_float(data.get("mineral")),
            "vfi":          to_float(data.get("visceral_fat_index")),
            "bmr":          to_int(data.get("bmr")),
            "body_age":     to_int(data.get("body_age")),
            "whr":          to_float(data.get("waist_hip_ratio")),
            "std_weight":   to_float(data.get("standard_weight")),
            "obesity":      to_float(data.get("obesity_degree")),
            "fname":        filename,
            "raw_llm_json": json.dumps(data, ensure_ascii=False),
        })

    print(f"[DB] FitDays 저장 완료: {filename}")


def process_image_file(image_path: str, user_id: int = 1):
    filename = os.path.basename(image_path)
    try:
        print(f"\n[처리 시작] {filename}")
        data = call_claude(image_path)
        save_to_db(data, filename, user_id)
        dest = os.path.join(FITDAYS_PROCESSED_DIR, filename)
        shutil.move(image_path, dest)
        print(f"[✅ 성공] {filename} → processed/")
        return {"status": "success", "data": data}
    except Exception as e:
        print(f"[❌ 실패] {filename}: {e}")
        dest = os.path.join(FITDAYS_FAILED_DIR, filename)
        try:
            shutil.move(image_path, dest)
        except Exception as move_err:
            print(f"[이동 실패] {move_err}")
        return {"status": "error", "message": str(e)}


def batch_process_all():
    files = sorted([
        f for f in os.listdir(FITDAYS_RAW_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ])
    print(f"\n[일괄 처리] {len(files)}개 파일 발견")
    for fname in files:
        process_image_file(os.path.join(FITDAYS_RAW_DIR, fname), user_id=1)
    print(f"\n[완료] 일괄 처리 종료")


if __name__ == "__main__":
    batch_process_all()
