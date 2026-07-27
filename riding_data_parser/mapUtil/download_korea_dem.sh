#!/bin/bash

echo "🇰🇷 한국 전체 DEM(SRTM1) 미리 다운로드 시작"
CACHE_DIR="$HOME/.cache/elevation/SRTM1"
mkdir -p "$CACHE_DIR/spool" "$CACHE_DIR/cache"

# 한국 범위
LATS=(33 34 35 36 37 38 39)
LONS=(124 125 126 127 128 129 130 131 132)

cd "$CACHE_DIR"

for lat in "${LATS[@]}"; do
    for lon in "${LONS[@]}"; do
        TILE="N${lat}E${lon}"
        echo "⬇️ Downloading $TILE"

        # 다운로드 후 gunzip
        curl -s -o "spool/${TILE}.hgt.gz" \
            "https://s3.amazonaws.com/elevation-tiles-prod/skadi/N${lat}/N${lat}E${lon}.hgt.gz"

        if [ -f "spool/${TILE}.hgt.gz" ]; then
            gunzip -f "spool/${TILE}.hgt.gz"

            # GeoTIFF 변환
            if [ -f "spool/${TILE}.hgt" ]; then
                gdal_translate -q \
                    -co TILED=YES -co COMPRESS=DEFLATE -co ZLEVEL=9 -co PREDICTOR=2 \
                    "spool/${TILE}.hgt" "cache/${TILE}.tif" 2>/dev/null
            fi
        fi
    done
done

echo "🎉 모든 타일 다운로드 및 변환 완료!"
echo "✔️  다음 실행부터 DEM 보정은 즉시(초 단위) 수행됩니다."

