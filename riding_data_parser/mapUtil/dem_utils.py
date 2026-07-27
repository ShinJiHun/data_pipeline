import rasterio
import numpy as np
import math

# ✅ 실제 존재하는 DEM 경로 (네가 찾은 경로 기준)
DEM_PATH = "/mnt/200gb/project/data_pipeline/dem/korea_dem.vrt"


class DEMReader:
    """
    DEM을 한 번만 메모리에 로드해서
    초고속 고도 조회를 하기 위한 클래스
    """

    def __init__(self):
        self.ds = rasterio.open(DEM_PATH)
        self.band = self.ds.read(1)   # ✅ 단 한 번만 read
        self.transform = self.ds.transform

    def get_elevation(self, lat, lon):
        try:
            row, col = self.ds.index(lon, lat)
            val = self.band[row, col]
            if np.isnan(val):
                return None
            return float(val)
        except Exception:
            return None

    def close(self):
        self.ds.close()


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0  # meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

