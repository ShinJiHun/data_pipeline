"""
지형(업힐/평지/다운힐) 분석

1) compute_grades     : 포인트마다 경사도(%) 계산 (거리 윈도우 방식으로 고도 노이즈 완화)
2) classify_terrain   : 경사도 → UPHILL / FLAT / DOWNHILL 라벨
3) build_terrain_summaries : 라벨별 거리 비중 / 파워 / 심박 등 집계 → ActivityTerrain

포인트 단위로 라벨을 붙이고 마지막에 라벨 기준으로 집계한다.
(구간을 먼저 자르면 경계 처리가 복잡해지므로)
"""

import bisect

from riding_data_parser.model.models import ActivityTerrain

GRADE_WINDOW_M = 100        # 경사도 계산 윈도우 (포인트 앞뒤 50m씩)
MIN_WINDOW_M = 10           # 윈도우가 이보다 짧으면(정지/시작 직후) 경사 0 처리
UPHILL_GRADE = 2.0          # % 이상 -> 오르막
DOWNHILL_GRADE = -2.0       # % 이하 -> 내리막
MOVING_SPEED_KMH = 2        # 2km/h 이상만 이동 시간으로 인정 (compute_basic_metrics와 동일, point.speed는 km/h)
MAX_DT_SEC = 10             # 기록 끊김(오토포즈 등) 구간의 dt 상한

# 고도 글리치 필터 (clean_altitude)
MIN_MOVE_M = 1.0            # 이보다 덜 움직인 구간의 고도 변화는 무시 (정지 중 기압계 재보정)
MAX_STEP_DH_M = 3.0         # 한 스텝 고도 변화가 이보다 크고
MAX_STEP_GRADE = 0.30       # 경사가 30%를 넘으면 글리치로 보고 무시
MAX_STEP_M = 200            # 한 스텝이 이보다 길면 기록 끊김 → 그 사이 고도 변화 무시 (가민 total_ascent와 동일)

TERRAINS = ("UPHILL", "FLAT", "DOWNHILL")


def compute_grades(distance_list, ele_list, window_m=GRADE_WINDOW_M):
    """
    포인트 i의 경사도 = (d_i + w/2 지점 고도 - d_i - w/2 지점 고도) / 실제 윈도우 길이 * 100

    포인트 간 경사를 바로 쓰면 고도 노이즈 때문에 들쭉날쭉하므로
    누적거리 축에서 고도를 선형보간해 일정 거리 윈도우의 평균 경사를 쓴다.
    """
    n = len(distance_list)
    if n < 2:
        return [0.0] * n

    ele = clean_altitude(distance_list, ele_list)
    total = distance_list[-1]
    half = window_m / 2

    def ele_at(d):
        j = bisect.bisect_left(distance_list, d)
        if j <= 0:
            return ele[0]
        if j >= n:
            return ele[-1]
        d0, d1 = distance_list[j - 1], distance_list[j]
        if d1 == d0:
            return ele[j]
        return ele[j - 1] + (ele[j] - ele[j - 1]) * (d - d0) / (d1 - d0)

    grades = []
    for d in distance_list:
        a = max(0.0, d - half)
        b = min(total, d + half)
        if b - a < MIN_WINDOW_M:
            grades.append(0.0)
        else:
            grades.append(round((ele_at(b) - ele_at(a)) / (b - a) * 100, 2))
    return grades


def clean_altitude(distance_list, ele_list):
    """
    기압계 고도 글리치 제거.
    - 정지 중(이동 < MIN_MOVE_M) 고도 변화: 기압계 재보정/날씨 → 실제 오르내림 아님
    - 한 스텝에 MAX_STEP_GRADE 넘는 급변: 고도 점프
    - 한 스텝 거리가 MAX_STEP_M 초과: 기록 끊김 (그 사이 오르내림은 알 수 없음)
    해당 스텝의 고도 변화만 빼고 이후 고도를 그만큼 평행이동한다 (계단 제거).
    """
    ele = _fill_none(ele_list)
    if not ele:
        return ele

    out = [ele[0]]
    offset = 0.0
    for i in range(1, len(ele)):
        dh = ele[i] - ele[i - 1]
        seg = distance_list[i] - distance_list[i - 1]
        if (seg < MIN_MOVE_M or seg > MAX_STEP_M
                or (abs(dh) > MAX_STEP_DH_M and abs(dh) > seg * MAX_STEP_GRADE)):
            offset -= dh
        out.append(ele[i] + offset)
    return out


def classify_terrain(grade):
    if grade is None:
        return "FLAT"
    if grade >= UPHILL_GRADE:
        return "UPHILL"
    if grade <= DOWNHILL_GRADE:
        return "DOWNHILL"
    return "FLAT"


def calc_terrain_distances(distance_list, grades):
    """업/평/다운 거리(m). 구간 i-1→i 는 포인트 i의 라벨로 분류."""
    acc = {t: 0.0 for t in TERRAINS}
    for i in range(1, len(distance_list)):
        acc[classify_terrain(grades[i])] += distance_list[i] - distance_list[i - 1]
    return acc["UPHILL"], acc["FLAT"], acc["DOWNHILL"]


def build_terrain_summaries(points, activity_core_id=None):
    """
    ActivityPoint 리스트(slope 채워진 상태) → 지형별 ActivityTerrain 리스트

    avg_power         : 이동 시간 가중 평균 (코스팅 0W 포함 → 다운힐은 낮게 나오는 게 정상)
    avg_power_pedaling: 파워 > 0 인 시간만 평균 (실제로 밟을 때의 파워)
    """
    if not points or len(points) < 2:
        return []

    agg = {t: {
        "distance": 0.0, "moving": 0.0,
        "power_t": 0.0, "power_sum": 0.0,
        "pedal_t": 0.0, "pedal_sum": 0.0,
        "hr_t": 0.0, "hr_sum": 0.0,
        "cad_t": 0.0, "cad_sum": 0.0,
        "gain": 0.0, "loss": 0.0,
    } for t in TERRAINS}

    total_distance = 0.0
    for prev, p in zip(points, points[1:]):
        if p.distance is None or prev.distance is None:
            continue
        seg = max(0.0, p.distance - prev.distance)
        total_distance += seg

        e = agg[classify_terrain(p.slope)]
        e["distance"] += seg

        # 고도차 대신 구간거리 × 경사 (원본 고도의 글리치/노이즈 영향 제거)
        dh = seg * (p.slope or 0) / 100
        if dh > 0:
            e["gain"] += dh
        else:
            e["loss"] -= dh

        if p.point_time is None or prev.point_time is None:
            continue
        dt = min(max(0.0, (p.point_time - prev.point_time).total_seconds()), MAX_DT_SEC)
        if dt == 0 or (p.speed or 0) < MOVING_SPEED_KMH:
            continue

        e["moving"] += dt
        if p.power is not None:
            e["power_t"] += dt
            e["power_sum"] += p.power * dt
            if p.power > 0:
                e["pedal_t"] += dt
                e["pedal_sum"] += p.power * dt
        if p.heart_rate:
            e["hr_t"] += dt
            e["hr_sum"] += p.heart_rate * dt
        if p.cadence:
            e["cad_t"] += dt
            e["cad_sum"] += p.cadence * dt

    if total_distance <= 0:
        return []

    def avg(s, t):
        return round(s / t, 1) if t > 0 else None

    summaries = []
    for terrain in TERRAINS:
        e = agg[terrain]
        if e["distance"] <= 0:
            continue
        summaries.append(ActivityTerrain(
            activity_core_id=activity_core_id,
            terrain=terrain,
            distance=round(e["distance"], 1),
            distance_ratio=round(e["distance"] / total_distance * 100, 1),
            moving_time_sec=int(e["moving"]),
            elevation_gain=round(e["gain"], 1),
            elevation_loss=round(e["loss"], 1),
            avg_grade=round((e["gain"] - e["loss"]) / e["distance"] * 100, 2),
            avg_speed=round(e["distance"] / e["moving"] * 3.6, 1) if e["moving"] > 0 else None,
            avg_power=avg(e["power_sum"], e["power_t"]),
            avg_power_pedaling=avg(e["pedal_sum"], e["pedal_t"]),
            avg_heart_rate=avg(e["hr_sum"], e["hr_t"]),
            avg_cadence=avg(e["cad_sum"], e["cad_t"]),
        ))
    return summaries


def _fill_none(values):
    """None 고도를 직전 유효값으로 채움 (앞쪽 None은 첫 유효값)."""
    first = next((v for v in values if v is not None), 0.0)
    out, last = [], first
    for v in values:
        if v is not None:
            last = v
        out.append(last)
    return out
