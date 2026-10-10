"""Fetch ESA WorldCover 10 m land-cover for the Ahmedabad-Gandhinagar bbox via /vsicurl.

Writes:
  metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif  (10 m classes)
  metro-hacks-data/04_population_pollution/green_cover_100m_amd_gnr.csv           (lon, lat, green_fraction)

Classes (WorldCover v200): 10 tree, 20 shrub, 30 grass, 40 crop, 50 built-up,
60 bare, 70 snow/ice, 80 water, 90 wetland, 95 mangrove, 100 moss/lichen.
"green" = tree + mangrove + shrub + grass (vegetation a pedestrian perceives).
"""
import io
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.transform import xy as transform_xy

TILE_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
            "ESA_WorldCover_10m_2021_v200_N21E072_Map.tif")
BBOX = (72.40, 22.90, 72.80, 23.30)          # matches the pack's GHSL rasters
OUT_TIF = Path("metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif")
OUT_CSV = Path("metro-hacks-data/04_population_pollution/green_cover_100m_amd_gnr.csv")
GREEN_CLASSES = (10, 20, 30, 95)

with rasterio.open(TILE_URL) as src:
    win = from_bounds(*BBOX, src.transform)
    win = win.round_offsets().round_lengths()
    cls = src.read(1, window=win)
    transform = src.window_transform(win)
    profile = src.profile.copy()
    print(f"tile CRS {src.crs}, res {src.res}, window {cls.shape}")

# -- save the cropped 10 m class raster -------------------------------------
profile.update(height=cls.shape[0], width=cls.shape[1], transform=transform,
               compress="deflate", blockxsize=512, blockysize=512, tiled=True)
with rasterio.open(OUT_TIF, "w", **profile) as dst:
    dst.write(cls, 1)
print("wrote", OUT_TIF)

# -- aggregate to 100 m green-fraction grid (10 px blocks) ------------------
h10 = cls.shape[0] // 10 * 10
w10 = cls.shape[1] // 10 * 10
green = np.isin(cls[:h10, :w10], GREEN_CLASSES).astype(np.float32)
green = green.reshape(h10 // 10, 10, w10 // 10, 10).mean(axis=(1, 3))
cell_transform = transform * rasterio.Affine.scale(10, 10)

rows, cols = np.mgrid[0:green.shape[0], 0:green.shape[1]]
xs, ys = transform_xy(cell_transform, rows, cols, offset="center")
buf = io.StringIO()
buf.write("lon,lat,green_fraction\n")
for x, y, g in zip(np.asarray(xs).ravel(), np.asarray(ys).ravel(), green.ravel()):
    buf.write(f"{round(x, 6)},{round(y, 6)},{g:.2f}\n")
OUT_CSV.write_text(buf.getvalue())
print("wrote", OUT_CSV, f"({green.size} cells)")
print(f"green fraction in bbox: {green.mean():.1%} | pure-tree share: "
      f"{(cls == 10).mean():.1%} | built-up share: {(cls == 50).mean():.1%}")
