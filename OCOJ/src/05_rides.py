"""Generate mock rider journeys (origin->destination rides) for router testing.

Two demand sources:
  - od_weighted: station pairs sampled from the 2015 AJL BRTS station-to-station
    OD matrix with probability proportional to monthly trip totals. 2015 station
    names are fuzzy-matched to stops_unified (brts/multi names preferred, any
    mode as fallback); pairs whose endpoints fail matching are skipped and
    resampled. If matching stalls, the remainder is topped up with uniform
    pairs under source="od_weighted_topup".
  - uniform: random distinct stop pairs from all 3,288 unified stops with
    straight-line (haversine) distance >= 1.0 km.

Departure times are a peak mixture: 40% morning N(9.0, 1.2h) clipped [7,11],
30% midday U(13,17), 30% evening N(18.5, 1.0h) clipped [17,21].

Output: OCOJ/data/rides_mock.csv (3,000 rows, deterministic under seed 42)
  ride_id, source, o_stop_id, o_name, d_stop_id, d_name, straight_km, dep_time, peak_label
"""
import difflib
import math
import random
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STOPS = ROOT / "OCOJ/data/stops_unified.csv"
OD = ROOT / "metro-hacks-data/01_transit/bus_brts/station_ridership_brts_2015/ajl_brts_2015_monthly_OD_station_to_station.csv"
OUT = ROOT / "OCOJ/data/rides_mock.csv"

N_OD = 1500
N_UNIFORM = 1500
MIN_KM = 1.0
CUTOFF = 0.75
MAX_OD_ATTEMPTS = 200_000
MIXTURE = {"morning_peak": 0.40, "midday": 0.30, "evening_peak": 0.30}

random.seed(42)


def normalize(name: str) -> str:
    """Lowercase, strip diacritics/punctuation and any leading station-id prefix."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"^\s*\d+\s*[-.]?\s*", "", s)  # e.g. "123-Name" / "123 Name"
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def records(df: pd.DataFrame) -> list[dict]:
    cols = ["stop_id", "name", "lat", "lon", "mode"]
    return [dict(zip(cols, row)) for row in df[cols].itertuples(index=False, name=None)]


# ---- fuzzy-match 2015 BRTS station names to unified stops ----
stops = pd.read_csv(STOPS)
pools: dict[str, list[tuple[str, dict]]] = {}
for modes, key in ((("brts", "multi"), "pref"), (None, "all")):
    sub: pd.DataFrame = stops if modes is None else stops[stops["mode"].isin(modes)]
    pools[key] = [(normalize(r["name"]), r) for r in records(sub)]


def match_station(station: str):
    """Closest unified stop for an OD station name; brts/multi pool first."""
    n = normalize(station)
    for key in ("pref", "all"):
        names = [p[0] for p in pools[key]]
        cand = difflib.get_close_matches(n, names, n=1, cutoff=CUTOFF)
        if cand:
            return pools[key][names.index(cand[0])][1]
    return None


od_long = pd.read_csv(OD)
stations = sorted(set(od_long["origin_station"]) | set(od_long["destination_station"]))
stations = [s for s in stations if not s.startswith("9990")]  # fare artifact, not a station

station_stop = {s: match_station(s) for s in stations}
unmatched = sorted(s for s, r in station_stop.items() if r is None)
n_matched = len(stations) - len(unmatched)
print(f"OD stations: {len(stations)} distinct (excluded '9990-Student/Special Card')")
print(f"matched: {n_matched}  rate: {n_matched / len(stations):.1%}")
if unmatched:
    print("unmatched:", ", ".join(unmatched))

# ---- aggregate monthly OD rows to pair totals, keep fully-matched pairs ----
pairs: pd.DataFrame = od_long.groupby(
    ["origin_station", "destination_station"], as_index=False
)["passengers"].sum()
valid = []
for o_name, d_name, pax in zip(
    pairs["origin_station"], pairs["destination_station"], pairs["passengers"]
):
    o_stop, d_stop = station_stop.get(o_name), station_stop.get(d_name)
    if o_stop is None or d_stop is None or o_stop["stop_id"] == d_stop["stop_id"]:
        continue
    valid.append((o_stop, d_stop, int(pax)))
o_pool = [p[0] for p in valid]
d_pool = [p[1] for p in valid]
w_pool = [p[2] for p in valid]
total_pax = pairs["passengers"].sum()
print(f"OD pairs: {len(pairs)} aggregated, {len(valid)} fully matched "
      f"({sum(w_pool) / total_pax:.1%} of trip weight)")

stop_by_id: dict[str, dict] = {r["stop_id"]: r for r in records(stops)}


def km_between(o: dict, d: dict) -> float:
    return haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])


# ---- source 1: OD-weighted sampling over the matched pair pool ----
rides: list[dict] = []
attempts = 0
while len(rides) < N_OD and attempts < MAX_OD_ATTEMPTS:
    attempts += 1
    i = random.choices(range(len(valid)), weights=w_pool, k=1)[0]
    rides.append({"source": "od_weighted", "o": o_pool[i], "d": d_pool[i]})
n_topup = N_OD - len(rides)
if n_topup:
    print(f"WARNING: OD sampling stalled after {attempts} attempts; topping up {n_topup} rows uniformly")

# ---- source 2: uniform pairs over all stops (>= 1 km apart) ----
ids = list(stop_by_id)
while len(rides) < N_OD + N_UNIFORM:
    o_id, d_id = random.sample(ids, 2)
    o, d = stop_by_id[o_id], stop_by_id[d_id]
    if km_between(o, d) < MIN_KM:
        continue
    src = "od_weighted_topup" if len(rides) < N_OD + n_topup else "uniform"
    rides.append({"source": src, "o": o, "d": d})

# ---- departure-time mixture: pick the component, then draw from it ----
def dep_time(label: str) -> str:
    if label == "morning_peak":
        h = min(11.0, max(7.0, random.gauss(9.0, 1.2)))
    elif label == "midday":
        h = random.uniform(13.0, 17.0)
    else:
        h = min(21.0, max(17.0, random.gauss(18.5, 1.0)))
    minutes = int(round(h * 60))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


labels = list(MIXTURE)
weights = list(MIXTURE.values())
peak = [random.choices(labels, weights=weights, k=1)[0] for _ in rides]

out = pd.DataFrame(
    {
        "ride_id": range(1, len(rides) + 1),
        "source": [r["source"] for r in rides],
        "o_stop_id": [r["o"]["stop_id"] for r in rides],
        "o_name": [r["o"]["name"] for r in rides],
        "d_stop_id": [r["d"]["stop_id"] for r in rides],
        "d_name": [r["d"]["name"] for r in rides],
        "straight_km": [round(km_between(r["o"], r["d"]), 2) for r in rides],
        "dep_time": [dep_time(p) for p in peak],
        "peak_label": peak,
    }
)

out.to_csv(OUT, index=False)
print(f"\nwrote {OUT}: {len(out)} rows")

# ---- report ----
print("\ntop-10 busiest matched OD pairs (2015 trips):")
for o, d, w in sorted(valid, key=lambda p: -p[2])[:10]:
    print(f"  {o['name']} -> {d['name']}: {w:,}")

print("\nrides by source:")
print(out.groupby("source").size().to_string())
print("rides by peak_label:")
print(out.groupby("peak_label").size().to_string())

print("\nstraight_km by source:")
print(out.groupby("source")["straight_km"].describe()[["min", "50%", "mean", "max"]].round(2).to_string())
