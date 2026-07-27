# /app/riding_data_parser/utils/wind_power.py
import requests
import math
from datetime import datetime, timezone

# ----------------------------------------
# Open-Meteo API에서 풍속/풍향 가져오기
# ----------------------------------------
def get_wind_info(lat: float, lon: float, timestamp: datetime):
    """
    특정 시간·위치의 풍속(m/s)과 풍향(°: 바람이 불어오는 방향)을 반환
    """
    # UTC 기준으로 변환
    timestamp_utc = timestamp.astimezone(timezone.utc)
    date_str = timestamp_utc.strftime("%Y-%m-%d")
    hour = timestamp_utc.hour

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_direction_10m"
        f"&start_date={date_str}&end_date={date_str}&timezone=UTC"
    )

    res = requests.get(url)
    res.raise_for_status()
    data = res.json()

    # 가장 가까운 시간대 찾기
    hours = data["hourly"]["time"]
    speeds = data["hourly"]["wind_speed_10m"]
    dirs = data["hourly"]["wind_direction_10m"]

    # 해당 시각에 가장 근접한 index 선택
    idx = min(range(len(hours)), key=lambda i: abs(int(hours[i][-5:-3]) - hour))
    wind_speed = speeds[idx]
    wind_dir = dirs[idx]
    return wind_speed, wind_dir  # m/s, degrees


# ----------------------------------------
# 풍향 보정 포함 파워 계산
# ----------------------------------------
def calc_wind_corrected_power(
    ride_speed: float,  # m/s
    heading_deg: float,  # 라이더 진행방향 (0~360)
    wind_speed: float,  # m/s
    wind_dir: float,  # 바람이 불어오는 방향 (0~360)
    CdA: float = 0.32,
    Crr: float = 0.004,
    mass: float = 80.0,  # kg
):
    """
    풍향/풍속 반영한 공기저항 포함 파워 계산 (W)
    """
    air_density = 1.226  # kg/m³
    g = 9.80665

    # 상대풍속 계산
    # wind_dir은 "바람이 불어오는 방향" → 역풍이면 +, 순풍이면 -
    wind_angle = math.radians(heading_deg - wind_dir)
    relative_wind = wind_speed * math.cos(wind_angle)
    effective_speed = ride_speed + relative_wind

    # 공기저항력 (aero), 구름저항 (roll)
    aero = 0.5 * air_density * CdA * effective_speed**2
    roll = mass * g * Crr
    total_force = aero + roll

    power = total_force * ride_speed  # 실제 주행속도 기준
    return round(power, 2), round(effective_speed, 2), round(relative_wind, 2)


# ----------------------------------------
# 테스트 예시
# ----------------------------------------
if __name__ == "__main__":
    # 예: 2025-10-23 07:30, 서울 인근
    lat, lon = 37.5, 127.0
    ts = datetime(2025, 10, 23, 7, 30, tzinfo=timezone.utc)
    wind_speed, wind_dir = get_wind_info(lat, lon, ts)

    # 라이더 주행정보
    ride_speed = 8.3  # m/s ≈ 30 km/h
    heading = 90  # 동쪽
    mass = 80.0  # kg (몸무게+자전거)
    power, eff_speed, rel_wind = calc_wind_corrected_power(
        ride_speed, heading, wind_speed, wind_dir, mass=mass
    )

    print(f"🌬️ 바람속도: {wind_speed:.1f} m/s @ {wind_dir:.0f}°")
    print(f"➡️ 상대풍속: {rel_wind:+.1f} m/s, 유효속도: {eff_speed:.1f} m/s")
    print(f"⚡ 추정 파워(풍향 보정): {power:.1f} W")

