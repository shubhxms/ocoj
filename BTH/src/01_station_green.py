"""Per-station tree / green cover around Ahmedabad Metro stations.

For each metro station (agency = metro in the scraped stops table) compute the
share of each ESA WorldCover 2021 land-cover class inside a straight-line
circle of 500 m and 1,000 m, straight from the 10 m class raster.

Output: BTH/out/station_green_cover.csv
  stop_id, name, line, lat, lon, has_transfer_links,
  tree_share_500m, green_share_500m, built_share_500m, water_share_500m,
  ... same at 1000m, green_rank (1 = greenest 500 m walkshed)

"green" = tree + shrub + grass + wetland (vegetation a pedestrian perceives).
Distances use flat-earth metre approximations at 23 deg lat; good to ~0.1 %.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

ROOT = Path(__file__).resolve().parents[2]
RASTER = ROOT / "metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif"
STOPS = ROOT / "metro-hacks-data/01_transit/secondary_scraped/stops.csv"
OUT = Path(__file__).resolve().parents[1] / "out/station_green_cover.csv"

RADII_M = (500, 1000)
GREEN_CLASSES = (10, 20, 30, 90)          # tree, shrub, grass, wetland
M_PER_DEG_LAT = 110_744.0                 # ~23 deg N

ROW_KEYS = ("stop_id", "name", "lat", "lon", "interchange_group_id")


def circle_shares(win, dx_m, dy_m, radius):
    """Class/green shares inside a radius (m) given metre offset grids."""
    mask = (dx_m * dx_m + dy_m * dy_m) <= radius * radius
    inside = win[mask]
    return {
        "n_pixels": int(inside.size),
        "tree_share": float((inside == 10).mean()),
        "green_share": float(np.isin(inside, GREEN_CLASSES).mean()),
        "built_share": float((inside == 50).mean()),
        "water_share": float((inside == 80).mean()),
    }


def main():
    metro = pd.read_csv(STOPS)
    metro = metro[metro["agency"] == "metro"].reset_index(drop=True)

    with rasterio.open(RASTER) as src:
        cls = src.read(1)
        t = src.transform
        left, top, px = t.c, t.f, t.a           # 10 m pixels, degree-anchored
        print(f"raster {cls.shape}, ~{px * 111_320:.1f} m/px, origin {left:.4f},{top:.4f}")

    rows = []
    cols = [metro[k] for k in ROW_KEYS]
    for stop_id, name, lat, lon, ixg in zip(*cols):
        x0f, y0f = (lon - left) / px, (top - lat) / px
        mx = px * 111_320.0 * np.cos(np.radians(lat))   # metres per pixel, east
        my = px * M_PER_DEG_LAT                          # metres per pixel, north
        hx = int(np.ceil(1000 / mx)) + 1        # window big enough for 1 km
        hy = int(np.ceil(1000 / my)) + 1
        x0, y0 = int(x0f), int(y0f)
        xa, xb = max(x0 - hx, 0), min(x0 + hx, cls.shape[1])
        ya, yb = max(y0 - hy, 0), min(y0 + hy, cls.shape[0])
        win = cls[ya:yb, xa:xb]

        # metre offsets of every window pixel centre from the station
        dx_m = (((np.arange(xa, xb) + 0.5) - x0f) * mx)[None, :]
        dy_m = (((np.arange(ya, yb) + 0.5) - y0f) * my)[:, None]

        row = {"stop_id": stop_id, "name": name, "line": stop_id.split("-")[1],
               "lat": lat, "lon": lon,
               # interchange_group_id links stops ACROSS agencies (metro stop
               # co-located with BRTS/AMTS stops); it never joins two metro stops
               "has_transfer_links": isinstance(ixg, str)}
        for r in RADII_M:
            sh = circle_shares(win, dx_m, dy_m, r)
            for k in ("tree", "green", "built", "water"):
                row[f"{k}_share_{r}m"] = round(sh[f"{k}_share"], 4)
        rows.append(row)
        print(f"{name:<28} green500={row['green_share_500m']:.1%} "
              f"tree500={row['tree_share_500m']:.1%}")

    out = pd.DataFrame(rows)
    out["green_rank"] = out["green_share_500m"].rank(ascending=False).astype(int)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"\nwrote {OUT}  ({len(out)} stations)")
    print("greenest 500 m walksheds:")
    print(out.nsmallest(5, "green_rank")[["green_rank", "name", "line", "green_share_500m"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
