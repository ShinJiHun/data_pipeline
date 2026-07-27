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


def match_bike(db, core):
    gear = getattr(core, "gear_name", None)
    if not gear:
        return None
    row = db.execute(
        text("SELECT id FROM bike WHERE model = :g LIMIT 1"),
        {"g": gear},
    ).first()
    return row[0] if row else None
