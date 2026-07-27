def calculate_difficulty(dist_km, ascent_m, slopes):
    """
    dist_km (float)
    ascent_m (int)
    slopes (list of float)
    """
    # 평균 경사도 절대값
    avg_slope = sum(abs(s) for s in slopes) / len(slopes) if slopes else 0

    # 난이도 공식(가변)
    difficulty = (dist_km * 0.8) + (ascent_m * 0.12) + (avg_slope * 5)

    return round(difficulty, 2)


