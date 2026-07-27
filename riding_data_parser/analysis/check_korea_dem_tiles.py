import os

CACHE_DIR = os.path.expanduser("~/.cache/elevation/SRTM1/cache")

required_tiles = []

for lat in range(33, 40):
    for lon in range(124, 133):
        required_tiles.append(f"N{lat:02d}E{lon:03d}.tif")

missing = []
for tile in required_tiles:
    if not os.path.exists(os.path.join(CACHE_DIR, tile)):
        missing.append(tile)

if not missing:
    print("🎉 모든 한국 DEM 타일이 완전하게 준비됨!")
else:
    print("❌ 누락된 타일:")
    for m in missing:
        print(" -", m)

