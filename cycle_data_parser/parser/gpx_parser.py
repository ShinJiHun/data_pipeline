import os
import math
from datetime import timezone, timedelta

import gpxpy

from cycle_data_parser.model.models import ActivityCore, ActivityPoint
from cycle_data_parser.analysis.metrics_utils import (
    compute_basic_metrics,
    compute_virtual_power,
    compute_headings,
    compute_total_calories,
    haversine,
)

KST = timezone(timedelta(hours=9))


def parse_gpx(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    lat_list, lon_list, ele_list, time_list = [], [], [], []

    # --------------------------------------------------
    # TrackPoint 수집
    # --------------------------------------------------
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                if p.time is None:
                    continue
                lat_list.append(p.latitude)
                lon_list.append(p.longitude)
                ele_list.append(p.elevation or 0.0)
                time_list.append(p.time.astimezone(KST))

    if len(lat_list) < 2:
        raise ValueError("Not enough GPX points")

    # --------------------------------------------------
    # PASS1 slope = 0
    # --------------------------------------------------
    slopes = [0.0] * len(lat_list)

    # --------------------------------------------------
    # 기본 메트릭
    # --------------------------------------------------
    metrics = compute_basic_metrics(lat_list, lon_list, ele_list, time_list)
    moving_time = metrics["moving_time"]

    # --------------------------------------------------
    # speed
    # --------------------------------------------------
    speed_list = [0.0]
    for i in range(1, len(lat_list)):
        d = haversine(lat_list[i - 1], lon_list[i - 1], lat_list[i], lon_list[i])
        dt = (time_list[i] - time_list[i - 1]).total_seconds()
        speed_list.append(d / dt if dt > 0 else speed_list[-1])

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
        distance_list.append(distance_list[-1] + haversine(
            lat_list[i - 1], lon_list[i - 1],
            lat_list[i], lon_list[i]
        ))

    # --------------------------------------------------
    # Virtual Power (slope=0)
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
    activity_id = int(filename.replace(".gpx", ""))

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

        start_lat=lat_list[0],
        start_lon=lon_list[0],
        end_lat=lat_list[-1],
        end_lon=lon_list[-1],
    )

    # --------------------------------------------------
    # ActivityPoint
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
                cadence=None,
                heart_rate=None,
                power=virtual_powers[i],

                heading=headings[i],
                slope=0.0,
            )
        )

    return core, points

