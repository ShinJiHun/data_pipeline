import os
import shutil
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from riding_data_parser.parser.fit_parser import parse_fit, parse_multiple_fits
from riding_data_parser.parser.gpx_parser import parse_gpx
from riding_data_parser.parser.tcx_parser import parse_tcx
from riding_data_parser.services.activity_save_service import ActivitySaveService
from dotenv import load_dotenv

load_dotenv("/mnt/200gb/project/data_pipeline/.env")

# -------------------------------------------------
# 공통 처리 함수
# -------------------------------------------------
def process_file(db, file_path, parser_func, saver):
    """
    파일을 파싱하고 DB에 저장한다.
    성공 시 True, 실패 시 False를 반환한다.
    """
    try:
        core, points, gear_usages = parser_func(file_path)

        # 스킵된 파일 (indoor 등) → 성공으로 처리
        if core is None:
            print(f"  ⏭️ 스킵 (indoor 등): {os.path.basename(file_path)}")
            return True

        saver.save_with_points(core, points, gear_usages)
        return True

    except Exception as e:
        print(f"  ❌ 처리 실패: {os.path.basename(file_path)} → {e}")
        return False


def move_file(file_path, dest_dir):
    """파일을 dest_dir로 이동"""
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, os.path.basename(file_path))
    shutil.move(file_path, dest_path)
    return dest_path


# -------------------------------------------------
# main
# -------------------------------------------------
def main():
    print("🚀 START main_reader")

    # ---------------------------------------------
    # DB 연결 (환경변수 사용)
    # ---------------------------------------------
    db_url = os.getenv("PYTHON_DB_URL")

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
    base_dir = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/")

    # 파일 유형별 설정: (소스폴더, 확장자, 파서, 성공폴더, 실패폴더)
    file_types = [
        ("fit", ".fit", parse_fit),
        ("gpx", ".gpx", parse_gpx),
        ("tcx", ".tcx", parse_tcx),
    ]

    print(f"📂 base_dir: {base_dir}")

    total = 0
    success = 0
    fail = 0
    skip = 0

    for folder_name, ext, parser_func in file_types:
        src_dir = os.path.join(base_dir, folder_name)
        suc_dir = os.path.join(base_dir, f"{folder_name}_suc")
        fail_dir = os.path.join(base_dir, f"{folder_name}_fail")

        if not os.path.exists(src_dir):
            continue

        files = sorted([f for f in os.listdir(src_dir) if f.lower().endswith(ext)])

        if not files:
            print(f"📂 {folder_name}/ → 처리할 파일 없음")
            continue

        print(f"\n📂 {folder_name}/ → {len(files)}개 파일 발견")

        for fn in files:
            file_path = os.path.join(src_dir, fn)
            total += 1
            print(f"  📄 [{total}] {fn}")

            result = process_file(db, file_path, parser_func, saver)

            if result:
                dest = move_file(file_path, suc_dir)
                print(f"  ✅ → {os.path.basename(suc_dir)}/")
                success += 1
            else:
                dest = move_file(file_path, fail_dir)
                print(f"  ❌ → {os.path.basename(fail_dir)}/")
                fail += 1

    db.close()

    print(f"\n{'='*40}")
    print(f"🎉 main_reader 완료")
    print(f"   전체: {total} | 성공: {success} | 실패: {fail}")
    print(f"{'='*40}")

def process_single_file(file_path: str, user_id: int = 1, ride_type: str = None, permanent_no: str = None, permanent_gpx_file: str = None):
    db_url = os.getenv("PYTHON_DB_URL")
    if not db_url:
        raise RuntimeError("DB_URL 환경변수가 설정되지 않았습니다.")

    engine = create_engine(db_url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()
    saver = ActivitySaveService(db)

    ext = os.path.splitext(file_path)[1].lower()
    parser_map = {
        ".fit": parse_fit,
        ".gpx": parse_gpx,
        ".tcx": parse_tcx,
    }

    parser_func = parser_map.get(ext)
    if not parser_func:
        db.close()
        raise ValueError(f"지원하지 않는 파일 형식: {ext}")

    try:
        core, points, gear_usages = parser_func(file_path)
        if core is None:
            return None  # 스킵
        core.user_id = user_id
        core.ride_type = ride_type
        core.permanent_no = permanent_no
        core.permanent_gpx_file = permanent_gpx_file
        saver.save_with_points(core, points, gear_usages)
        base_dir = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/fit/")
        folder_name = ext.lstrip(".")
        dest_dir = os.path.join(base_dir, f"{folder_name}_suc")
        move_file(file_path, dest_dir)
        return core.id  # ← activity_core_id 반환
    except Exception as e:
        base_dir = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/")
        folder_name = ext.lstrip(".")
        dest_dir = os.path.join(base_dir, f"{folder_name}_fail")
        move_file(file_path, dest_dir)
        raise e
    finally:
        db.close()

def process_multiple_files(file_path_list: list, user_id: int = 1, ride_type: str = None, permanent_no: str = None, permanent_gpx_file: str = None):
    """
    여러 개의 파일 경로를 받아 병합 처리한 후 DB에 저장하고, 
    성공 시 파일을 _suc 폴더로 이동한 뒤 activity_core_id를 반환한다.
    """
    db_url = os.getenv("PYTHON_DB_URL")
    if not db_url:
        raise RuntimeError("DB_URL 환경변수가 설정되지 않았습니다.")

    engine = create_engine(db_url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()
    saver = ActivitySaveService(db)

    try:
        # 현재는 FIT 파일 다중 병합을 지원
        core, points, gear_usages = parse_multiple_fits(file_path_list)
        if core is None:
            return None  # 스킵된 경우

        core.user_id = user_id
        core.ride_type = ride_type
        core.permanent_no = permanent_no
        core.permanent_gpx_file = permanent_gpx_file
        saver.save_with_points(core, points, gear_usages)

        # 성공한 모든 파일을 fit_suc 폴더로 이동
        base_dir = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/")
        suc_dir = os.path.join(base_dir, "fit_suc")
        for fp in file_path_list:
            move_file(fp, suc_dir)

        return core.id
    except Exception as e:
        # 실패 시 모든 파일을 fit_fail 폴더로 이동
        base_dir = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/")
        fail_dir = os.path.join(base_dir, "fit_fail")
        for fp in file_path_list:
            move_file(fp, fail_dir)
        raise e
    finally:
        db.close()

if __name__ == "__main__":
    main()
