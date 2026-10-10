"""Per-stop tree / green cover around Ahmedabad Metro, BRTS and AMTS stops.

For every stop in the scraped table (agencies metro, brt, municipal_bus)
compute the share of each ESA WorldCover 2021 land-cover class inside a
straight-line circle of 500 m and 1,000 m, straight from the 10 m class raster.

Output: BTH/out/station_green_cover.csv
  stop_id, name, mode (metro|brts|amts), line (metro only), lat, lon,
  has_transfer_links, tree/green/built/water_share_{500m,1000m},
  green_rank (1 = greenest, within mode)

"green" = tree + shrub + grass + wetland (vegetation a pedestrian perceives).
Distances use flat-earth metre approximations at 23 deg lat; good to ~0.1 %.
Stops outside the raster window (72.40-72.80 E, 22.90-23.30 N) are skipped.
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
MODE_OF = {"metro": "metro", "brt": "brts", "municipal_bus": "amts"}
STAT_KEYS = ("tree", "green", "built", "water")


def circle_shares(win, dx_m, dy_m, radius):
    """Class/green shares inside a radius (m) given metre offset grids."""
    mask = (dx_m * dx_m + dy_m * dy_m) <= radius * radius
    inside = win[mask]
    return {
        "tree_share": float((inside == 10).mean()),
        "green_share": float(np.isin(inside, GREEN_CLASSES).mean()),
        "built_share": float((inside == 50).mean()),
        "water_share": float((inside == 80).mean()),
    }


def main():
    stops = pd.read_csv(STOPS)
    stops = stops[stops["agency"].isin(MODE_OF)].reset_index(drop=True)

    with rasterio.open(RASTER) as src:
        cls = src.read(1)
        t = src.transform
        left, top, px = t.c, t.f, t.a           # square pixels, degree-anchored
        print(f"raster {cls.shape}, ~{px * 111_320:.1f} m/px, origin {left:.4f},{top:.4f}")

    rows, skipped = [], 0
    cols = [stops[k] for k in ("stop_id", "name", "agency", "lat", "lon",
                               "interchange_group_id")]
    for stop_id, name, agency, lat, lon, ixg in zip(*cols):
        x0f, y0f = (lon - left) / px, (top - lat) / px
        if not (0 <= x0f < cls.shape[1] and 0 <= y0f < cls.shape[0]):
            skipped += 1
            continue
        mx = px * 111_320.0 * np.cos(np.radians(lat))   # metres per pixel, east
        my = px * M_PER_DEG_LAT                          # metres per pixel, north
        hx = int(np.ceil(1000 / mx)) + 1        # window big enough for 1 km
        hy = int(np.ceil(1000 / my)) + 1
        x0, y0 = int(x0f), int(y0f)
        xa, xb = max(x0 - hx, 0), min(x0 + hx, cls.shape[1])
        ya, yb = max(y0 - hy, 0), min(y0 + hy, cls.shape[0])
        win = cls[ya:yb, xa:xb]

        # metre offsets of every window pixel centre from the stop
        dx_m = (((np.arange(xa, xb) + 0.5) - x0f) * mx)[None, :]
        dy_m = (((np.arange(ya, yb) + 0.5) - y0f) * my)[:, None]

        row = {"stop_id": stop_id, "name": name, "mode": MODE_OF[agency],
               "line": stop_id.split("-")[1] if agency == "metro" else "",
               "lat": lat, "lon": lon,
               # interchange_group_id links stops ACROSS agencies (a stop
               # co-located with stops of another mode); never two metro stops
               "has_transfer_links": isinstance(ixg, str)}
        for r in RADII_M:
            sh = circle_shares(win, dx_m, dy_m, r)
            for k in STAT_KEYS:
                row[f"{k}_share_{r}m"] = round(sh[f"{k}_share"], 4)
        rows.append(row)

    out = pd.DataFrame(rows)
    out["green_rank"] = (out.groupby("mode")["green_share_500m"]
                         .rank(ascending=False).astype(int))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)

    print(f"\nwrote {OUT}  ({len(out)} stops; {skipped} skipped outside raster)")
    med = out.groupby("mode")["green_share_500m"].median()
    print("median green share, 500 m, by mode:")
    print((med * 100).round(1).to_string())
    print("\ngreenest / least green metro 500 m walksheds:")
    metro = out[out["mode"] == "metro"].sort_values(by=["green_rank"]).head(3)
    print(metro[["green_rank", "name", "green_share_500m"]].to_string(index=False))


if __name__ == "__main__":
    main()
