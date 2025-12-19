#!/bin/bash

PROJECT_ROOT="/home/tho881/project/data_pipeline"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python"

# ============================================
# 🔥 Load .env 파일 (중요!! 이 부분 필수)
# ============================================
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

echo "📂 이동 중: $PROJECT_ROOT"
cd "$PROJECT_ROOT" || exit 1

echo "🐍 실행 중 Python: $VENV_PYTHON"
"$VENV_PYTHON" -V

echo "🚀 main_reader 실행 중..."
"$VENV_PYTHON" -m cycle_data_parser.main.main_reader

STATUS=$?
if [ $STATUS -eq 0 ]; then
  echo "✅ main_reader 실행 완료!"
else
  echo "❌ 실행 실패 (code $STATUS)"
fi

