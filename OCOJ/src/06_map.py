"""Render the multi-modal network: stops, route polylines, metro-bus walk links.

Output: OCOJ/out/map_routes.html (folium, OSM tiles)
"""
import math
from pathlib import Path

import folium
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STOPS = ROOT / "OCOJ/data/stops_unified.csv"
PATHS = ROOT / "OCOJ/data/route_paths.parquet"
OUT = ROOT / "OCOJ/out/map_routes.html"
OUT.parent.mkdir(exist_ok=True)

STOP_COLOR = {"metro": "#d62728", "brts": "#ff7f0e", "amts": "#2ca02c", "gtsl_bus": "#9467bd", "multi": "#7f7f7f"}
ROUTE_COLOR = {"AMTS": "#2ca02c", "AJL": "#ff7f0e", "GTSL": "#9467bd"}  # agency_id -> color
METRO_COLOR = {"GMRC_BLUE": "#1f77b4", "GMRC_RED": "#d62728", "GMRC_YELLOW": "#bcbd22", "GMRC_VIOLET": "#9467bd"}

WALK_M = 800.0


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def line_of(rid):
    """GMRC_BLUE -> blue, for popups."""
    return rid.split("_")[1].lower()


def main():
    stops = pd.read_csv(STOPS, dtype=str, encoding="utf-8-sig")
    stops[["lat", "lon"]] = stops[["lat", "lon"]].astype(float)
    paths = pd.read_parquet(PATHS)

    m = folium.Map(location=[23.03, 72.58], zoom_start=11, tiles="OpenStreetMap")

    # ---- walk/transfer links: metro station <-> every bus stop within 800 m ----
    lat = stops["lat"].to_numpy()
    lon = stops["lon"].to_numpy()
    is_metro = (stops["mode"] == "metro").to_numpy()
    m_idx = [i for i in range(len(stops)) if is_metro[i]]
    b_idx = [i for i in range(len(stops)) if not is_metro[i]]
    n_walk = 0
    for mi in m_idx:
        for bi in b_idx:
            if haversine_m(lat[mi], lon[mi], lat[bi], lon[bi]) <= WALK_M:
                folium.PolyLine(
                    [[lat[mi], lon[mi]], [lat[bi], lon[bi]]],
                    color="grey", weight=1, opacity=0.6, dash_array="4,6",
                ).add_to(m)
                n_walk += 1

    # ---- one polyline per route ----
    coord = stops.set_index("stop_id")
    for row in paths.to_dict("records"):
        ids = str(row["stop_ids_str"]).split("|")
        pts = []
        for sid in ids:
            if sid in coord.index:
                s = coord.loc[sid]
                pts.append((float(s["lat"]), float(s["lon"])))
        if len(pts) < 2:
            continue
        if str(row["route_id"]).startswith("GMRC_"):
            folium.PolyLine(
                pts, color=METRO_COLOR[row["route_id"]], weight=4, opacity=0.9,
                tooltip=f"{row['route_id']} ({row['n_stops']} stops)",
            ).add_to(m)
        else:
            folium.PolyLine(
                pts, color=ROUTE_COLOR[row["agency"]], weight=2, opacity=0.4,
            ).add_to(m)  # no popup/tooltip on bus lines: file size

    # ---- stops ----
    for s in stops.to_dict("records"):
        if s["mode"] == "metro":
            folium.CircleMarker(
                [s["lat"], s["lon"]], radius=5, color=STOP_COLOR[s["mode"]], fill=True, fill_opacity=0.9,
                popup=f"{s['name']} ({s['stop_id']})",
            ).add_to(m)
        else:
            folium.CircleMarker(
                [s["lat"], s["lon"]], radius=1.5, color=STOP_COLOR[s["mode"]], fill=True, fill_opacity=0.7,
            ).add_to(m)

    m.save(OUT)
    size_mb = OUT.stat().st_size / 1e6
    print(f"wrote {OUT}: {size_mb:.1f} MB, {n_walk} walk links")


if __name__ == "__main__":
    main()
