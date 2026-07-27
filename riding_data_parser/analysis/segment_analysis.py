import pymysql
import pandas as pd
from geopy.distance import distance
import polyline
import folium
from shapely.geometry import LineString, Point

# =========================================
# DB 연결
# =========================================
conn = pymysql.connect(
    host='127.0.0.1',
    user='tho881',
    password='ride1234!',
    database='riding_db',
    charset='utf8mb4'
)

# =========================================
# 구룡령 세그먼트 정보
# =========================================
activity_id = 131
start_lat, start_lon = 37.906567, 128.507032
end_lat, end_lon = 37.880689, 128.514601
buffer_m = 40  # 시작/끝 버퍼 (GPS 오차 감안)

# =========================================
# 1️⃣ 활동 전체 포인트 가져오기
# =========================================
query = f"""
SELECT latitude, longitude, altitude, timestamp
FROM activity_point
WHERE activity_core_id = {activity_id}
ORDER BY timestamp
"""
df = pd.read_sql(query, conn)

# ✅ timestamp 변환
if "timestamp" in df.columns:
    # 문자열 또는 숫자인 경우 모두 datetime으로 변환 시도
    if df["timestamp"].dtype == "float64":
        # ✅ float이면 UNIX timestamp로 간주
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    else:
        # ✅ 문자열이면 ISO 형태로 변환
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

# =========================================
# 2️⃣ Haversine 거리 계산 함수
# =========================================
def haversine(lat1, lon1, lat2, lon2):
    return distance((lat1, lon1), (lat2, lon2)).m  # meter 단위

# =========================================
# 3️⃣ 세그먼트 시작/끝 인덱스 탐색
# =========================================
df["dist_to_start"] = df.apply(lambda r: haversine(r.latitude, r.longitude, start_lat, start_lon), axis=1)
df["dist_to_end"] = df.apply(lambda r: haversine(r.latitude, r.longitude, end_lat, end_lon), axis=1)

start_idx = df["dist_to_start"].idxmin()
end_idx = df["dist_to_end"].idxmin()

print(f"📍 start_idx={start_idx}, end_idx={end_idx}")
print(f"  start 거리={df.loc[start_idx,'dist_to_start']:.1f}m, end 거리={df.loc[end_idx,'dist_to_end']:.1f}m")

# =========================================
# 4️⃣ 세그먼트 구간 슬라이싱
# =========================================
segment_df = df.loc[start_idx:end_idx].copy().reset_index(drop=True)

# =========================================
# 5️⃣ 통계 계산
# =========================================
total_distance = sum(
    haversine(segment_df.loc[i, "latitude"], segment_df.loc[i, "longitude"],
              segment_df.loc[i + 1, "latitude"], segment_df.loc[i + 1, "longitude"])
    for i in range(len(segment_df) - 1)
)

start_time = segment_df["timestamp"].min()
end_time = segment_df["timestamp"].max()
elapsed_s = (end_time - start_time).total_seconds()
avg_speed = total_distance / elapsed_s * 3.6 if elapsed_s > 0 else 0  # km/h

ascent = 0
for i in range(1, len(segment_df)):
    diff = segment_df.loc[i, "altitude"] - segment_df.loc[i - 1, "altitude"]
    if diff > 0:
        ascent += diff

print(f"🚴 구룡령 세그먼트 통계")
print(f"- 이동 거리: {total_distance/1000:.2f} km")
print(f"- 고도 상승: {ascent:.1f} m")
print(f"- 소요 시간: {elapsed_s/60:.1f} 분")
print(f"- 평균 속도: {avg_speed:.2f} km/h")

# =========================================
# 6️⃣ Polyline 및 지도 시각화
# =========================================
segment_points = list(zip(segment_df.latitude, segment_df.longitude))
segment_polyline = polyline.encode(segment_points)
print(f"Polyline (encoded): {segment_polyline[:80]}...")

mid_point = segment_points[len(segment_points)//2]
m = folium.Map(location=mid_point, zoom_start=13)
folium.PolyLine(segment_points, color="red", weight=4, tooltip="구룡령 세그먼트").add_to(m)
folium.Marker(segment_points[0], popup="시작점").add_to(m)
folium.Marker(segment_points[-1], popup="종료점").add_to(m)
m.save("kuryong_segment.html")
print("✅ 지도 저장 완료 → kuryong_segment.html")

