#!/usr/bin/env bash

# === 설정 ===
PROJECT_ROOT="/mnt/200gb/project/data_pipeline"
VENV_PATH="$PROJECT_ROOT/venv"
APP_MODULE="backend.main:app"
HOST="0.0.0.0"
PORT="8000"

# === 이동 ===
cd "$PROJECT_ROOT" || exit 1

# === venv 활성화 ===
source "$VENV_PATH/bin/activate"

# === 서버 실행 ===
exec uvicorn $APP_MODULE --host $HOST --port $PORT --reload
