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
def compute_virtual_power(speed_list, slope_list, dt_list,
                          mass=95, Crr=0.004, CdA=0.32, rho=1.226):
    g = 9.80665
    powers = []
    prev_v = speed_list[0]

    for i in range(len(speed_list)):
        v = speed_list[i]
        slope = slope_list[i] / 100.0 if i < len(slope_list) else 0
        dt = dt_list[i]

        # 구름저항
        P_roll = Crr * mass * g * v

        # 경사저항
        P_gravity = mass * g * slope * v

        # 공기저항
        P_aero = 0.5 * rho * CdA * v * v * v

        # 가속도 파워
        dv = (v - prev_v) / dt if dt > 0 else 0
        P_acc = mass * dv * v
        prev_v = v

        total_p = max(0, P_roll + P_gravity + P_aero + P_acc)
        powers.append(total_p)

    return powers

def calc_calories_from_power(total_work_kj):
    if total_work_kj is None:
        return None
    return total_work_kj * 0.24  # Strava와 유사한 계수

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

def compute_total_calories(total_work_kj, avg_speed_kmh, moving_time_sec, weight=85):
    if total_work_kj:  # 파워 기반 존재
        return calc_calories_from_power(total_work_kj)
    return calc_calories_from_MET(avg_speed_kmh, moving_time_sec, weight)

