#!/usr/bin/env python3
"""
activity_weather_backfill.py
라이딩 GPS 포인트에서 30분 간격 샘플링 → Open-Meteo 과거 날씨 조회 → DB 저장

사용법:
    python3 activity_weather_backfill.py              # 날씨 없는 라이딩만
    python3 activity_weather_backfill.py --force       # 전체 재처리
    python3 activity_weather_backfill.py --id 12345    # 특정 라이딩만
"""

import requests
import time
import argparse
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

# ── DB 설정 ──
DB_URL = "mysql+pymysql://tho881:ride1234!@localhost:3306/riding_db?charset=utf8mb4"
engine = create_engine(DB_URL)

# ── Open-Meteo 설정 ──
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
SAMPLE_INTERVAL_MIN = 30  # 30분 간격 샘플링


def get_activities(force=False, activity_id=None):
    """날씨 데이터 없는 라이딩 목록 조회"""
    with engine.connect() as conn:
        if activity_id:
            sql = text("SELECT id, DATE(start_time) as dt, start_time, end_time, "
                        "ROUND(total_distance/1000,1) as km "
                        "FROM activity_core WHERE id = :id")
            rows = conn.execute(sql, {"id": activity_id}).fetchall()
        elif force:
            sql = text("SELECT id, DATE(start_time) as dt, start_time, end_time, "
                        "ROUND(total_distance/1000,1) as km "
                        "FROM activity_core ORDER BY start_time DESC")
            rows = conn.execute(sql).fetchall()
        else:
            sql = text("""
                SELECT ac.id, DATE(ac.start_time) as dt, ac.start_time, ac.end_time,
                       ROUND(ac.total_distance/1000,1) as km
                FROM activity_core ac
                LEFT JOIN activity_weather_point awp ON ac.id = awp.activity_core_id
                WHERE awp.id IS NULL
                ORDER BY ac.start_time DESC
            """)
            rows = conn.execute(sql).fetchall()
    return rows


def sample_points(activity_id):
    """30분 간격으로 GPS 포인트 샘플링"""
    with engine.connect() as conn:
        sql = text("""
            SELECT seq, point_time, lat, lon
            FROM activity_point
            WHERE activity_core_id = :id AND lat IS NOT NULL AND lon IS NOT NULL
            ORDER BY seq
        """)
        rows = conn.execute(sql, {"id": activity_id}).fetchall()

    if not rows:
        return []

    samples = [rows[0]]  # 출발점 항상 포함
    last_time = rows[0][1]  # point_time

    for row in rows[1:]:
        pt_time = row[1]
        if pt_time and last_time:
            diff = (pt_time - last_time).total_seconds()
            if diff >= SAMPLE_INTERVAL_MIN * 60:
                samples.append(row)
                last_time = pt_time

    # 마지막 포인트 추가 (도착점)
    if samples[-1][0] != rows[-1][0]:
        samples.append(rows[-1])

    return samples


def fetch_weather_from_open_meteo(lat, lon, date_str):
    """
    Open-Meteo Archive API로 특정 좌표+날짜의 hourly 날씨 조회
    반환: {hour: {temp, humidity, wind_speed, wind_deg, pressure, weather_code, weather_desc}}
    """
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": date_str,
        "end_date": date_str,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,surface_pressure,weather_code",
        "timezone": "Asia/Seoul"
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        humids = hourly.get("relative_humidity_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        wind_dirs = hourly.get("wind_direction_10m", [])
        pressures = hourly.get("surface_pressure", [])
        codes = hourly.get("weather_code", [])

        result = {}
        for i, t in enumerate(times):
            hour = int(t.split("T")[1].split(":")[0])
            result[hour] = {
                "temperature": temps[i] if i < len(temps) else None,
                "humidity": humids[i] if i < len(humids) else None,
                "wind_speed": winds[i] if i < len(winds) else None,
                "wind_deg": wind_dirs[i] if i < len(wind_dirs) else None,
                "pressure": pressures[i] if i < len(pressures) else None,
                "weather_code": codes[i] if i < len(codes) else None,
                "weather_desc": wmo_code_to_desc(codes[i]) if i < len(codes) else None
            }
        return result

    except Exception as e:
        print(f"  Open-Meteo API error: {e}")
        return {}


def wmo_code_to_desc(code):
    """WMO Weather Code → 한국어 설명"""
    if code is None:
        return None
    WMO = {
        0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
        45: "안개", 48: "짙은 안개",
        51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
        56: "약한 결빙 이슬비", 57: "강한 결빙 이슬비",
        61: "약한 비", 63: "비", 65: "강한 비",
        66: "약한 결빙 비", 67: "강한 결빙 비",
        71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
        80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
        85: "약한 눈소나기", 86: "강한 눈소나기",
        95: "뇌우", 96: "우박 뇌우", 99: "강한 우박 뇌우"
    }
    return WMO.get(code, f"코드{code}")


def process_activity(activity_id, date_str, km):
    """하나의 라이딩에 대해 날씨 채우기"""
    samples = sample_points(activity_id)
    if not samples:
        print(f"  GPS 포인트 없음 - 스킵")
        return 0

    print(f"  샘플 포인트: {len(samples)}개 (30분 간격)")

    # 좌표+날짜 조합별로 API 캐싱 (같은 날짜+비슷한 좌표면 재사용)
    weather_cache = {}
    insert_rows = []

    for seq, pt_time, lat, lon in samples:
        if pt_time is None:
            continue

        date_key = pt_time.strftime("%Y-%m-%d")
        hour = pt_time.hour

        # 좌표를 소수점 2자리로 반올림 (약 1km 그리드)
        grid_key = f"{round(lat, 2)}_{round(lon, 2)}_{date_key}"

        if grid_key not in weather_cache:
            weather_cache[grid_key] = fetch_weather_from_open_meteo(lat, lon, date_key)
            time.sleep(0.3)  # API rate limit

        hourly_data = weather_cache[grid_key]
        weather = hourly_data.get(hour, {})

        insert_rows.append({
            "activity_core_id": activity_id,
            "seq": seq,
            "point_time": pt_time,
            "lat": lat,
            "lon": lon,
            "temperature": weather.get("temperature"),
            "humidity": weather.get("humidity"),
            "wind_speed": weather.get("wind_speed"),
            "wind_deg": weather.get("wind_deg"),
            "pressure": weather.get("pressure"),
            "weather_code": weather.get("weather_code"),
            "weather_desc": weather.get("weather_desc")
        })

    if not insert_rows:
        return 0

    # DB 저장
    with engine.begin() as conn:
        # 기존 데이터 삭제 (재처리 시)
        conn.execute(text("DELETE FROM activity_weather_point WHERE activity_core_id = :id"),
                      {"id": activity_id})

        conn.execute(text("""
            INSERT INTO activity_weather_point
            (activity_core_id, seq, point_time, lat, lon, temperature, humidity,
             wind_speed, wind_deg, pressure, weather_code, weather_desc)
            VALUES
            (:activity_core_id, :seq, :point_time, :lat, :lon, :temperature, :humidity,
             :wind_speed, :wind_deg, :pressure, :weather_code, :weather_desc)
        """), insert_rows)

    # activity_weather 요약도 업데이트 (출발지 기준)
    first = insert_rows[0]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM activity_weather WHERE activity_core_id = :id"),
                      {"id": activity_id})
        conn.execute(text("""
            INSERT INTO activity_weather
            (activity_core_id, temperature, humidity, wind_speed, wind_deg, pressure,
             weather_main, weather_desc)
            VALUES
            (:core_id, :temp, :humidity, :wind_speed, :wind_deg, :pressure, :main, :desc)
        """), {
            "core_id": activity_id,
            "temp": first.get("temperature"),
            "humidity": first.get("humidity"),
            "wind_speed": first.get("wind_speed"),
            "wind_deg": first.get("wind_deg"),
            "pressure": first.get("pressure"),
            "main": first.get("weather_desc"),
            "desc": first.get("weather_desc")
        })

    return len(insert_rows)


def main():
    parser = argparse.ArgumentParser(description="라이딩 날씨 데이터 채우기")
    parser.add_argument("--force", action="store_true", help="전체 재처리")
    parser.add_argument("--id", type=int, help="특정 activity_core_id만 처리")
    args = parser.parse_args()

    activities = get_activities(force=args.force, activity_id=args.id)
    print(f"=== 처리할 라이딩: {len(activities)}개 ===\n")

    total_points = 0
    for i, row in enumerate(activities):
        act_id, dt, start_time, end_time, km = row
        print(f"[{i+1}/{len(activities)}] {dt} - {km}km (ID: {act_id})")

        try:
            count = process_activity(act_id, str(dt), km)
            total_points += count
            print(f"  -> {count}개 날씨 포인트 저장\n")
        except Exception as e:
            print(f"  -> ERROR: {e}\n")

        time.sleep(0.5)

    print(f"=== 완료! 총 {total_points}개 날씨 포인트 저장 ===")


if __name__ == "__main__":
    main()
