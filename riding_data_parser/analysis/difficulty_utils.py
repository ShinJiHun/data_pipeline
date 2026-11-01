"""
라이딩 난이도 (코스 기준)

파워/심박 같은 라이더 상태가 아니라 코스 자체(거리 + 오르막 + 경사)로만 계산한다.
→ 파워미터 유무와 상관없이 라이딩끼리 비교 가능.

difficulty_score = 평지 환산 거리(km)
                 = 총 거리(km) + climb_load(m) / CLIMB_M_PER_FLAT_KM

climb_load = Σ(오르막 포인트의 상승고도 × 경사 가중치)
  - 상승고도는 포인트 고도차 대신 '구간거리 × 경사(100m 윈도우)'를 써서 노이즈를 줄인다.
  - 경사 가중치 = 1 + max(0, grade - STEEP_FROM) / STEEP_SCALE
    (5% 이하는 1배, 10% 1.5배, 15% 2배 → 같은 고도라도 가파를수록 어렵게)
"""

import bisect

from riding_data_parser.analysis.terrain_utils import UPHILL_GRADE, clean_altitude

CLIMB_M_PER_FLAT_KM = 25   # 상승 25m ≈ 평지 1km (상승 1000m ≈ 평지 40km)
STEEP_FROM = 5.0           # % 초과부터 가중
STEEP_SCALE = 10.0         # 초과 10%p 마다 +1배
SUSTAINED_WINDOW_M = 1000  # 최대 지속 경사 측정 구간

# (score 하한, level, 라벨) — 기존 라이딩 200건 분포로 보정, Lv5 ≈ 300km급 브레베
DIFFICULTY_LEVELS = [
    (0, 1, "쉬움"),
    (50, 2, "보통"),
    (100, 3, "어려움"),
    (180, 4, "매우 어려움"),
    (300, 5, "극한"),
]


def steep_weight(grade):
    return 1 + max(0.0, grade - STEEP_FROM) / STEEP_SCALE


def difficulty_level(score):
    level, label = 1, DIFFICULTY_LEVELS[0][2]
    for lower, lv, lb in DIFFICULTY_LEVELS:
        if score >= lower:
            level, label = lv, lb
    return level, label


def max_sustained_grade(distance_list, ele_list, window_m=SUSTAINED_WINDOW_M):
    """window_m 구간 평균 경사 중 최댓값(%). 라이딩이 window보다 짧으면 None."""
    n = len(distance_list)
    if n < 2 or distance_list[-1] < window_m:
        return None
    ele_list = clean_altitude(distance_list, ele_list)

    best = None
    j = 0
    for i in range(n):
        target = distance_list[i] + window_m
        if target > distance_list[-1]:
            break
        j = bisect.bisect_left(distance_list, target, lo=max(j, i))
        d0, d1 = distance_list[j - 1], distance_list[j]
        e0, e1 = ele_list[j - 1], ele_list[j]
        ele_end = e1 if d1 == d0 else e0 + (e1 - e0) * (target - d0) / (d1 - d0)
        g = (ele_end - ele_list[i]) / window_m * 100
        if best is None or g > best:
            best = g
    return round(best, 2) if best is not None else None


def compute_difficulty(distance_list, ele_list, grades):
    """
    distance_list : 누적거리(m)
    ele_list      : 고도(m)
    grades        : 포인트별 경사(%) — terrain_utils.compute_grades 결과
    """
    if len(distance_list) < 2:
        return None

    climb_m = 0.0        # 오르막 상승고도
    climb_load = 0.0     # 경사 가중 상승고도
    uphill_dist = 0.0
    for i in range(1, len(distance_list)):
        g = grades[i]
        if g is None or g < UPHILL_GRADE:
            continue
        seg = distance_list[i] - distance_list[i - 1]
        dh = seg * g / 100
        uphill_dist += seg
        climb_m += dh
        climb_load += dh * steep_weight(g)

    distance_km = distance_list[-1] / 1000
    score = distance_km + climb_load / CLIMB_M_PER_FLAT_KM
    level, label = difficulty_level(score)

    return {
        "avg_uphill_grade": round(climb_m / uphill_dist * 100, 2) if uphill_dist > 0 else None,
        "max_grade_1km": max_sustained_grade(distance_list, ele_list),
        "climb_load": round(climb_load, 1),
        "difficulty_score": round(score, 1),
        "difficulty_level": level,
        "difficulty_label": label,
    }
