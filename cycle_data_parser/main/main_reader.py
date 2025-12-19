import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cycle_data_parser.parser.fit_parser import parse_fit
from cycle_data_parser.parser.gpx_parser import parse_gpx
from cycle_data_parser.parser.tcx_parser import parse_tcx
from cycle_data_parser.services.activity_save_service import ActivitySaveService


# -------------------------------------------------
# 공통 처리 함수
# -------------------------------------------------
def process_file(db, file_path, parser_func, saver):
    """
    PASS1 전용:
    - Core + Point만 저장
    - DEM / slope / power ❌
    """
    try:
        core, points = parser_func(file_path)
        saver.save_with_points(core, points)
        print(f"✅ 저장 완료: {os.path.basename(file_path)}")

    except Exception as e:
        print(f"❌ 처리 실패: {os.path.basename(file_path)} → {e}")
        raise


# -------------------------------------------------
# main
# -------------------------------------------------
def main():
    print("🚀 START main_reader")

    # ---------------------------------------------
    # DB 연결 (환경변수 사용)
    # ---------------------------------------------
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("❌ DB_URL 환경변수가 설정되지 않았습니다.")

    print(f"🔍 DB_URL 확인: {db_url}")

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        future=True
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()

    saver = ActivitySaveService(db)

    # ---------------------------------------------
    # 입력 디렉토리
    # ---------------------------------------------
    base_dir = os.getenv("DATA_BASE_DIR", "/mnt/hdd/NAS/data/test")

    fit_dir = os.path.join(base_dir, "fit")
    gpx_dir = os.path.join(base_dir, "gpx")
    tcx_dir = os.path.join(base_dir, "tcx")

    print(f"📂 base_dir: {base_dir}")
    print(f"📂 fit_dir: {fit_dir}")
    print(f"📂 gpx_dir: {gpx_dir}")
    print(f"📂 tcx_dir: {tcx_dir}")

    # ---------------------------------------------
    # FIT
    # ---------------------------------------------
    if os.path.exists(fit_dir):
        for fn in sorted(os.listdir(fit_dir)):
            if fn.lower().endswith(".fit"):
                file_path = os.path.join(fit_dir, fn)
                print(f"📄 Processing: {file_path}")
                process_file(db, file_path, parse_fit, saver)

    # ---------------------------------------------
    # GPX
    # ---------------------------------------------
    if os.path.exists(gpx_dir):
        for fn in sorted(os.listdir(gpx_dir)):
            if fn.lower().endswith(".gpx"):
                file_path = os.path.join(gpx_dir, fn)
                print(f"📄 Processing: {file_path}")
                process_file(db, file_path, parse_gpx, saver)

    # ---------------------------------------------
    # TCX
    # ---------------------------------------------
    if os.path.exists(tcx_dir):
        for fn in sorted(os.listdir(tcx_dir)):
            if fn.lower().endswith(".tcx"):
                file_path = os.path.join(tcx_dir, fn)
                print(f"📄 Processing: {file_path}")
                process_file(db, file_path, parse_tcx, saver)

    db.close()
    print("🎉 main_reader 완료")


if __name__ == "__main__":
    main()

