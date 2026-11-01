import sys
sys.path.insert(0, "/mnt/200gb/project/data_pipeline")
sys.path.insert(0, "/mnt/200gb/project/data_pipeline/riding_data_parser/main")

import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main_reader import process_single_file, process_multiple_files
from riding_data_parser.services.segment_match_service import match_segments

app = FastAPI()

UPLOAD_DIR = os.getenv("DATA_BASE_DIR", "/mnt/200gb/NAS/data/") + "fit/"

def get_db():
    db_url = os.getenv("PYTHON_DB_URL")
    engine = create_engine(db_url, pool_pre_ping=True, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    return SessionLocal()

@app.post("/parse")
async def parse_fit_file(file: UploadFile = File(...), user_id: int = 1):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        activity_core_id = process_single_file(file_path, user_id)

        if activity_core_id:
            db = get_db()
            try:
                match_segments(db, activity_core_id, user_id)
            finally:
                db.close()

        return JSONResponse({
            "status": "success" if activity_core_id else "fail",
            "filename": file.filename
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from typing import List, Optional  # 상단에 추가

@app.post("/parse")
async def parse_fit_files(
    files: List[UploadFile] = File(...),
    user_id: int = 1,
    # LifeMetrics 업로드 팝업에서 선택한 라이딩 타입/퍼머넌트 코스
    # (multipart 폼 필드라서 File과 같이 쓰려면 Form(...)으로 명시해야 함)
    rideType: Optional[str] = Form(None),
    permanentNo: Optional[str] = Form(None),
    permanentGpxFile: Optional[str] = Form(None),
):
    """
    여러 개의 FIT 파일을 받아 서버에 저장한 뒤,
    하나의 활동(Activity)으로 병합하여 처리합니다.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_file_paths = []
    filenames = []

    try:
        # 1. 업로드된 모든 파일을 지정된 디렉토리에 저장
        for file in files:
            file_path = os.path.join(UPLOAD_DIR, file.filename)
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            saved_file_paths.append(file_path)
            filenames.append(file.filename)

        # 2. 다중 파일 처리 함수 호출 (예: process_multiple_files 구현 필요)
        # 단일 파일 처리 함수 대신 여러 경로를 받는 함수를 호출해야 합니다.
        activity_core_id = process_multiple_files(
            saved_file_paths, user_id,
            ride_type=rideType, permanent_no=permanentNo, permanent_gpx_file=permanentGpxFile,
        )

        # 3. 세그먼트 매칭 수행
        if activity_core_id:
            db = get_db()
            try:
                match_segments(db, activity_core_id, user_id)
            finally:
                db.close()

        return JSONResponse({
            "status": "success" if activity_core_id else "fail",
            "filenames": filenames,
            "activity_core_id": activity_core_id
        })

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
