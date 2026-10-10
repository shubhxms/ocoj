"""Build the unified stop table: GTFS bus stops (agency-resolved) + GMRC metro stations.

Output: OCOJ/data/stops_unified.csv
  stop_id, name, lat, lon, agency (pipe-joined), mode (amts|brts|gtsl_bus|multi|metro), n_routes
"""
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GTFS = ROOT / "metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted"
SCRAPED = ROOT / "metro-hacks-data/01_transit/secondary_scraped/stops.csv"
GMRC = ROOT / "metro-hacks-data/01_transit/metro/fares_stations/gmrc_station_list_official_site.csv"
OUT = ROOT / "OCOJ/data/stops_unified.csv"


def gtfs(name):
    return pd.read_csv(GTFS / name, dtype=str, encoding="utf-8-sig")


MODE = {"AMTS": "amts", "AJL": "brts", "GTSL": "gtsl_bus"}


def mode_of(agencies: str) -> str:
    modes = sorted({MODE[p] for p in agencies.split("|")})
    return modes[0] if len(modes) == 1 else "multi"


# ---- bus stops: resolve agency + route count via stop_times -> trips -> routes ----
stops = gtfs("stops.txt")
stops[["stop_lat", "stop_lon"]] = stops[["stop_lat", "stop_lon"]].astype(float)

st = gtfs("stop_times.txt")[["trip_id", "stop_id"]]
trips = gtfs("trips.txt")[["trip_id", "route_id"]]
routes = gtfs("routes.txt")[["route_id", "agency_id"]]

joined = st.merge(trips, on="trip_id", how="left").merge(routes, on="route_id", how="left")
agg = joined.groupby("stop_id").agg(
    agencies=("agency_id", lambda s: "|".join(sorted(set(s.dropna())))),
    n_routes=("route_id", "nunique"),
)

agg_df = pd.DataFrame(agg).reset_index()
bus = stops.merge(agg_df, on="stop_id", how="left")
bus["agencies"] = bus["agencies"].fillna("UNKNOWN")
bus_out = pd.DataFrame(
    {
        "stop_id": bus["stop_id"],
        "name": bus["stop_name"].str.strip(),
        "lat": bus["stop_lat"],
        "lon": bus["stop_lon"],
        "agency": bus["agencies"],
        "mode": bus["agencies"].map(mode_of),
        "n_routes": bus["n_routes"].fillna(0).astype(int),
    }
)

# ---- metro: zip operational GMRC list with scraped coords (scraped list is in GMRC
# order and skips GMRC #29, Sabarmati Railway Station, under construction) ----
op = pd.read_csv(GMRC, dtype=str, encoding="utf-8-sig")
op = op[op["status_on_gmrc_site"] == "operational"].reset_index(drop=True)
scr = pd.read_csv(SCRAPED, dtype=str, encoding="utf-8-sig")
scr = scr[scr["agency"] == "metro"].reset_index(drop=True)
assert len(op) == len(scr) == 53, f"GMRC list {len(op)} vs scraped {len(scr)}"

metro = pd.DataFrame(
    {
        "stop_id": [f"GMRC_{int(i):02d}" for i in op["gmrc_station_id"]],
        "name": op["station_name"].astype(str).str.strip(),
        "lat": pd.to_numeric(scr["lat"]).to_numpy(),
        "lon": pd.to_numeric(scr["lon"]).to_numpy(),
        "agency": "metro",
        "mode": "metro",
        "n_routes": 0,  # filled by the graph builder (line membership)
    }
)

out = pd.concat([metro, bus_out], ignore_index=True)
out.to_csv(OUT, index=False)

print(f"wrote {OUT}: {len(out)} stops")
print(out["mode"].value_counts().to_string())
print("\nagency combos (bus):")
print(bus_out["agency"].value_counts().to_string())
