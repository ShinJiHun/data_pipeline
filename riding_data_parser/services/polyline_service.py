def encode_polyline(points):
    """Polyline encoding (ORM 객체용)"""
    result = []
    prev_lat = 0
    prev_lon = 0

    for p in points:
        # ✅ ORM 객체 지원
        lat = int(round(p.latitude * 1e5))
        lon = int(round(p.longitude * 1e5))

        d_lat = lat - prev_lat
        d_lon = lon - prev_lon
        prev_lat, prev_lon = lat, lon

        for value in [d_lat, d_lon]:
            value = ~(value << 1) if value < 0 else (value << 1)
            while value >= 0x20:
                result.append(chr((0x20 | (value & 0x1f)) + 63))
                value >>= 5
            result.append(chr(value + 63))

    return "".join(result)

