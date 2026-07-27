"""
patch_speed.py
기존 DB의 ActivityCore.avg_speed, max_speed를
FIT session의 가민 보정값으로 업데이트
"""
import os
import fitparse
from riding_data_parser.model.db import SessionLocal
from riding_data_parser.model.models import ActivityCore

FIT_DIR = "/data/home/tho881/project/NAS/data/fit/"


def extract_session_speeds(file_path):
    """FIT session에서 가민 보정 max/avg speed 추출 (km/h, StandardUnitsDataProcessor 적용)"""
    fitfile = fitparse.FitFile(
        file_path,
        data_processor=fitparse.StandardUnitsDataProcessor()
    )
    for record in fitfile.get_messages("session"):
        v = record.get_values()
        return {
            "max_speed": v.get("enhanced_max_speed") or v.get("max_speed"),
            "avg_speed": v.get("enhanced_avg_speed") or v.get("avg_speed"),
        }
    return None


def patch():
    db = SessionLocal()
    try:
        cores = db.query(ActivityCore).filter(
            ActivityCore.filename.like("%.fit")
        ).all()

        print(f"📋 패치 대상: {len(cores)}건")
        updated = 0
        skipped = 0

        for core in cores:
            fit_path = os.path.join(FIT_DIR, core.filename)
            if not os.path.exists(fit_path):
                print(f"  ⚠️ FIT 없음: {core.filename}")
                skipped += 1
                continue

            result = extract_session_speeds(fit_path)
            if not result or not result["max_speed"]:
                print(f"  ⚠️ session 데이터 없음: {core.filename}")
                skipped += 1
                continue

            old_max = core.max_speed
            old_avg = core.avg_speed
            new_max = result["max_speed"]
            new_avg = result["avg_speed"]

            core.max_speed = new_max
            core.avg_speed = new_avg
            updated += 1

            if old_max and new_max and abs(old_max - new_max) > 1:
                print(f"  ✅ {core.filename}: max {old_max:.1f} → {new_max:.1f} / avg {old_avg:.1f} → {new_avg:.1f} km/h")

        db.commit()
        print(f"\n🏁 완료: {updated}건 업데이트, {skipped}건 스킵")

    except Exception as e:
        db.rollback()
        print(f"❌ 에러: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    patch()
