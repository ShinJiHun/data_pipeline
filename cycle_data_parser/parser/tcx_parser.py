import os
import lxml.etree as ET
from datetime import datetime, timezone, timedelta

from cycle_data_parser.model.models import ActivityCore, ActivityPoint
from cycle_data_parser.analysis.metrics_utils import (
    compute_basic_metrics,
    compute_virtual_power,
    compute_headings,
    compute_total_calories,
    haversine
)

KST = timezone(timedelta(hours=9))


def parse_tcx(file_path):
    tree = ET.parse(file_path)
    ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}

    lat_list, lon_list, ele_list, time_list = [], [], [], []
    hr_list, cad_list = [], []

    # --------------------------------------------------
    # TrackPoint 수집
    # --------------------------------------------------
    for pt in tree.findall(".//tcx:Trackpoint", ns):
        lat = pt.find("tcx:Position/tcx:LatitudeDegrees", ns)
        lon = pt.find("tcx:Position/tcx:LongitudeDegrees", ns)
        ele = pt.find("tcx:AltitudeMeters", ns)
        tm = pt.find("tcx:Time", ns)
        hr = pt.find("tcx:HeartRateBpm/tcx:Value", ns)
        cad = pt.find("tcx:Cadence", ns)

        if lat is None or lon is None or tm is None:
            continue

        lat_list.append(float(lat.text))
        lon_list.append(float(lon.text))
        ele_list.append(float(ele.text) if ele is not None else 0.0)
        time_list.append(datetime.fromisoformat(tm.text.replace("Z", "+00:00")).astimezone(KST))

        hr_list.append(int(hr.text) if hr is not None else None)
        cad_list.append(int(cad.text) if cad is not None else None)

    if len(lat_list) < 2:
        raise ValueError("Not enough TCX points")

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
    # Virtual Power
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
    activity_id = int(filename.replace(".tcx", ""))

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

        avg_heart_rate=(
            sum(x for x in hr_list if x is not None) / len([x for x in hr_list if x is not None])
            if any(hr_list) else None
        ),
        max_heart_rate=max([x for x in hr_list if x is not None], default=None),
        avg_cadence=(
            sum(x for x in cad_list if x is not None) / len([x for x in cad_list if x is not None])
            if any(cad_list) else None
        ),

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
                cadence=cad_list[i],
                heart_rate=hr_list[i],
                power=virtual_powers[i],

                heading=headings[i],
                slope=0.0,
            )
        )

    return core, points

