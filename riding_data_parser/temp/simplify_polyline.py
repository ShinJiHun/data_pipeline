# simplify_polyline.py
import polyline
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_URL = "mysql+pymysql://tho881:ride1234!@127.0.0.1:3306/riding_db"

engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)
db = Session()

# 모든 activity 조회
activities = db.execute(text("""
    SELECT id, polyline FROM activity_core WHERE polyline IS NOT NULL AND polyline != ''
""")).fetchall()

print(f"📍 polyline 간소화 대상: {len(activities)}개")

for activity_id, encoded in activities:
    try:
        # 디코딩
        coords = polyline.decode(encoded)
        original_len = len(coords)
        
        # 이미 500개 이하면 스킵
        if original_len <= 500:
            print(f"⏭️ 스킵: {activity_id} ({original_len} points)")
            continue
        
        # 간소화 (최대 500개)
        step = original_len // 500
        simplified = coords[::step]
        
        # 재인코딩
        new_encoded = polyline.encode(simplified)
        
        db.execute(text("""
            UPDATE activity_core SET polyline = :polyline WHERE id = :id
        """), {"polyline": new_encoded, "id": activity_id})
        
        print(f"✅ 간소화: {activity_id} ({original_len} → {len(simplified)} points, {len(encoded)} → {len(new_encoded)} chars)")
        
    except Exception as e:
        print(f"❌ 오류: {activity_id} - {e}")

db.commit()
db.close()
print("🎉 완료!")
