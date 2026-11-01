from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    Date,
    ForeignKey,
    Boolean,
)
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

# =========================================================
# 👤 AppUser
# =========================================================
class AppUser(Base):
    __tablename__ = "app_user"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False)
    email = Column(String(255))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# =========================================================
# 📱 DeviceInfo
# =========================================================
class DeviceInfo(Base):
    __tablename__ = "device_info"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    manufacturer = Column(String(100))
    model = Column(String(100))
    serial_number = Column(String(100))
    firmware_version = Column(String(50))
    device_type = Column(String(50))
    user_label = Column(String(100))
    owner_user_id = Column(BigInteger, ForeignKey("app_user.id"))
    first_seen_at = Column(DateTime)
    last_seen_at = Column(DateTime)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# =========================================================
# 🚴 ActivityCore (라이딩 핵심 요약)
# =========================================================
class ActivityCore(Base):
    __tablename__ = "activity_core"

    id = Column(BigInteger, primary_key=True)
    filename = Column(String(255), nullable=False, unique=True)

    start_time = Column(DateTime)
    end_time = Column(DateTime)

    total_distance = Column(Float)
    moving_time = Column(Integer)
    elapsed_time = Column(Integer)

    total_ascent = Column(Float)
    total_descent = Column(Float)

    avg_speed = Column(Float)
    max_speed = Column(Float)

    avg_cadence = Column(Float)
    avg_heart_rate = Column(Float)
    max_heart_rate = Column(Float)

    avg_power = Column(Float)
    max_power = Column(Float)
    calories = Column(Float)

    start_lat = Column(Float)
    start_lon = Column(Float)
    end_lat = Column(Float)
    end_lon = Column(Float)

    polyline = Column(Text)

    device_id = Column(BigInteger, ForeignKey("device_info.id"))
    source = Column(String(30))
    #gear_name = Column(String(100))
    user_id = Column(BigInteger, ForeignKey("app_user.id"))
    bike_id = Column(BigInteger, ForeignKey("bike.id"))

    uphill_distance = Column(Float)
    flat_distance = Column(Float)
    down_distance = Column(Float)

    # 코스 난이도 (analysis/difficulty_utils.py)
    avg_uphill_grade = Column(Float)     # 오르막 평균 경사 %
    max_grade_1km = Column(Float)        # 최대 1km 지속 경사 %
    climb_load = Column(Float)           # 경사 가중 상승고도 m
    difficulty_score = Column(Float)     # 평지 환산 거리 km
    difficulty_level = Column(Integer)   # 1~5

    avg_power_weather_adj = Column(Float)
    total_work_weather_adj_kj = Column(Float)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    has_power = Column(Boolean, default=False)

    # 업로드 시 프론트에서 선택한 라이딩 타입/퍼머넌트 코스 (LifeMetrics 업로드 팝업에서 전달)
    ride_type = Column(String(20))
    permanent_no = Column(String(20))
    permanent_gpx_file = Column(String(255))


# =========================================================
# 📍 ActivityPoint (DB 스키마 완전 일치)
# =========================================================
class ActivityPoint(Base):
    __tablename__ = "activity_point"

    activity_core_id = Column(
        BigInteger,
        ForeignKey("activity_core.id"),
        primary_key=True
    )
    seq = Column(Integer, primary_key=True)

    point_time = Column(DateTime)

    lat = Column(Float)
    lon = Column(Float)

    altitude = Column(Float)
    distance = Column(Float)

    speed = Column(Float)
    cadence = Column(Float)
    heart_rate = Column(Float)
    power = Column(Float)
    temperature = Column(Float)

    heading = Column(Float)
    slope = Column(Float)

    altitude_dem = Column(Float)
    slope_dem = Column(Float)

    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 🌦 ActivityWeather (PASS2)
# =========================================================
class ActivityWeather(Base):
    __tablename__ = "activity_weather"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(
        BigInteger,
        ForeignKey("activity_core.id"),
        nullable=False,
        unique=True
    )

    temperature = Column(Float)
    humidity = Column(Float)
    wind_speed = Column(Float)
    wind_deg = Column(Float)
    pressure = Column(Float)
    weather_main = Column(String(50))
    weather_desc = Column(String(100))

    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 🏞 Activity (피드/UI)
# =========================================================
class Activity(Base):
    __tablename__ = "activity"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(
        BigInteger,
        ForeignKey("activity_core.id", ondelete="CASCADE")
    )

    activity_date = Column(Date)
    activity_title = Column(String(255))
    activity_content = Column(Text)
    visibility = Column(String(50))

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# =========================================================
# 🖼 ActivityImage
# =========================================================
class ActivityImage(Base):
    __tablename__ = "activity_image"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(
        BigInteger,
        ForeignKey("activity_core.id", ondelete="CASCADE")
    )
    image_path = Column(String(500))
    image_type = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 💬 ActivityComment
# =========================================================
class ActivityComment(Base):
    __tablename__ = "activity_comment"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(
        BigInteger,
        ForeignKey("activity_core.id", ondelete="CASCADE")
    )
    author = Column(String(100))
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 📅 FileDate (중복 방지)
# =========================================================
class FileDate(Base):
    __tablename__ = "file_date"

    filename = Column(String(255), primary_key=True)
    processed_at = Column(DateTime, default=datetime.now)


# =========================================================
# 🧍 UserBodyRecord
# =========================================================
class UserBodyRecord(Base):
    __tablename__ = "user_body_record"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("app_user.id"))
    record_date = Column(Date)
    filename = Column(String(255))
    ocr_text = Column(Text)

    weight = Column(Float)
    muscle_mass = Column(Float)
    body_fat_mass = Column(Float)
    body_fat_percentage = Column(Float)
    visceral_fat_level = Column(Integer)
    bmr = Column(Float)
    bmi = Column(Float)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# =========================================================
# 🏁 Segment
# =========================================================
class Segment(Base):
    __tablename__ = "segment"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(255))
    description = Column(Text)
    start_lat = Column(Float)
    start_lon = Column(Float)
    end_lat = Column(Float)
    end_lon = Column(Float)
    start_radius_m = Column(Integer)
    end_radius_m = Column(Integer)
    match_tolerance_m = Column(Integer)
    min_match_percent = Column(Float)
    distance = Column(Float)
    elevation_gain = Column(Float)
    elevation_loss = Column(Float)
    avg_grade = Column(Float)
    max_grade = Column(Float)
    direction_degrees = Column(Float)
    bbox_min_lat = Column(Float)
    bbox_max_lat = Column(Float)
    bbox_min_lon = Column(Float)
    bbox_max_lon = Column(Float)
    polyline = Column(Text)
    is_active = Column(Boolean, default=True)
    strava_segment_id = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# =========================================================
# 🏁 ActivitySegment
# =========================================================
class ActivitySegment(Base):
    __tablename__ = "activity_segment"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(BigInteger, ForeignKey("activity_core.id"), nullable=False)
    segment_id = Column(BigInteger, ForeignKey("segment.id"), nullable=False)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    elapsed_time = Column(Integer)
    avg_speed = Column(Float)
    max_speed = Column(Float)
    avg_cadence = Column(Float)
    avg_heart_rate = Column(Float)
    created_at = Column(DateTime, default=datetime.now)

# =========================================================
# 🏁 SegmentEffort
# =========================================================
class SegmentEffort(Base):
    __tablename__ = "segment_effort"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    segment_id = Column(BigInteger, ForeignKey("segment.id"))
    activity_core_id = Column(BigInteger, ForeignKey("activity_core.id"))
    user_id = Column(BigInteger)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    elapsed_time_sec = Column(Integer)
    moving_time_sec = Column(Integer)
    match_percent = Column(Float)
    start_distance_m = Column(Float)
    end_distance_m = Column(Float)
    avg_speed = Column(Float)
    max_speed = Column(Float)
    avg_power = Column(Float)
    max_power = Column(Float)
    avg_heart_rate = Column(Integer)
    max_heart_rate = Column(Integer)
    avg_cadence = Column(Integer)
    start_point_seq = Column(Integer)
    end_point_seq = Column(Integer)
    pr_rank = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 🚲 Bike
# =========================================================
class Bike(Base):
    __tablename__ = "bike"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model = Column(String(100))
    model_year = Column(Integer)
    bike_type = Column(String(30))
    ride_category = Column(String(20))
    frame_size = Column(String(20))
    frame_material = Column(String(30))
    wheel_size = Column(String(20))
    groupset = Column(String(50))
    shifting_type = Column(String(20))
    speeds = Column(Integer)
    total_distance = Column(Float, default=0)
    total_time = Column(Integer, default=0)
    photo_url = Column(String(255))
    is_retired = Column(Boolean, default=False)
    is_default = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# =========================================================
# ⚙️ GearUsage (기어 사용 구간)
# =========================================================
class GearUsage(Base):
    __tablename__ = "gear_usage"
 
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(BigInteger, ForeignKey("activity_core.id"), nullable=False)
 
    front_gear = Column(Integer, nullable=False)   # 실제 이빨 수 (예: 53)
    rear_gear = Column(Integer, nullable=False)    # 실제 이빨 수 (예: 15)
    gear_ratio = Column(Float)
 
    terrain = Column(String(10), nullable=False)   # 'uphill' / 'flat' / 'downhill'
 
    duration_sec = Column(Float, nullable=False, default=0)
    distance = Column(Float, nullable=False, default=0)
    avg_power = Column(Float)

    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# ⛰ ActivityTerrain (지형별 요약: 업힐/평지/다운힐)
# =========================================================
class ActivityTerrain(Base):
    __tablename__ = "activity_terrain"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(BigInteger, ForeignKey("activity_core.id", ondelete="CASCADE"), nullable=False)

    terrain = Column(String(10), nullable=False)   # 'UPHILL' / 'FLAT' / 'DOWNHILL'

    distance = Column(Float, nullable=False, default=0)   # m
    distance_ratio = Column(Float)                        # 전체 거리 대비 %
    moving_time_sec = Column(Integer)
    elevation_gain = Column(Float)                        # m
    elevation_loss = Column(Float)                        # m
    avg_grade = Column(Float)                             # %
    avg_speed = Column(Float)                             # km/h

    avg_power = Column(Float)            # 이동 시간 평균 (코스팅 0W 포함)
    avg_power_pedaling = Column(Float)   # 파워 > 0 구간만 평균
    avg_heart_rate = Column(Float)
    avg_cadence = Column(Float)

    created_at = Column(DateTime, default=datetime.now)


# =========================================================
# 🗺 Region (행정구역: 시/도 - 시/군/구)
# =========================================================
class Region(Base):
    __tablename__ = "region"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    level = Column(Integer, nullable=False)  # 1=시/도, 2=시/군/구
    parent_id = Column(BigInteger, ForeignKey("region.id"))
    code = Column(String(10), nullable=False, unique=True)  # 법정동코드 (시도 2자리 / 시군구 5자리)
    name = Column(String(50), nullable=False)        # "수원시"
    full_name = Column(String(100), nullable=False)  # "경기도 수원시"
    center_lat = Column(Float)
    center_lon = Column(Float)
    # boundary GEOMETRY 컬럼은 raw DDL로 생성되며 ORM에는 매핑하지 않음.
    # ST_Contains 등 공간 함수로만 다룸 (mapUtil/setup_region_tables.py, mapUtil/load_region_boundaries.py 참고)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# =========================================================
# 🗺 ActivityRegion (라이딩이 통과한 행정구역)
# =========================================================
class ActivityRegion(Base):
    __tablename__ = "activity_region"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    activity_core_id = Column(BigInteger, ForeignKey("activity_core.id", ondelete="CASCADE"), nullable=False)
    region_id = Column(BigInteger, ForeignKey("region.id"), nullable=False)
    point_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


