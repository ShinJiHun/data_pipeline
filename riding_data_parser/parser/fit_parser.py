import polyline
import fitparse
import os
from datetime import timezone, timedelta

from riding_data_parser.model.models import ActivityCore, ActivityPoint
from riding_data_parser.analysis.metrics_utils import (
    compute_basic_metrics,
    calc_up_flat_down,
    compute_virtual_power,
    compute_headings,
    compute_total_calories,
    haversine,
    compute_calories_by_heart_rate
)

KST = timezone(timedelta(hours=9))

MIN_DISTANCE_KM = 3  # 최소 거리 (km)
DEFAULT_USER_ID = 1  # 단일 사용자 모드

def safe_avg(values):
    valid = [v for v in values if v is not None]
    return sum(valid) / len(valid) if valid else None


def safe_max(values):
    valid = [v for v in values if v is not None]
    return max(valid) if valid else None


def parse_fit(file_path):
    fitfile = fitparse.FitFile(
        file_path,
        data_processor=fitparse.StandardUnitsDataProcessor()
    )

    # --------------------------------------------------
    # Session 메시지에서 요약 데이터 가져오기
    # --------------------------------------------------
    session_data = {}
    for record in fitfile.get_messages("session"):
        session_data = record.get_values()
        break

    lat_list, lon_list, ele_list, time_list = [], [], [], []
    hr_list, cad_list, speed_raw_list = [], [], []

    # 파워 관련 리스트 추가
    power_list       = []   # 실제 파워미터 값 (W)
    left_power_list  = []   # 좌측 파워 (W) - 양발형만
    right_power_list = []   # 우측 파워 (W) - 양발형만
    lr_balance_list  = []   # 좌우 밸런스 (좌측 %, 0~100)

    # --------------------------------------------------
    # FIT → GPS + HR + CAD + Speed + Power
    # --------------------------------------------------
    for record in fitfile.get_messages("record"):
        values = record.get_values()

        if "position_lat" not in values or "position_long" not in values:
            continue

        lat = values["position_lat"]
        lon = values["position_long"]
        ts  = values.get("timestamp")

        if lat is None or lon is None or ts is None:
            continue

        lat_list.append(lat)
        lon_list.append(lon)
        ele_list.append(values.get("altitude", 0.0))
        time_list.append(ts.astimezone(KST))

        hr_list.append(values.get("heart_rate"))
        cad_list.append(values.get("cadence"))
        speed_raw_list.append(values.get("enhanced_speed") or values.get("speed"))

        # 실제 파워미터 값
        power_list.append(values.get("power"))

        # 좌우 파워 (아씨오마 양발형 등)
        left_power_list.append(values.get("left_pedal_power"))
        right_power_list.append(values.get("right_pedal_power"))

        # 좌우 밸런스: 가민은 left_right_balance 필드로 좌측 % 제공
        # 값 형태: 50.0 = 좌50/우50, 48.0 = 좌48/우52
        lr_raw = values.get("left_right_balance")
        if lr_raw is not None:
            # 가민 FIT은 때로 비트 플래그 포함한 정수로 옴 (0x80 | value)
            # StandardUnitsDataProcessor 사용 시 보통 float으로 정규화됨
            if isinstance(lr_raw, (int, float)):
                lr_val = float(lr_raw)
                # 100 초과면 비트 마스크 처리 (0x80 = 128 제거)
                if lr_val > 100:
                    lr_val = lr_val - 128
                lr_balance_list.append(round(lr_val, 1))
            else:
                lr_balance_list.append(None)
        else:
            lr_balance_list.append(None)

    # --------------------------------------------------
    # GPS 포인트 부족 시 스킵
    # --------------------------------------------------
    if len(lat_list) < 2:
        print(f"⚠️ GPS 포인트 부족 ({len(lat_list)}개) - 스킵: {file_path}")
        return None, None

    # --------------------------------------------------
    # slope (PASS1: 0, PASS2에서 DEM으로 갱신)
    # --------------------------------------------------
    slopes = [0.0] * len(lat_list)

    # --------------------------------------------------
    # 기본 메트릭
    # --------------------------------------------------
    metrics     = compute_basic_metrics(lat_list, lon_list, ele_list, time_list)
    moving_time = metrics["moving_time"]

    # --------------------------------------------------
    # 최소 거리 미달 시 스킵
    # --------------------------------------------------
    total_distance_km = metrics["total_distance"] / 1000
    if total_distance_km < MIN_DISTANCE_KM:
        print(f"⚠️ 거리 미달 ({total_distance_km:.1f}km < {MIN_DISTANCE_KM}km) - 스킵: {file_path}")
        return None, None

    # --------------------------------------------------
    # speed: FIT 보정값 우선, 없으면 haversine 폴백
    # --------------------------------------------------
    speed_list = []
    for i, spd in enumerate(speed_raw_list):
        if spd is not None:
            speed_list.append(spd)
        else:
            if i > 0:
                dist = haversine(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])
                dt   = (time_list[i] - time_list[i-1]).total_seconds()
                speed_list.append(dist / dt if dt > 0 else speed_list[-1])
            else:
                speed_list.append(0.0)

    # --------------------------------------------------
    # Δt
    # --------------------------------------------------
    dt_list = [1] + [
        max(1, (time_list[i] - time_list[i - 1]).total_seconds())
        for i in range(1, len(time_list))
    ]

    # --------------------------------------------------
    # 누적 거리
    # --------------------------------------------------
    distance_list = [0.0]
    for i in range(1, len(lat_list)):
        d = haversine(
            lat_list[i - 1], lon_list[i - 1],
            lat_list[i], lon_list[i]
        )
        distance_list.append(distance_list[-1] + d)

    # --------------------------------------------------
    # FIT 세션 데이터
    # --------------------------------------------------
    fit_elapsed_time  = session_data.get("total_elapsed_time")
    fit_calories      = session_data.get("total_calories")
    fit_total_ascent  = session_data.get("total_ascent")
    fit_total_descent = session_data.get("total_descent")

    elapsed_time = int(fit_elapsed_time) if fit_elapsed_time else \
                   int((time_list[-1] - time_list[0]).total_seconds())

    # --------------------------------------------------
    # 파워 결정: 실제 파워미터 우선, 없으면 Virtual Power 폴백
    # --------------------------------------------------
    has_real_power = any(p is not None for p in power_list)

    if has_real_power:
        # 실제 파워미터 데이터 사용
        # None인 포인트는 인접값으로 보간 (잠깐 신호 끊김 처리)
        final_powers = _interpolate_none(power_list)
        avg_power    = safe_avg(final_powers)
        max_power    = safe_max(final_powers)
        total_work_kj = sum(
            (p or 0) * dt for p, dt in zip(final_powers, dt_list)
        ) / 1000
        power_source = "real"
        print(f"  ⚡ 실제 파워미터 데이터 사용 (avg: {avg_power:.0f}W)")
    else:
        # Virtual Power 폴백
        virtual_powers = compute_virtual_power(speed_list, slopes, dt_list, mass=95)
        final_powers   = virtual_powers
        avg_power      = sum(virtual_powers) / len(virtual_powers)
        max_power      = max(virtual_powers)
        total_work_kj  = sum(p * dt for p, dt in zip(virtual_powers, dt_list)) / 1000
        power_source   = "virtual"

    # --------------------------------------------------
    # 좌우 밸런스 요약 (실제 파워미터 있을 때만 의미있음)
    # --------------------------------------------------
    avg_lr_balance = None
    if has_real_power:
        avg_lr_balance = safe_avg(lr_balance_list)  # 좌측 평균 %

    # --------------------------------------------------
    # 업 / 평 / 다운
    # --------------------------------------------------
    up, flat, down = calc_up_flat_down(lat_list, lon_list, slopes)

    # --------------------------------------------------
    # Heading
    # --------------------------------------------------
    headings = compute_headings(lat_list, lon_list)

    # --------------------------------------------------
    # Calories
    # 실제 파워 있으면 kJ 기반으로 정확히 계산
    # --------------------------------------------------
    if fit_calories:
        calories = fit_calories
    elif has_real_power:
        # 파워미터 기반 칼로리: kJ / 효율(0.25) / 4.184 * 1000
        # 또는 단순하게: kJ * 0.239 (kcal/kJ) / 0.25 (기계효율 25%)
        calories = total_work_kj / 0.25 * 0.239
    elif metrics.get("avg_heart_rate") and metrics["avg_heart_rate"] > 0:
        calories = compute_calories_by_heart_rate(
            avg_hr=metrics["avg_heart_rate"],
            weight=85,
            moving_time_sec=moving_time,
            age=40
        )
    else:
        calories = compute_total_calories(
            total_work_kj=total_work_kj,
            avg_speed_kmh=metrics["avg_speed"],
            moving_time_sec=moving_time,
            elapsed_time_sec=elapsed_time,
            bmr_daily=1700,
            weight=85
        )

    # --------------------------------------------------
    # Polyline
    # --------------------------------------------------
    encoded_polyline = polyline.encode(
        [(lat, lon) for lat, lon in zip(lat_list, lon_list)]
    )

    # --------------------------------------------------
    # ActivityCore
    # --------------------------------------------------
    filename    = os.path.basename(file_path)
    activity_id = int(filename.replace(".fit", ""))

    valid_speeds = [s for s in speed_list if s is not None and s > 0]
    fit_max_speed = max(valid_speeds) if valid_speeds else metrics["max_speed"]
    fit_avg_speed = (metrics["total_distance"] / moving_time * 3.6) if moving_time > 0 else 0

    core = ActivityCore(
        id=activity_id,
        user_id=DEFAULT_USER_ID,
        filename=filename,

        start_time=time_list[0],
        end_time=time_list[-1],

        total_distance=metrics["total_distance"],
        moving_time=int(moving_time),
        elapsed_time=elapsed_time,

        avg_speed=fit_avg_speed,
        max_speed=fit_max_speed,

        total_ascent=fit_total_ascent,
        total_descent=fit_total_descent,

        avg_power=avg_power,
        max_power=max_power,

        calories=calories,

        avg_heart_rate=safe_avg(hr_list),
        max_heart_rate=safe_max(hr_list),
        avg_cadence=safe_avg(cad_list),

        uphill_distance=up,
        flat_distance=flat,
        down_distance=down,

        start_lat=lat_list[0],
        start_lon=lon_list[0],
        end_lat=lat_list[-1],
        end_lon=lon_list[-1],

        polyline=encoded_polyline,

        has_power=has_real_power,

        # 파워미터 관련 (DDL 추가 후 활성화)
        # normalized_power=_calc_normalized_power(final_powers, dt_list),
        # left_right_balance=avg_lr_balance,
        # power_source=power_source,
    )

    # --------------------------------------------------
    # ActivityPoint (PASS1 완성)
    # --------------------------------------------------
    points = []
    for i in range(len(lat_list)):
        points.append(
            ActivityPoint(
                activity_core_id=activity_id,
                seq=i,

                point_time=time_list[i],
                lat=lat_list[i],
                lon=lon_list[i],

                altitude=ele_list[i],
                distance=distance_list[i],

                speed=speed_list[i],
                cadence=cad_list[i],
                heart_rate=hr_list[i],
                power=final_powers[i],  # 실제 or virtual

                heading=headings[i],
                slope=slopes[i],

                # 파워미터 좌우 데이터 (DDL 추가 후 활성화)
                # left_power=left_power_list[i] if has_real_power else None,
                # right_power=right_power_list[i] if has_real_power else None,
                # left_right_balance=lr_balance_list[i] if has_real_power else None,
            )
        )

    return core, points


# ── 유틸 ─────────────────────────────────────────────────────────

def _interpolate_none(values: list) -> list:
    """
    None 값을 앞뒤 유효값으로 선형 보간
    앞에 유효값 없으면 뒤 첫 유효값으로, 끝까지 None이면 0으로
    """
    result = list(values)
    n = len(result)

    # 앞쪽 None 처리: 첫 유효값으로 채우기
    first_valid = next((v for v in result if v is not None), 0)
    for i in range(n):
        if result[i] is None:
            result[i] = first_valid
        else:
            break

    # 뒤쪽 None 처리: 마지막 유효값으로 채우기
    last_valid = first_valid
    for i in range(n):
        if result[i] is not None:
            last_valid = result[i]
        else:
            result[i] = last_valid

    return result


def _calc_normalized_power(powers: list, dt_list: list) -> float:
    """
    Normalized Power 계산
    - 30초 롤링 평균 → 4제곱 평균의 4제곱근
    """
    if not powers or all(p is None for p in powers):
        return None

    clean = [p or 0 for p in powers]
    window = 30  # 30초 롤링

    # 30초 롤링 평균
    rolling = []
    for i in range(len(clean)):
        start = max(0, i - window + 1)
        chunk = clean[start:i+1]
        rolling.append(sum(chunk) / len(chunk))

    # 4제곱 평균의 4제곱근
    np_val = (sum(r**4 for r in rolling) / len(rolling)) ** 0.25
    return round(np_val, 1)
