# update_polyline.py 수정
import polyline
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_URL = "mysql+pymysql://tho881:ride1234!@127.0.0.1:3306/riding_db"

engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
db = Session()

# polyline 없는 activity 조회
activities = db.execute(text("""
    SELECT id FROM activity_core WHERE polyline IS NULL OR polyline = ''
""")).fetchall()

print(f"📍 polyline 업데이트 대상: {len(activities)}개")

for (activity_id,) in activities:
    points = db.execute(text("""
        SELECT lat, lon FROM activity_point 
        WHERE activity_core_id = :id 
        ORDER BY seq ASC
    """), {"id": activity_id}).fetchall()
    
    if len(points) < 2:
        print(f"⚠️ 포인트 부족 - 스킵: {activity_id}")
        continue
    
    coords = [(lat, lon) for lat, lon in points]
    
    # 포인트가 많으면 간소화 (최대 500개)
    if len(coords) > 500:
        step = len(coords) // 500
        coords = coords[::step]
        print(f"📉 간소화: {len(points)} → {len(coords)} points")
    
    encoded = polyline.encode(coords)
    
    db.execute(text("""
        UPDATE activity_core SET polyline = :polyline WHERE id = :id
    """), {"polyline": encoded, "id": activity_id})
    
    print(f"✅ 업데이트: {activity_id} ({len(coords)} points, {len(encoded)} chars)")

db.commit()
db.close()
print("🎉 완료!")
