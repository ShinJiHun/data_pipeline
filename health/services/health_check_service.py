"""
user_health_check 테이블 저장 서비스
"""

import os
import pymysql
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/apps/.env")

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "172.17.0.1"),
    "port":     3306,
    "user":     os.getenv("DB_USERNAME", "tho881"),
    "password": os.environ["DB_PASSWORD"],
    "database": "riding_db",
    "charset":  "utf8mb4",
}


def save_health_check(
    measure_date: str,
    systolic_bp: int        = None,
    diastolic_bp: int       = None,
    heart_rate: int         = None,
    oxygen_saturation: float = None,
    source: str             = "manual",
    note: str               = None,
    user_id: int            = 1,
):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO user_health_check
                    (user_id, measure_date, systolic_bp, diastolic_bp,
                     heart_rate, oxygen_saturation, source, note)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    systolic_bp       = VALUES(systolic_bp),
                    diastolic_bp      = VALUES(diastolic_bp),
                    heart_rate        = VALUES(heart_rate),
                    oxygen_saturation = VALUES(oxygen_saturation),
                    source            = VALUES(source),
                    note              = VALUES(note),
                    updated_at        = CURRENT_TIMESTAMP
            """, (
                user_id, measure_date,
                systolic_bp, diastolic_bp,
                heart_rate, oxygen_saturation,
                source, note,
            ))
        conn.commit()
        print(f"[DB] 저장 완료: {measure_date}")
    finally:
        conn.close()


def get_recent_health_checks(days: int = 30, user_id: int = 1) -> list:
    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT measure_date, systolic_bp, diastolic_bp,
                       heart_rate, oxygen_saturation, source, note
                FROM user_health_check
                WHERE user_id = %s
                  AND measure_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY measure_date DESC
            """, (user_id, days))
            return c.fetchall()
    finally:
        conn.close()
