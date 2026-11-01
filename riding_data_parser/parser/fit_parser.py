import bisect
import polyline
import fitparse
import os
from datetime import timezone, timedelta, datetime

from riding_data_parser.model.models import ActivityCore, ActivityPoint, GearUsage
from riding_data_parser.analysis.metrics_utils import (
    compute_basic_metrics,
    compute_virtual_power,
    compute_headings,
    compute_total_calories,
    haversine,
    compute_calories_by_heart_rate
)
from riding_data_parser.analysis.terrain_utils import compute_grades, calc_terrain_distances
from riding_data_parser.analysis.difficulty_utils import compute_difficulty

KST = timezone(timedelta(hours=9))

MIN_DISTANCE_KM = 3  # 최소 거리 (km)
DEFAULT_USER_ID = 1  # 단일 사용자 모드

MIN_GEAR_SEGMENT_SEC = 3          # 3초 미만 구간은 노이즈로 간주하고 무시
UPHILL_GRADE_THRESHOLD = 0.02     # 평균 경사 2% 이상 -> 오르막
DOWNHILL_GRADE_THRESHOLD = -0.02  # 평균 경사 -2% 이하 -> 내리막


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
        ele_list.append(_get_altitude(values))
        time_list.append(ts.replace(tzinfo=timezone.utc).astimezone(KST))

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
        return None, None, []

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
        return None, None, []

    # --------------------------------------------------
    # speed (km/h): FIT 보정값 우선, 없으면 haversine 폴백
    # --------------------------------------------------
    speed_list = []
    for i, spd in enumerate(speed_raw_list):
        if spd is not None:
            speed_list.append(spd)
        else:
            if i > 0:
                dist = haversine(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])
                dt   = (time_list[i] - time_list[i-1]).total_seconds()
                speed_list.append(dist / dt * 3.6 if dt > 0 else speed_list[-1])
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
    # slope: FIT 고도(기압계) + 100m 거리 윈도우 경사
    # --------------------------------------------------
    slopes = compute_grades(distance_list, ele_list)

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
        virtual_powers = compute_virtual_power([v / 3.6 for v in speed_list], slopes, dt_list, mass=95)
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
    up, flat, down = calc_terrain_distances(distance_list, slopes)
    difficulty = compute_difficulty(distance_list, ele_list, slopes)

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

        avg_uphill_grade=difficulty["avg_uphill_grade"],
        max_grade_1km=difficulty["max_grade_1km"],
        climb_load=difficulty["climb_load"],
        difficulty_score=difficulty["difficulty_score"],
        difficulty_level=difficulty["difficulty_level"],

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

    # --------------------------------------------------
    # GearUsage (기어 사용 구간)
    # --------------------------------------------------
    gear_events = _extract_gear_events(fitfile, KST)
    gear_usages = _build_gear_usage_segments(
        gear_events, time_list, distance_list, ele_list, final_powers, activity_id
    )

    return core, points, gear_usages


# ── 유틸 ─────────────────────────────────────────────────────────

def _get_altitude(values: dict):
    """기압계 고도. 최신 가민은 enhanced_altitude만 채우는 경우가 있어 우선 사용."""
    alt = values.get("enhanced_altitude")
    return alt if alt is not None else values.get("altitude")

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


def _extract_gear_events(fitfile, kst):
    """
    FIT 'event' 메시지에서 front_gear_change / rear_gear_change 이벤트를 추출.
    연속으로 동일한 (front_gear, rear_gear) 조합이 찍히는 중복 이벤트는 병합한다.
    (실제 변속이 없었는데도 주기적으로 상태를 재전송하는 경우가 있음)
    """
    raw_events = []
    for record in fitfile.get_messages("event"):
        v = record.get_values()
        if v.get("event") not in ("front_gear_change", "rear_gear_change"):
            continue

        ts = v.get("timestamp")
        front = v.get("front_gear")
        rear = v.get("rear_gear")

        if ts is None or front is None or rear is None:
            continue

        raw_events.append({
            "time": ts.astimezone(kst),
            "front_gear": front,
            "rear_gear": rear,
        })

    raw_events.sort(key=lambda e: e["time"])

    # 연속 중복 병합 (실제 변속이 아닌 상태 재전송 제거)
    merged = []
    for e in raw_events:
        if merged and (
            merged[-1]["front_gear"] == e["front_gear"]
            and merged[-1]["rear_gear"] == e["rear_gear"]
        ):
            continue
        merged.append(e)

    return merged


def _build_gear_usage_segments(
    gear_events, time_list, distance_list, ele_list, final_powers, activity_id
):
    """
    변속 이벤트 사이 구간들을 (front_gear, rear_gear, terrain) 조합별로 집계하여
    GearUsage 레코드로 변환한다.

    DB의 uk_gu 유니크 제약(activity_core_id, front_gear, rear_gear, terrain) 때문에
    같은 조합은 활동당 1행만 존재해야 하므로, 시간순 개별 구간이 아니라
    조합별 합산(duration_sec/distance)과 가중평균(avg_power)으로 만든다.
    """
    if not gear_events:
        return []

    n = len(time_list)
    if n == 0:
        return []

    # 각 이벤트 시각에 대응하는 포인트 인덱스 찾기
    event_indices = []
    for e in gear_events:
        idx = bisect.bisect_left(time_list, e["time"])
        idx = min(idx, n - 1)
        event_indices.append(idx)

    boundaries = event_indices[1:] + [n]

    # (front_gear, rear_gear, terrain) -> 집계용 dict
    agg = {}

    for i, e in enumerate(gear_events):
        start_idx = event_indices[i]
        end_idx = min(boundaries[i], n)

        if start_idx >= end_idx:
            continue

        seg_time = (time_list[end_idx - 1] - time_list[start_idx]).total_seconds()
        if seg_time < MIN_GEAR_SEGMENT_SEC:
            continue

        seg_dist = distance_list[end_idx - 1] - distance_list[start_idx]

        seg_powers = final_powers[start_idx:end_idx]
        valid_powers = [p for p in seg_powers if p is not None]

        ele_start = ele_list[start_idx]
        ele_end = ele_list[end_idx - 1]
        if ele_start is None or ele_end is None or seg_dist <= 0:
            grade = 0
        else:
            grade = (ele_end - ele_start) / seg_dist

        if grade >= UPHILL_GRADE_THRESHOLD:
            terrain = "UPHILL"
        elif grade <= DOWNHILL_GRADE_THRESHOLD:
            terrain = "DOWNHILL"
        else:
            terrain = "FLAT"

        front = e["front_gear"]
        rear = e["rear_gear"]

        key = (front, rear, terrain)
        entry = agg.setdefault(key, {
            "duration": 0.0,
            "distance": 0.0,
            "power_sum": 0.0,
            "power_count": 0,
        })
        entry["duration"] += seg_time
        entry["distance"] += seg_dist
        entry["power_sum"] += sum(valid_powers)
        entry["power_count"] += len(valid_powers)

    segments = []
    for (front, rear, terrain), v in agg.items():
        gear_ratio = round(front / rear, 3) if rear else None
        avg_power = round(v["power_sum"] / v["power_count"], 1) if v["power_count"] > 0 else None

        segments.append(
            GearUsage(
                activity_core_id=activity_id,
                front_gear=front,
                rear_gear=rear,
                gear_ratio=gear_ratio,
                terrain=terrain,
                duration_sec=v["duration"],
                distance=v["distance"],
                avg_power=avg_power,
            )
        )

    return segments

def parse_multiple_fits(file_path_list):
    """
    여러 개의 FIT 파일을 받아 시작 시간순으로 정렬한 뒤,
    하나의 ActivityCore, Points, GearUsages로 병합한다.
    """
    # 1. 파일별 시작 시간 추출 및 정렬
    file_metas = []
    for fp in file_path_list:
        fitfile = fitparse.FitFile(fp, data_processor=fitparse.StandardUnitsDataProcessor())
        session_data = next(fitfile.get_messages("session"), None)
        session_vals = session_data.get_values() if session_data else {}
        start_time = session_vals.get("start_time")

        if not start_time:
            for rec in fitfile.get_messages("record"):
                ts = rec.get_values().get("timestamp")
                if ts:
                    start_time = ts
                    break
        file_metas.append({"path": fp, "start_time": start_time})

    # 시작 시간 오름차순 정렬
    file_metas.sort(key=lambda x: x["start_time"] if x["start_time"] else datetime.min)
    sorted_paths = [m["path"] for m in file_metas]

    # 2. 모든 파일의 데이터 리스트 병합
    lat_list, lon_list, ele_list, time_list = [], [], [], []
    hr_list, cad_list, speed_raw_list = [], [], []
    power_list, left_power_list, right_power_list, lr_balance_list = [], [], [], []
    all_gear_events = []
    session_data_combined = {}

    for fp in sorted_paths:
        fitfile = fitparse.FitFile(fp, data_processor=fitparse.StandardUnitsDataProcessor())

        for record in fitfile.get_messages("session"):
            if not session_data_combined:
                session_data_combined = record.get_values()
            break

        for record in fitfile.get_messages("record"):
            values = record.get_values()
            if "position_lat" not in values or "position_long" not in values:
                continue

            lat = values["position_lat"]
            lon = values["position_long"]
            ts = values.get("timestamp")

            if lat is None or lon is None or ts is None:
                continue

            lat_list.append(lat)
            lon_list.append(lon)
            ele_list.append(_get_altitude(values))
            time_list.append(ts.replace(tzinfo=timezone.utc).astimezone(KST))

            hr_list.append(values.get("heart_rate"))
            cad_list.append(values.get("cadence"))
            speed_raw_list.append(values.get("enhanced_speed") or values.get("speed"))
            power_list.append(values.get("power"))
            left_power_list.append(values.get("left_pedal_power"))
            right_power_list.append(values.get("right_pedal_power"))

            lr_raw = values.get("left_right_balance")
            if lr_raw is not None and isinstance(lr_raw, (int, float)):
                lr_val = float(lr_raw)
                if lr_val > 100:
                    lr_val -= 128
                lr_balance_list.append(round(lr_val, 1))
            else:
                lr_balance_list.append(None)

        gear_events = _extract_gear_events(fitfile, KST)
        all_gear_events.extend(gear_events)

    if len(lat_list) < 2:
        print(f"⚠️ 병합된 GPS 포인트 부족 ({len(lat_list)}개)")
        return None, None, []

    # 3. 이후 메트릭 및 후처리는 기존 parse_fit과 동일하게 전체 리스트를 대상으로 수행
    metrics = compute_basic_metrics(lat_list, lon_list, ele_list, time_list)
    moving_time = metrics["moving_time"]

    total_distance_km = metrics["total_distance"] / 1000
    if total_distance_km < MIN_DISTANCE_KM:
        print(f"⚠️ 거리 미달 ({total_distance_km:.1f}km < {MIN_DISTANCE_KM}km)")
        return None, None, []

    # 속도, 누적 거리, 파워 등 후처리 (parse_fit 내부 로직과 동일하게 연결)
    speed_list = []
    for i, spd in enumerate(speed_raw_list):
        if spd is not None:
            speed_list.append(spd)
        else:
            if i > 0:
                dist = haversine(lat_list[i-1], lon_list[i-1], lat_list[i], lon_list[i])
                dt = (time_list[i] - time_list[i-1]).total_seconds()
                speed_list.append(dist / dt * 3.6 if dt > 0 else speed_list[-1])
            else:
                speed_list.append(0.0)

    dt_list = [1] + [max(1, (time_list[i] - time_list[i - 1]).total_seconds()) for i in range(1, len(time_list))]

    distance_list = [0.0]
    for i in range(1, len(lat_list)):
        d = haversine(lat_list[i - 1], lon_list[i - 1], lat_list[i], lon_list[i])
        distance_list.append(distance_list[-1] + d)

    slopes = compute_grades(distance_list, ele_list)

    fit_elapsed_time = session_data_combined.get("total_elapsed_time")
    fit_calories = session_data_combined.get("total_calories")
    fit_total_ascent = session_data_combined.get("total_ascent")
    fit_total_descent = session_data_combined.get("total_descent")

    elapsed_time = int(fit_elapsed_time) if fit_elapsed_time else int((time_list[-1] - time_list[0]).total_seconds())

    has_real_power = any(p is not None for p in power_list)
    if has_real_power:
        final_powers = _interpolate_none(power_list)
        avg_power = safe_avg(final_powers)
        max_power = safe_max(final_powers)
        total_work_kj = sum((p or 0) * dt for p, dt in zip(final_powers, dt_list)) / 1000
    else:
        final_powers = compute_virtual_power([v / 3.6 for v in speed_list], slopes, dt_list, mass=95)
        avg_power = sum(final_powers) / len(final_powers)
        max_power = max(final_powers)
        total_work_kj = sum(p * dt for p, dt in zip(final_powers, dt_list)) / 1000

    up, flat, down = calc_terrain_distances(distance_list, slopes)
    difficulty = compute_difficulty(distance_list, ele_list, slopes)
    headings = compute_headings(lat_list, lon_list)

    if fit_calories:
        calories = fit_calories
    elif has_real_power:
        calories = total_work_kj / 0.25 * 0.239
    else:
        calories = compute_total_calories(total_work_kj, metrics["avg_speed"], moving_time, elapsed_time, 1700, 85)

    encoded_polyline = polyline.encode([(lat, lon) for lat, lon in zip(lat_list, lon_list)])

    activity_id = int(time_list[0].strftime("%Y%m%d%H%M%S"))
    valid_speeds = [s for s in speed_list if s is not None and s > 0]
    fit_max_speed = max(valid_speeds) if valid_speeds else metrics["max_speed"]
    fit_avg_speed = (metrics["total_distance"] / moving_time * 3.6) if moving_time > 0 else 0

    core = ActivityCore(
        id=activity_id,
        user_id=DEFAULT_USER_ID,
        filename=os.path.basename(sorted_paths[0]),
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

        avg_uphill_grade=difficulty["avg_uphill_grade"],
        max_grade_1km=difficulty["max_grade_1km"],
        climb_load=difficulty["climb_load"],
        difficulty_score=difficulty["difficulty_score"],
        difficulty_level=difficulty["difficulty_level"],
        start_lat=lat_list[0],
        start_lon=lon_list[0],
        end_lat=lat_list[-1],
        end_lon=lon_list[-1],
        polyline=encoded_polyline,
        has_power=has_real_power,
    )

    points = [
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
            power=final_powers[i],
            heading=headings[i],
            slope=slopes[i],
        )
        for i in range(len(lat_list))
    ]

    gear_usages = _build_gear_usage_segments(
        all_gear_events, time_list, distance_list, ele_list, final_powers, activity_id
    )

    return core, points, gear_usages
