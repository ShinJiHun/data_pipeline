import os
import re
import io
import time
import pymysql
from datetime import datetime
from google.cloud import vision

# === 경로 설정 ===
IMAGE_DIR = "/home/tho881/ocr/image/"
RESULT_DIR = "/home/tho881/ocr/result/"
os.makedirs(RESULT_DIR, exist_ok=True)

# === DB 연결 ===
def connect_db(retries=3, delay=3):
    for attempt in range(1, retries + 1):
        try:
            conn = pymysql.connect(
                host="riding-db",  # ✅ docker 환경에서는 localhost 대신 서비스명
                user="tho881",
                password="ride1234!",
                database="riding_db",
                port=3306,
                charset="utf8mb4"
            )
            print("✅ DB 연결 성공")
            return conn
        except pymysql.err.OperationalError as e:
            print(f"⚠️ DB 연결 실패 (시도 {attempt}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("❌ DB 연결 실패 (모든 재시도 종료)")

# === 날짜 추출 ===
def extract_inbody_datetime(text, filename):
    """
    OCR 텍스트 또는 파일명에서 날짜 추출
    """
    match_dt = re.search(r"(\d{4})[.\-](\d{2})[.\-](\d{2})", text)
    if match_dt:
        for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(match_dt.group(0), fmt)
            except ValueError:
                continue

    match_file = re.search(r"(20\d{6})", filename)
    if match_file:
        try:
            return datetime.strptime(match_file.group(1), "%Y%m%d")
        except ValueError:
            pass

    return datetime.now()

# === OCR 텍스트에서 인바디 값 추출 ===
def extract_inbody_values(text):
    """
    OCR 결과에서 주요 수치 추출
    """
    clean_text = text.replace("O", "0").replace("o", "0").replace(",", ".").replace("..", ".")
    clean_text = re.sub(r"\s+", " ", clean_text)

    def find(pattern):
        match = re.search(pattern, clean_text)
        if match:
            val = re.sub(r"[^0-9.]", "", match.group(1))
            try:
                return float(val)
            except ValueError:
                return None
        return None

    weight = find(r"체중\s*([0-9]+(?:\.[0-9]+)?)")
    muscle_mass = find(r"골격근량\s*([0-9]+(?:\.[0-9]+)?)")
    body_fat_mass = find(r"체지방량\s*([0-9]+(?:\.[0-9]+)?)")
    visceral_fat_level = find(r"내장지방레벨\s*([0-9]+)")
    bmr = find(r"기초대사량\s*([0-9]+)")

    return {
        "weight": weight,
        "muscle_mass": muscle_mass,
        "body_fat_mass": body_fat_mass,
        "visceral_fat_level": visceral_fat_level,
        "bmr": bmr
    }

# === OCR 실행 ===
def process_image(image_path):
    client = vision.ImageAnnotatorClient()
    with io.open(image_path, "rb") as image_file:
        content = image_file.read()
    image = vision.Image(content=content)
    response = client.text_detection(image=image)
    texts = response.text_annotations

    if not texts:
        print(f"⚠️ No text detected in {image_path}")
        return ""
    detected_text = texts[0].description
    print(f"[🔍] OCR Text from {os.path.basename(image_path)}:\n{detected_text}")
    return detected_text

# === DB 존재 확인 ===
def is_already_exists(conn, filename):
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM user_body_record WHERE filename=%s", (filename,))
        (count,) = cursor.fetchone()
        return count > 0

# === DB 저장 (INSERT or UPDATE) ===
def save_result_to_db(conn, filename, text):
    try:
        record_date = extract_inbody_datetime(text, filename)
        values = extract_inbody_values(text)

        with conn.cursor() as cursor:
            if is_already_exists(conn, filename):
                sql = """
                    UPDATE user_body_record
                    SET
                        weight=%s,
                        muscle_mass=%s,
                        body_fat_mass=%s,
                        visceral_fat_level=%s,
                        bmr=%s,
                        record_date=%s,
                        ocr_text=%s,
                        updated_at=NOW()
                    WHERE filename=%s
                """
                cursor.execute(sql, (
                    values["weight"],
                    values["muscle_mass"],
                    values["body_fat_mass"],
                    values["visceral_fat_level"],
                    values["bmr"],
                    record_date,
                    text,
                    filename
                ))
                print(f"🔁 Updated existing record for {filename}")
            else:
                sql = """
                    INSERT INTO user_body_record
                        (user_id, record_date, filename, ocr_text,
                         weight, muscle_mass, body_fat_mass, visceral_fat_level, bmr, created_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cursor.execute(sql, (
                    1,  # 기본 사용자 (shin ji-hoon)
                    record_date,
                    filename,
                    text,
                    values["weight"],
                    values["muscle_mass"],
                    values["body_fat_mass"],
                    values["visceral_fat_level"],
                    values["bmr"]
                ))
                print(f"✅ Inserted new record for {filename}")

        conn.commit()
        print(f"   ↳ 체중={values['weight']} / 근육={values['muscle_mass']} / 지방={values['body_fat_mass']} / "
              f"내장={values['visceral_fat_level']} / BMR={values['bmr']} / 날짜={record_date}")

    except Exception as e:
        print(f"❌ DB 저장 중 오류: {e}")

# === 메인 실행 ===
def main():
    print("🚀 OCR Batch Start")
    conn = connect_db()

    image_files = [f for f in os.listdir(IMAGE_DIR)
                   if f.lower().endswith((".png", ".jpg", ".jpeg"))]

    print(f"📁 총 {len(image_files)}개 파일 감지됨")

    for filename in image_files:
        image_path = os.path.join(IMAGE_DIR, filename)
        print(f"📂 Processing: {image_path}")

        text = process_image(image_path)
        if text:
            save_result_to_db(conn, filename, text)
            result_file = os.path.join(RESULT_DIR, f"{filename}.txt")
            with open(result_file, "w") as f:
                f.write(text)
        else:
            print(f"⚠️ {filename} OCR 실패 — 건너뜀")

    conn.close()
    print("✅ OCR Batch Completed Successfully")

if __name__ == "__main__":
    main()

