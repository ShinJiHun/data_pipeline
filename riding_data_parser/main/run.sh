#!/bin/bash

PROJECT_ROOT="/mnt/200gb/project/data_pipeline"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python"

export DB_URL="mysql+pymysql://tho881:ride1234!@127.0.0.1:3306/riding_db"

# .env 로드
set -a
source /mnt/200gb/apps/.env
set +a

echo "📂 이동 중: $PROJECT_ROOT"
cd "$PROJECT_ROOT" || exit 1

echo "🐍 실행 중 Python: $VENV_PYTHON"
"$VENV_PYTHON" -V

echo "🚀 main_reader 실행 중..."
"$VENV_PYTHON" -m riding_data_parser.main.main_reader

STATUS=$?
if [ $STATUS -eq 0 ]; then
  echo "✅ main_reader 실행 완료!"
else
  echo "❌ 실행 실패 (code $STATUS)"
fi
