#!/bin/bash

CACHE_DIR="$HOME/.cache/elevation/SRTM1/cache"
OUTPUT_VRT="$CACHE_DIR/korea_dem.vrt"

echo "🔎 Checking DEM tiles in $CACHE_DIR"
cd "$CACHE_DIR" || exit 1

# 모든 Korea 영역 tif 파일을 VRT로 묶기
echo "🧱 Creating korea_dem.vrt ..."
gdalbuildvrt -q "$OUTPUT_VRT" ./*.tif

echo "🎉 완료! 생성됨 → $OUTPUT_VRT"

