"""Build the two data inputs for the McRAPTOR router (folder OCOJ).

Outputs:
  OCOJ/data/timetable.parquet  -- one row per trip (bus GTFS verbatim + synth metro)
  OCOJ/data/walk_edges.parquet -- all unordered stop pairs within 1.2 km haversine

METRO TIMETABLE SOURCE DECISION (documented prominently):
  Inspected both candidates:
  (1) gmrc_timetable_phase2_wef_2026-05-18_tidy.csv -- REAL per-trip times, but only
      84 through-services on the APMC<->Gandhinagar/GIFT corridor, calling at 6-8
      timepoint stations. It has NO Blue line, NO full-line stop coverage, and cannot
      produce the per-line trips (GMRC_BLUE/RED/YELLOW/VIOLET over route_paths
      sequences) that this timetable requires.
  (2) gmrc_line_frequency_wef_2026-05-18.csv -- real daypart-varying headways, first/
      last train times and spans for ALL four lines.
  => USED (2) the line-frequency file as the metro source (real spans/headways),
     with per-stop run times accumulated from the official GMRC OD matrix
     (travel_time_min between consecutive route stops, reverse lookup as needed).
     Per-stop times are therefore synthetic (is_synth=True); dwell = 0 (limitation).
     The tidy through-service times are kept as a cross-check (printed below).

Metro limitations (documented):
  * dwell times are zero (OD matrix gives pure inter-station run minutes);
  * headways are the published averages stepped from each terminus's first train;
  * the published last train of each direction is always included exactly (if the
    headway chain misses it by <= 5 min the final chain departure is snapped to it,
    otherwise it is appended);
  * Violet line has a published bus-only gap 10:18-16:06 with no metro trips;
  * RED: published "from APMC" times are anchored to the route_paths UP trip's
    first stop (GMRC_11 Old High Court), because route_paths models the Red line
    as starting at Old High Court (see anomaly note in the run report).
"""

import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GTFS = ROOT / "metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted"
GMRC_OD = ROOT / "metro-hacks-data/01_transit/metro/fares_stations/gmrc_fare_od_matrix_official_api.csv"
ROUTE_PATHS = ROOT / "OCOJ/data/route_paths.parquet"
STOPS = ROOT / "OCOJ/data/stops_unified.csv"
OUT_TT = ROOT / "OCOJ/data/timetable.parquet"
OUT_WALK = ROOT / "OCOJ/data/walk_edges.parquet"

WALK_MAX_KM = 1.2
EARTH_R_KM = 6371.0088

TT_COLUMNS = ["trip_id", "route_id", "agency", "is_synth", "n_stops",
              "first_dep_sec", "stop_ids_str", "dep_secs_str"]


def gtfs(name: str) -> pd.DataFrame:
    return pd.read_csv(GTFS / name, dtype=str, encoding="utf-8-sig")


def hhmm_to_sec(s: str) -> int:
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(x)


def dep_ints(dep_secs_str: str) -> list:
    return [int(x) for x in dep_secs_str.split("|")]


# ---------------------------------------------------------------- bus (GTFS)

def build_bus_trips() -> pd.DataFrame:
    """Every GTFS trip verbatim: departure times only, hours >24 kept raw."""
    st = gtfs("stop_times.txt")[["trip_id", "departure_time", "stop_id", "stop_sequence"]]
    trips = gtfs("trips.txt")[["trip_id", "route_id"]]
    routes = gtfs("routes.txt")[["route_id", "agency_id"]]

    assert st.trip_id.isin(trips.trip_id).all(), "stop_times trip_id missing from trips.txt"
    assert trips.route_id.isin(routes.route_id).all(), "trips route_id missing from routes.txt"

    agency = dict(zip(routes.route_id, routes.agency_id))
    route_of = dict(zip(trips.trip_id, trips.route_id))

    st["dep_sec"] = st.departure_time.map(hhmm_to_sec)
    st["seq"] = st.stop_sequence.astype(int)
    st = st.sort_values(by=["trip_id", "seq"])

    rows = []
    for tid, grp in st.groupby("trip_id", sort=False):
        deps = grp.dep_sec.tolist()
        rows.append({
            "trip_id": tid,
            "route_id": route_of[tid],
            "agency": agency[route_of[tid]],
            "is_synth": False,
            "n_stops": len(deps),
            "first_dep_sec": int(deps[0]),
            "stop_ids_str": "|".join(grp.stop_id),
            "dep_secs_str": "|".join(str(d) for d in deps),
        })
    return pd.DataFrame(rows, columns=TT_COLUMNS)


# --------------------------------------------------------------- metro synth

def load_od_minutes() -> dict:
    """(from_int, to_int) -> minutes, both directions of every official OD pair."""
    od = pd.read_csv(GMRC_OD, encoding="utf-8-sig")
    lookup = {}
    for r in od.to_dict("records"):
        mins = int(str(r["travel_time_min"]).strip())
        a, b = int(r["from_id"]), int(r["to_id"])
        lookup[(a, b)] = mins
        lookup[(b, a)] = mins  # symmetric reverse lookup
    return lookup


def line_dep_secs(stop_ids: list, od: dict) -> list:
    """Cumulative departure seconds along a line order; 0 at the first stop."""
    secs = [0]
    for a, b in zip(stop_ids, stop_ids[1:]):
        ia, ib = int(a.split("_")[1]), int(b.split("_")[1])
        secs.append(secs[-1] + od[(ia, ib)] * 60)
    return secs


# Daypart headway tables transcribed from gmrc_line_frequency_wef_2026-05-18.csv.
# (start "HH:MM", end "HH:MM", headway_seconds | None) -- None headway = no service.
# Weekday profile chosen: GTFS bus service_id covers all days in one pattern.
METRO_SCHEDULES = {
    # Blue: peak(08-11 & 17-20) 7 / non-peak 10 / shoulders(06:20-07:00 & 22:00-23:00) 20
    ("GMRC_BLUE", "UP"): ("06:20", "23:00", [
        ("06:20", "07:00", 1200), ("07:00", "08:00", 600), ("08:00", "11:00", 420),
        ("11:00", "17:00", 600), ("17:00", "20:00", 420), ("20:00", "22:00", 600),
        ("22:00", "23:00", 1200)]),
    ("GMRC_BLUE", "DN"): ("06:20", "23:00", [
        ("06:20", "07:00", 1200), ("07:00", "08:00", 600), ("08:00", "11:00", 420),
        ("11:00", "17:00", 600), ("17:00", "20:00", 420), ("20:00", "22:00", 600),
        ("22:00", "23:00", 1200)]),
    # Red: 06:16-22:00 @12, 22:00-23:10 @20. UP published "from APMC", DN "from Koteshwar Road".
    ("GMRC_RED", "UP"): ("06:20", "23:10", [
        ("06:16", "22:00", 720), ("22:00", "23:10", 1200)]),
    ("GMRC_RED", "DN"): ("06:16", "23:00", [
        ("06:16", "22:00", 720), ("22:00", "23:10", 1200)]),
    # Yellow: 06:40-08:00 @40 (avg), 08:00-21:00 @24 (avg); window end extended to last train.
    ("GMRC_YELLOW", "UP"): ("06:55", "21:20", [
        ("06:40", "08:00", 2400), ("08:00", "21:20", 1440)]),
    ("GMRC_YELLOW", "DN"): ("06:40", "21:00", [
        ("06:40", "08:00", 2400), ("08:00", "21:00", 1440)]),
    # Violet: 07:36-10:18 @49 (avg), 10:18-16:06 BUS ONLY, 16:06-19:13 @57 (avg).
    ("GMRC_VIOLET", "UP"): ("07:36", "18:57", [
        ("07:36", "10:18", 2940), ("10:18", "16:06", None), ("16:06", "19:13", 3420)]),
    ("GMRC_VIOLET", "DN"): ("07:48", "19:13", [
        ("07:36", "10:18", 2940), ("10:18", "16:06", None), ("16:06", "19:13", 3420)]),
}


def headway_at(windows: list, t: int):
    """(headway_seconds | None, window_end) of the latest window with start <= t < end."""
    for start, end, hw in windows:
        if hhmm_to_sec(start + ":00") <= t < hhmm_to_sec(end + ":00"):
            return hw, hhmm_to_sec(end + ":00")
    return None, None


def departures(first: str, last: str, windows: list) -> list:
    """Terminus departure seconds stepped through daypart headways.

    Published last train always present exactly: snap if within 5 min, else append.
    """
    first_s, last_s = hhmm_to_sec(first + ":00"), hhmm_to_sec(last + ":00")
    deps, cur = [first_s], first_s
    while True:
        hw, gap_end = headway_at(windows, cur)
        if hw is None:                       # bus-only window: resume at its end
            assert gap_end is not None
            cur = gap_end
            continue
        cur += hw
        if cur > last_s:
            break
        deps.append(cur)
    if deps[-1] != last_s:
        if last_s - deps[-1] <= 5 * 60:
            deps[-1] = last_s
        else:
            deps.append(last_s)
    return deps


def build_metro_trips(od: dict):
    """Synth trips per line x direction over route_paths stop sequences."""
    rp = pd.read_parquet(ROUTE_PATHS)
    rp = rp.loc[rp["route_id"].str.startswith("GMRC_")]  # bus route paths share this file
    rows, info = [], {}
    for r in rp.to_dict("records"):
        line = r["route_id"]
        assert line.startswith("GMRC_"), f"unexpected route_id {line} in route_paths"
        short = line.removeprefix("GMRC_")
        stops = r["stop_ids_str"].split("|")
        for direction, seq in (("UP", stops), ("DN", stops[::-1])):
            first, last, windows = METRO_SCHEDULES[(line, direction)]
            deps = departures(first, last, windows)
            cum = line_dep_secs(seq, od)
            for i, d0 in enumerate(deps, 1):
                rows.append({
                    "trip_id": f"GMRC_SYNTH_{short}_{direction}_{i:03d}",
                    "route_id": line,
                    "agency": "metro",
                    "is_synth": True,
                    "n_stops": len(seq),
                    "first_dep_sec": int(d0),
                    "stop_ids_str": "|".join(seq),
                    "dep_secs_str": "|".join(str(d0 + c) for c in cum),
                })
            info[(line, direction)] = (len(deps), first, last, len(seq))
    return pd.DataFrame(rows, columns=TT_COLUMNS), info


# ---------------------------------------------------------------- walk edges

def build_walk_edges():
    """All unordered stop pairs with 0 < haversine km <= 1.2, emitted both ways."""
    stops = pd.read_csv(STOPS, encoding="utf-8-sig", dtype={"stop_id": str})
    lat = np.radians(stops.lat.to_numpy(float))
    lon = np.radians(stops.lon.to_numpy(float))
    ids = stops.stop_id.to_numpy()
    n = len(stops)

    pairs_a, pairs_b, pairs_km = [], [], []
    step = 512
    for i0 in range(0, n, step):                       # row-chunked full pairwise
        i1 = min(i0 + step, n)
        dlat = lat[i0:i1, None] - lat[None, :]
        dlon = lon[i0:i1, None] - lon[None, :]
        h = np.sin(dlat / 2) ** 2 + np.cos(lat[i0:i1, None]) * np.cos(lat[None, :]) * np.sin(dlon / 2) ** 2
        km = 2 * EARTH_R_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
        # keep only global upper triangle: col > global row (no self pairs, no dupes)
        local_rows, cols = np.nonzero(np.triu(np.ones((i1 - i0, n), dtype=bool), k=i0 + 1))
        vals = km[local_rows, cols]
        keep = (vals > 0) & (vals <= WALK_MAX_KM)
        pairs_a.append(ids[local_rows[keep] + i0])
        pairs_b.append(ids[cols[keep]])
        pairs_km.append(np.round(vals[keep], 4))

    a = np.concatenate(pairs_a)
    b = np.concatenate(pairs_b)
    km = np.concatenate(pairs_km)
    edges = pd.DataFrame({
        "stop_a": np.concatenate([a, b]),
        "stop_b": np.concatenate([b, a]),
        "walk_km": np.concatenate([km, km]),
    }).sort_values(["stop_a", "walk_km", "stop_b"]).reset_index(drop=True)
    return edges, stops


# ------------------------------------------------------------------- verify

def verify_timetable(tt: pd.DataFrame, bus: pd.DataFrame) -> int:
    assert (tt.stop_ids_str.str.count(r"\|") + 1 == tt.n_stops).all(), "stop_ids length != n_stops"
    assert (tt.dep_secs_str.str.count(r"\|") + 1 == tt.n_stops).all(), "dep_secs length != n_stops"

    zero_deltas = 0
    for row in tt.to_dict("records"):
        d = dep_ints(row["dep_secs_str"])
        assert all(x <= y for x, y in zip(d, d[1:])), f"decreasing dep secs in {row['trip_id']}"
        if not row["is_synth"]:
            zero_deltas += sum(1 for x, y in zip(d, d[1:]) if x == y)
    assert zero_deltas == sum(
        sum(1 for x, y in zip(dep_ints(r["dep_secs_str"]), dep_ints(r["dep_secs_str"])[1:]) if x == y)
        for r in bus.to_dict("records")), "zero-delta count mismatch"
    return zero_deltas


def reverify_random_gtfs(tt: pd.DataFrame, k: int = 3, seed: int = 42) -> list:
    picked = tt[~tt["is_synth"]].sample(k, random_state=seed)["trip_id"].tolist()
    raw = gtfs("stop_times.txt")[["trip_id", "departure_time", "stop_id", "stop_sequence"]]
    for tid in picked:
        out = tt[tt["trip_id"] == tid].iloc[0]
        src = raw.loc[raw["trip_id"] == tid].copy()
        src["seq"] = src["stop_sequence"].astype(int)
        src = src.sort_values(by="seq")
        assert out.stop_ids_str == "|".join(src.stop_id), f"{tid}: stop ids differ"
        assert out.dep_secs_str == "|".join(str(hhmm_to_sec(t)) for t in src.departure_time), \
            f"{tid}: dep secs differ"
    return picked


def verify_walk(edges: pd.DataFrame):
    assert (edges.stop_a != edges.stop_b).all(), "self-pair in walk_edges"
    assert (edges.walk_km > 0).all() and (edges.walk_km <= WALK_MAX_KM).all(), "km out of range"
    lookup = {(r["stop_a"], r["stop_b"]): r["walk_km"] for r in edges.to_dict("records")}
    for (a, b), km in lookup.items():
        assert lookup.get((b, a)) == km, f"missing/asymmetric reverse edge {a}<->{b}"
    assert len(lookup) == len(edges), "duplicate directed edges"


# --------------------------------------------------------------------- main

def main():
    bus = build_bus_trips()
    od = load_od_minutes()
    metro, metro_info = build_metro_trips(od)
    tt = pd.concat([bus, metro], ignore_index=True)
    tt = tt.sort_values(["route_id", "first_dep_sec", "trip_id"]).reset_index(drop=True)
    tt = tt.astype({"is_synth": bool, "n_stops": "int64", "first_dep_sec": "int64"})
    tt.to_parquet(OUT_TT, index=False)

    edges, stops = build_walk_edges()
    edges.to_parquet(OUT_WALK, index=False)

    print(f"timetable.parquet: {len(tt)} rows -> {OUT_TT}")
    print("\ntrips by agency:")
    for agency, cnt in tt.agency.value_counts().items():
        print(f"  {agency:6s} {cnt}")
    print("\nmetro synth trips per line+direction (count, span, n_stops):")
    for (line, direction), (cnt, first, last, n) in sorted(metro_info.items()):
        print(f"  {line:14s} {direction}  {cnt:3d} trips  {first}-{last}  {n} stops")
    print("\nmetro source decision: gmrc_line_frequency_wef_2026-05-18.csv (real daypart\n"
          "headways/spans for all 4 lines); per-stop run times from the official GMRC OD\n"
          "matrix. The tidy per-trip file covers only the APMC<->Gandhinagar through\n"
          "corridor (84 services, 6-8 timepoint stations) and cannot build line trips.")

    patterns = tt.groupby(["route_id", "stop_ids_str"]).ngroups
    bus_patterns = bus.groupby(["route_id", "stop_ids_str"]).ngroups
    print(f"\npatterns (distinct route_id + stop sequence): {patterns} (bus {bus_patterns}, metro 8)")

    zero_deltas = verify_timetable(tt, bus)
    print(f"zero-second consecutive deltas kept verbatim (GTFS minute granularity): {zero_deltas}")

    picked = reverify_random_gtfs(tt)
    print(f"random GTFS trips re-verified against raw stop_times: {picked}")

    verify_walk(edges)
    mode = dict(zip(stops.stop_id, stops["mode"]))
    ma = edges.stop_a.map(mode)
    mb = edges.stop_b.map(mode)
    n_mm = int(((ma == "metro") & (mb == "metro")).sum())
    n_bm = int(((ma != "metro") & (mb == "metro")).sum())
    fine = Counter("+".join(sorted(p)) for p in zip(ma, mb))
    print(f"\nwalk_edges.parquet: {len(edges)} rows -> {OUT_WALK}")
    print(f"  bus-bus {len(edges) - n_mm - 2 * n_bm} | bus-metro {2 * n_bm} | metro-metro {n_mm}")
    print(f"  fine-grained mode pairs: {dict(sorted(fine.items()))}")
    print(f"  max walk_km: {edges.walk_km.max():.4f}")

    rp = pd.read_parquet(ROUTE_PATHS)
    rp = rp.loc[rp["route_id"].str.startswith("GMRC_")]
    print("\ncross-check: synth end-to-end run times (OD-cumulative, 0 dwell) vs published:")
    published = {"GMRC_BLUE": 45, "GMRC_RED": 35, "GMRC_YELLOW": 43, "GMRC_VIOLET": 6}
    for r in rp.to_dict("records"):
        cum = line_dep_secs(r["stop_ids_str"].split("|"), od)
        print(f"  {r['route_id']:14s} {cum[-1] / 60:5.0f} min vs published {published[r['route_id']]} min")

    back = pd.read_parquet(OUT_TT)
    backw = pd.read_parquet(OUT_WALK)
    assert list(back.columns) == TT_COLUMNS
    assert list(backw.columns) == ["stop_a", "stop_b", "walk_km"]
    print("\nboth parquets re-loaded OK; all asserts passed.")


if __name__ == "__main__":
    main()
