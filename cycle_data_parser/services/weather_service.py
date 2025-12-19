import math
from cycle_data_parser.model.models import ActivityWeather
from cycle_data_parser.analysis.metrics_utils import calc_air_density


def safe(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except:
        return default


def fetch_weather(lat, lon, timestamp):
    """
    Replace with actual weather API logic if needed.
    """
    return {
        "temperature": 12.0,
        "humidity": 55.0,
        "wind_speed": 3.0,
        "wind_direction": 300.0,
        "condition": "Cloudy"
    }


def calc_wind_factor(speed, heading, wind_speed, wind_dir):
    """
    Compute wind impact factor considering direction.
    """
    if speed < 0.5:
        return 1.0

    angle = abs(heading - wind_dir) % 360
    if angle > 180:
        angle = 360 - angle

    wind_component = wind_speed * math.cos(math.radians(angle))
    effective_speed = max(0.1, speed + wind_component)

    return max(0.3, effective_speed / speed)


def save_weather_info(db, core, virtual_powers, dt_list, speed_list, headings):
    try:
        if not virtual_powers or not dt_list:
            print("virtual power missing -> skip weather correction")
            return

        weather = fetch_weather(core.start_lat, core.start_lon, core.start_time)

        if not weather:
            print("no weather data")
            return

        temp = safe(weather.get("temperature"))
        humidity = safe(weather.get("humidity"))
        wind_speed = safe(weather.get("wind_speed"))
        wind_dir = safe(weather.get("wind_direction"))

        # Save base weather info
        db.add(ActivityWeather(
            activity_core_id=core.id,
            temperature=temp,
            humidity=humidity,
            wind_speed=wind_speed,
            condition=weather.get("condition")
        ))

        # Air density correction
        rho = calc_air_density(temp, humidity)
        rho_factor = rho / 1.226  # relative to standard air density

        corrected_list = []

        N = min(len(virtual_powers), len(speed_list), len(headings))

        for i in range(N):
            base_p = virtual_powers[i]
            speed = speed_list[i]
            heading = headings[i]

            wind_factor = calc_wind_factor(speed, heading, wind_speed, wind_dir)

            corrected = base_p * rho_factor * wind_factor
            corrected_list.append(corrected)

        avg_corr = sum(corrected_list) / len(corrected_list)
        work_corr = sum([p * dt for p, dt in zip(corrected_list, dt_list[:N])]) / 1000.0

        core.avg_power_weather_adj = avg_corr
        core.total_work_weather_adj_kj = work_corr

        db.commit()
        print(f"Weather correction OK: avg={avg_corr:.1f}W")

    except Exception as e:
        db.rollback()
        print(f"Weather processing error: {e}")

