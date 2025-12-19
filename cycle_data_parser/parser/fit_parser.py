import fitparse
import os
from datetime import timezone, timedelta

from cycle_data_parser.model.models import ActivityCore, ActivityPoint
from cycle_data_parser.analysis.metrics_utils import (
    compute_basic_metrics,
    calc_up_flat_down,
    compute_virtual_power,
    compute_headings,
    compute_total_calories,
    haversine
)

KST = timezone(timedelta(hours=9))


def safe_avg(values):
    valid = [v for v in values if v is not None]
    return sum(valid) / len(valid) if valid else None


def parse_fit(file_path):
    fitfile = fitparse.FitFile(
        file_path,
        data_processor=fitparse.StandardUnitsDataProcessor()
    )

    lat_list, lon_list, ele_list, time_list = [], [], [], []
    hr_list, cad_list = [], []

    # --------------------------------------------------
    # FIT → GPS + HR + CAD
    # --------------------------------------------------
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
        ele_list.append(values.get("altitude", 0.0))
        time_list.append(ts.astimezone(KST))

        hr_list.append(values.get("heart_rate"))
        cad_list.append(values.get("cadence"))

    if len(lat_list) < 2:
        raise ValueError("Not enough GPS points")

    # --------------------------------------------------
    # slope (PASS1: 0, PASS2에서 DEM으로 갱신)
    # --------------------------------------------------
    slopes = [0.0] * len(lat_list)

    # --------------------------------------------------
    # 기본 메트릭
    # --------------------------------------------------
    metrics = compute_basic_metrics(lat_list, lon_list, ele_list, time_list)
    moving_time = metrics["moving_time"]

    # --------------------------------------------------
    # speed 계산
    # --------------------------------------------------
    speed_list = [0.0]
    for i in range(1, len(lat_list)):
        dist = haversine(
            lat_list[i - 1], lon_list[i - 1],
            lat_list[i], lon_list[i]
        )
        dt = (time_list[i] - time_list[i - 1]).total_seconds()
        speed_list.append(dist / dt if dt > 0 else speed_list[-1])

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
    # Virtual Power (PASS1, slope=0)
    # --------------------------------------------------
    virtual_powers = compute_virtual_power(
        speed_list,
        slopes,
        dt_list,
        mass=95
    )

    avg_power = sum(virtual_powers) / len(virtual_powers)
    total_work_kj = sum(p * dt for p, dt in zip(virtual_powers, dt_list)) / 1000

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
    # --------------------------------------------------
    calories = compute_total_calories(
        total_work_kj=total_work_kj,
        avg_speed_kmh=metrics["avg_speed"],
        moving_time_sec=moving_time,
        weight=85
    )

    # --------------------------------------------------
    # ActivityCore
    # --------------------------------------------------
    filename = os.path.basename(file_path)
    activity_id = int(filename.replace(".fit", ""))

    core = ActivityCore(
        id=activity_id,
        filename=filename,

        start_time=time_list[0],
        end_time=time_list[-1],

        total_distance=metrics["total_distance"],
        moving_time=moving_time,

        avg_speed=metrics["avg_speed"],
        max_speed=metrics["max_speed"],

        avg_power=avg_power,
        calories=calories,

        avg_heart_rate=safe_avg(hr_list),
        max_heart_rate=max([x for x in hr_list if x is not None], default=None),
        avg_cadence=safe_avg(cad_list),

        uphill_distance=up,
        flat_distance=flat,
        down_distance=down,

        start_lat=lat_list[0],
        start_lon=lon_list[0],
        end_lat=lat_list[-1],
        end_lon=lon_list[-1],
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
                power=virtual_powers[i],

                heading=headings[i],
                slope=slopes[i],
            )
        )

    return core, points

