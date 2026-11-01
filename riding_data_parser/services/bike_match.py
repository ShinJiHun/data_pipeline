from sqlalchemy import text


def recompute_bike_totals(db, bike_id):
    if bike_id is None:
        return

    row = db.execute(
        text(
            "SELECT COALESCE(SUM(total_distance), 0), "
            "COALESCE(SUM(moving_time), 0) "
            "FROM activity_core WHERE bike_id = :bike_id"
        ),
        {"bike_id": bike_id},
    ).first()

    dist = float(row[0]) if row and row[0] is not None else 0.0
    tm = int(row[1]) if row and row[1] is not None else 0

    db.execute(
        text(
            "UPDATE bike SET total_distance = :dist, "
            "total_time = :tm WHERE id = :bike_id"
        ),
        {"dist": dist, "tm": tm, "bike_id": bike_id},
    )
    db.commit()


def match_bike(db, core, gear_usages=None):
    """
    라이딩(core)을 자전거와 자동매칭한다.

    - 전자변속 데이터(gear_usages)가 있으면: 관측된 체인링 조합이
      bike.chainring_outer/chainring_inner와 일치하는 자전거를 찾는다.
    - 전자변속 데이터가 없으면(기계식 등): purchase_date만으로 후보를 좁히고,
      후보가 정확히 1대일 때만 매칭한다.
    - 후보가 0개 또는 2개 이상이면 매칭하지 않고 None을 반환한다
      (틀린 자전거로 억지 매칭하는 것보다 안전).
    """
    gear_usages = gear_usages or []
    ride_date = getattr(core, "start_time", None)

    if ride_date is None:
        return None

    # purchase_date ~ retired_date 사이(그 자전거를 실제로 타고 있었던 기간)에 속하는 자전거만 후보로.
    # retired_date가 없으면 아직 은퇴 안 한 것. (예: 같은 체인링 스펙의 은퇴한 자전거와 헷갈리는 것 방지)
    candidates = db.execute(
        text(
            "SELECT id, chainring_outer, chainring_inner, purchase_date, name "
            "FROM bike "
            "WHERE (purchase_date IS NULL OR purchase_date <= :ride_date) "
            "AND (retired_date IS NULL OR retired_date >= :ride_date)"
        ),
        {"ride_date": ride_date},
    ).fetchall()

    if not candidates:
        return None

    # --------------------------------------------------
    # Case 1: 전자변속 데이터 있음 → 체인링 조합으로 매칭
    # --------------------------------------------------
    if gear_usages:
        usage_by_front = {}
        for g in gear_usages:
            usage_by_front[g.front_gear] = usage_by_front.get(g.front_gear, 0) + (g.duration_sec or 0)

        # 사용 시간 기준 상위 1~2개 체인링 값만 추출 (노이즈 제거)
        ranked = sorted(usage_by_front.items(), key=lambda x: -x[1])
        observed = {g for g, _ in ranked[:2] if g}

        if not observed:
            return None

        matches = []
        for c in candidates:
            spec = {c.chainring_outer, c.chainring_inner} - {None}
            if spec and observed.issubset(spec):
                matches.append(c)

        if len(matches) == 1:
            return matches[0].id

        # 0개 또는 여러 개 매칭 → 애매하므로 자동매칭 포기
        if len(matches) == 0:
            print(f"  ⚠️ 자전거 자동매칭 실패: 체인링 {observed} 에 맞는 자전거 없음 (filename={getattr(core, 'filename', '?')})")
        else:
            names = [m.name for m in matches]
            print(f"  ⚠️ 자전거 자동매칭 모호함: {names} 모두 체인링 {observed} 후보 (filename={getattr(core, 'filename', '?')})")
        return None

    # --------------------------------------------------
    # Case 2: 전자변속 데이터 없음(기계식 등) → 날짜만으로 후보 좁히기
    # --------------------------------------------------
    if len(candidates) == 1:
        return candidates[0].id

    print(f"  ⚠️ 자전거 자동매칭 보류: 기어 데이터 없음 + 날짜 후보 {len(candidates)}대 (filename={getattr(core, 'filename', '?')})")
    return None
