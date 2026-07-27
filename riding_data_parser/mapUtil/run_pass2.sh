#!/bin/bash

PROJECT_ROOT="/mnt/200gb/project/data_pipeline"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python"

# .env 로드
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

cd "$PROJECT_ROOT" || exit 1

echo "🚀 START PASS2 (DEM 기반 업/평/다운 계산)"
"$VENV_PYTHON" -m cycle_data_parser.mapUtil.run_pass2

STATUS=$?
if [ $STATUS -eq 0 ]; then
  echo "✅ PASS2 완료"
else
  echo "❌ PASS2 실패 (code $STATUS)"
fi

