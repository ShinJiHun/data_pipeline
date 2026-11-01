import math

# --------------------------------------
# 공기 밀도 계산 (기온 °C, 습도 %)
# --------------------------------------
def calc_air_density(temp_c, humidity):
    temp_k = temp_c + 273.15
    rh = humidity / 100
    p = 101325  # 기본 기압
    
    # 수증기 보정
    return (p / (287.05 * temp_k)) * (1 - 0.378 * rh)


# --------------------------------------
# 스무딩
# --------------------------------------
def smooth(values, window=7):
    if len(values) < window:
        return values

    half = window // 2
    smoothed = []

    for i in range(len(values)):
        s = max(0, i-half)
        e = min(len(values), i+half+1)
        smoothed.append(sum(values[s:e])/(e - s))

    return smoothed


# --------------------------------------
# 거리 계산
# --------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)

    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# --------------------------------------
# 경사도 계산
# --------------------------------------
def calculate_slopes(lat_list, lon_list, ele_list):
    slopes = []
    ele_list = smooth(ele_list, window=9)

    for i in range(1, len(lat_list)):
        d = haversine(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])
        if d < 1:
            slopes.append(0)
            continue
        dh = ele_list[i] - ele_list[i-1]
        slope = (dh / d) * 100

        # 스파이크 제거
        if abs(slope) > 25:
            slope = slopes[-1] if slopes else 0

        slopes.append(slope)

    return slopes


# --------------------------------------
# 업힐/평지/다운 거리 계산
# --------------------------------------
def calc_up_flat_down(lat_list, lon_list, slopes):
    up = flat = down = 0.0
    for i in range(1, len(slopes)):
        d = haversine(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])

        if slopes[i] > 2.0:
            up += d
        elif slopes[i] < -2.0:
            down += d
        else:
            flat += d

    return up, flat, down


# --------------------------------------
# 기본 메트릭 계산
# --------------------------------------
def compute_basic_metrics(lat, lon, ele, ts):
    total_distance = 0.0
    total_time = (ts[-1] - ts[0]).total_seconds()

    speed_list = [0.0]
    dt_list = [1]

    moving_time = 0.0
    speed_sum = 0
    speed_count = 0
    max_speed = 0

    for i in range(1, len(lat)):
        d = haversine(lat[i-1], lon[i-1], lat[i], lon[i])
        dt = (ts[i] - ts[i-1]).total_seconds()

        if dt < 0.5 or dt > 120:
            speed_list.append(0)
            dt_list.append(1)
            continue

        v = d / dt  # m/s
        v_kmh = v * 3.6

        speed_list.append(v)
        dt_list.append(dt)

        if d > 0.5:
            total_distance += d

        if v_kmh >= 2:
            moving_time += dt

        if v_kmh >= 1:
            speed_sum += v_kmh
            speed_count += 1
            max_speed = max(max_speed, v_kmh)

    avg_speed = speed_sum / speed_count if speed_count > 0 else 0
    max_speed = min(max_speed, 90)

    return {
        "total_distance": total_distance,
        "total_time": total_time,
        "moving_time": moving_time,
        "avg_speed": avg_speed,
        "max_speed": max_speed,
        "speed_list": speed_list,
        "dt_list": dt_list
    }


# --------------------------------------
# Virtual Power 계산
# --------------------------------------
VP_MAX_SPEED_MS = 25.0      # 90km/h 초과는 GPS 튐으로 보고 자름
VP_SMOOTH_WINDOW = 5        # 속도 이동 중앙값 윈도우 (포인트 수)
VP_MAX_ACCEL = 1.5          # |dv/dt| 상한 (m/s²) — 자전거로 낼 수 있는 가감속 수준
VP_MAX_SLOPE = 25.0         # 경사 상한 (%)
VP_MAX_POWER = 1200.0       # 추정 파워 상한 (W)


def _smooth_speeds(speed_list):
    """GPS 속도 튐 제거: 상한 클램프 후 이동 중앙값."""
    clamped = [min(max(v or 0.0, 0.0), VP_MAX_SPEED_MS) for v in speed_list]
    half = VP_SMOOTH_WINDOW // 2
    out = []
    for i in range(len(clamped)):
        window = sorted(clamped[max(0, i - half):i + half + 1])
        out.append(window[len(window) // 2])
    return out


def compute_virtual_power(speed_list, slope_list, dt_list,
                          mass=95, Crr=0.004, CdA=0.32, rho=1.226):
    """speed_list는 m/s. 속도 튐/가속 항 폭주를 막기 위해 평활화·상한을 적용한다."""
    g = 9.80665
    powers = []
    speed_list = _smooth_speeds(speed_list)
    prev_v = speed_list[0] if speed_list else 0.0

    for i in range(len(speed_list)):
        v = speed_list[i]
        slope = slope_list[i] if i < len(slope_list) and slope_list[i] is not None else 0
        slope = min(max(slope, -VP_MAX_SLOPE), VP_MAX_SLOPE) / 100.0
        dt = dt_list[i]

        # 구름저항
        P_roll = Crr * mass * g * v

        # 경사저항
        P_gravity = mass * g * slope * v

        # 공기저항
        P_aero = 0.5 * rho * CdA * v * v * v

        # 가속도 파워
        dv = (v - prev_v) / dt if dt > 0 else 0
        dv = min(max(dv, -VP_MAX_ACCEL), VP_MAX_ACCEL)
        P_acc = mass * dv * v
        prev_v = v

        total_p = min(max(0, P_roll + P_gravity + P_aero + P_acc), VP_MAX_POWER)
        powers.append(total_p)

    return powers

def calc_heading(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    heading = (math.degrees(math.atan2(y, x)) + 360) % 360
    return heading

def compute_headings(lat_list, lon_list):
    headings = [0.0]
    for i in range(1, len(lat_list)):
        h = calc_heading(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])
        headings.append(h)
    return headings

def calc_calories_from_MET(avg_speed_kmh, moving_time_sec, weight=85):
    hours = moving_time_sec / 3600

    if avg_speed_kmh < 10:
        MET = 4.0
    elif avg_speed_kmh < 14:
        MET = 6.8
    elif avg_speed_kmh < 16:
        MET = 8.0
    elif avg_speed_kmh < 20:
        MET = 10.0
    elif avg_speed_kmh < 22:
        MET = 12.0
    elif avg_speed_kmh < 25:
        MET = 14.0
    else:
        MET = 16.0

    return MET * weight * hours


def calc_calories_from_power(total_work_kj, elapsed_time_sec=None, bmr_daily=None):
    if total_work_kj is None:
        return None
    
    efficiency = 0.22
    total_energy_kj = total_work_kj / efficiency
    cycling_calories = total_energy_kj / 4.184
    
    if elapsed_time_sec:
        if bmr_daily:
            bmr_per_hour = bmr_daily / 24
        else:
            bmr_per_hour = 75  # 기본값
        bmr_calories = (elapsed_time_sec / 3600) * bmr_per_hour
        cycling_calories += bmr_calories
    
    return cycling_calories


def compute_total_calories(total_work_kj, avg_speed_kmh, moving_time_sec, elapsed_time_sec=None, bmr_daily=1700, weight=85):
    """스트라바 스타일 칼로리 계산"""
    hours = elapsed_time_sec / 3600 if elapsed_time_sec else moving_time_sec / 3600
    
    # 속도 기반 MET (스트라바 추정)
    if avg_speed_kmh < 16:
        MET = 4.0
    elif avg_speed_kmh < 20:
        MET = 6.0
    elif avg_speed_kmh < 24:
        MET = 7.0
    elif avg_speed_kmh < 28:
        MET = 8.5
    else:
        MET = 10.0
    
    return MET * weight * hours


def compute_calories_by_heart_rate(avg_hr, weight, moving_time_sec, age=40, gender='male'):
    """심박수 기반 칼로리 계산 (Keytel et al. 공식)"""
    if not avg_hr or avg_hr <= 0:
        return None
    hours = moving_time_sec / 3600
    if gender == 'male':
        cal = (-55.0969 + 0.6309 * avg_hr + 0.1988 * weight + 0.2017 * age) / 4.184 * hours
    else:
        cal = (-20.4022 + 0.4472 * avg_hr - 0.1263 * weight + 0.074 * age) / 4.184 * hours
    return max(cal, 0)
