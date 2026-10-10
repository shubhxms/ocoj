"""OCOJ 10 — join BTH per-stop exposure (AQI, shade-adjusted UV, tree cover) onto the router's stops.

BTH/out/stop_exposure.csv uses the scraped stop namespace (brt-6871, amts-*, metro-*),
OCOJ uses GTFS ids (AMC_*, GTSL_*) + GMRC_xx. Join by nearest coordinate (haversine).

Exposure index per stop (0..1), documented:
    aqi_n = clip(aqi_mean_est / 300, 0, 1)          # 300 ~ hazardous ceiling
    uv_n  = clip(uv_exposure_midday / 11, 0, 1)     # 11 ~ extreme UVI
    exp   = 0.6 * aqi_n + 0.4 * uv_n                # AQI weighs more (chronic vs acute)
Tree share is NOT added on top: BTH's uv_exposure_* is already shade-adjusted
(sky-view/tree), so counting trees again would double-count shade.

Output: OCOJ/data/stop_exposure_joined.csv
  stop_id, aqi, uv_midday, tree_share, exp, match_dist_m, matched
Unmatched stops (>150 m from any BTH stop) get city-median values, matched=False.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BTH = ROOT / "BTH" / "out" / "stop_exposure.csv"
STOPS = ROOT / "OCOJ" / "data" / "stops_unified.csv"
OUT = ROOT / "OCOJ" / "data" / "stop_exposure_joined.csv"

R_EARTH_M = 6371_008.8
MAX_MATCH_M = 150.0


def main() -> None:
    bth = pd.read_csv(BTH, encoding="utf-8-sig")
    stops = pd.read_csv(STOPS, dtype={"stop_id": str}, encoding="utf-8-sig")

    bl = np.radians(bth["lat"].to_numpy())
    bo = np.radians(bth["lon"].to_numpy())
    exp_b = (0.6 * (bth["aqi_mean_est"] / 300).clip(0, 1) + 0.4 * (bth["uv_exposure_midday"] / 11).clip(0, 1)).to_numpy()

    rows = []
    for chunk_lat, chunk_lon, chunk_ids in [
        (stops["lat"].to_numpy()[i : i + 500], stops["lon"].to_numpy()[i : i + 500], stops["stop_id"].tolist()[i : i + 500])
        for i in range(0, len(stops), 500)
    ]:
        sl = np.radians(chunk_lat)[:, None]
        so = np.radians(chunk_lon)[:, None]
        # approximate metres: dlat*110574 + dlon*111320*cos(lat) — good enough at city scale
        dm = np.hypot((sl - bl[None, :]) * 1_105_740.0, (so - bo[None, :]) * 1_113_200.0 * np.cos((sl + bl[None, :]) / 2))
        j = dm.argmin(axis=1)
        for k, sid in enumerate(chunk_ids):
            rows.append({"stop_id": sid, "bth_idx": int(j[k]), "match_dist_m": float(dm[k, j[k]])})

    m = pd.DataFrame(rows)
    m["aqi"] = bth["aqi_mean_est"].to_numpy()[m["bth_idx"]]
    m["uv_midday"] = bth["uv_exposure_midday"].to_numpy()[m["bth_idx"]]
    m["tree_share"] = bth["tree_share_500m"].to_numpy()[m["bth_idx"]]
    m["exp"] = exp_b[m["bth_idx"]]
    m["matched"] = m["match_dist_m"] <= MAX_MATCH_M

    med = m.loc[m["matched"], ["aqi", "uv_midday", "tree_share", "exp"]].median()
    for col in ("aqi", "uv_midday", "tree_share", "exp"):
        m.loc[~m["matched"], col] = med[col]

    m[["stop_id", "aqi", "uv_midday", "tree_share", "exp", "match_dist_m", "matched"]].to_csv(OUT, index=False)

    print(f"wrote {OUT}: {len(m)} stops")
    print(f"matched <= {MAX_MATCH_M:.0f} m: {m['matched'].sum()} ({m['matched'].mean():.1%})")
    print(f"match dist m: p50 {m['match_dist_m'].median():.0f} | p90 {m['match_dist_m'].quantile(0.9):.0f} | max {m['match_dist_m'].max():.0f}")
    print(f"unmatched by mode:\n{stops.set_index('stop_id').loc[m.loc[~m['matched'], 'stop_id'], 'mode'].value_counts().to_string()}")
    print(f"\nexp: p10 {m['exp'].quantile(0.1):.3f} | p50 {m['exp'].median():.3f} | p90 {m['exp'].quantile(0.9):.3f}")
    print(f"aqi: p50 {m['aqi'].median():.0f} | uv midday: p50 {m['uv_midday'].median():.1f} | tree: p50 {m['tree_share'].median():.3f}")


if __name__ == "__main__":
    main()
