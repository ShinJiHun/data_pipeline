import math

def calculate_slopes(latitudes, longitudes, elevations):
    slopes = []

    for i in range(1, len(latitudes)):
        d_dist = haversine(latitudes[i - 1], longitudes[i - 1],
                           latitudes[i], longitudes[i])
        d_alt = elevations[i] - elevations[i - 1]

        if d_dist <= 0:
            slopes.append(0)
            continue

        slope_percent = (d_alt / d_dist) * 100
        slopes.append(slope_percent)

    return slopes


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

