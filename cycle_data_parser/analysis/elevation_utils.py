import os
import rasterio
import numpy as np

# 한국 DEM 전체 VRT 경로
VRT_PATH = os.path.expanduser("~/.cache/elevation/SRTM1/cache/korea_dem.vrt")

def dem_correct_elevation(lat_list, lon_list):
    if not os.path.exists(VRT_PATH):
        raise FileNotFoundError(f"❌ DEM VRT 파일 없음: {VRT_PATH}")

    corrected = []
    with rasterio.open(VRT_PATH) as dem:
        for lat, lon in zip(lat_list, lon_list):
            row, col = dem.index(lon, lat)
            try:
                corrected.append(float(dem.read(1)[row, col]))
            except:
                corrected.append(None)

    return corrected

