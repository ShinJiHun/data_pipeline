import sys
sys.path.insert(0, "/mnt/200gb/project/data_pipeline")
sys.path.insert(0, "/mnt/200gb/project/data_pipeline/riding_data_parser/main")

import os
import shutil
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from main_reader import process_single_file
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

@app.get("/health")
def health():
    return {"status": "ok"}
