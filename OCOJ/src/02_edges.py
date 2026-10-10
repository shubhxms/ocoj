"""Build route edges (bus GTFS + metro lines) and per-route stop paths.

Outputs:
  OCOJ/data/edges_rides.parquet   route_id, agency, from_stop_id, to_stop_id, n_trips, med_sec, med_km
  OCOJ/data/route_paths.parquet   route_id, agency, n_stops, stop_ids_str (longest trip / metro line order)

Also prints an undirected-connectivity sanity report over the final edge set.
"""
import math
from collections import defaultdict, deque
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GTFS = ROOT / "metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted"
OD = ROOT / "metro-hacks-data/01_transit/metro/fares_stations/gmrc_fare_od_matrix_official_api.csv"
STOPS = ROOT / "OCOJ/data/stops_unified.csv"
OUT_EDGES = ROOT / "OCOJ/data/edges_rides.parquet"
OUT_PATHS = ROOT / "OCOJ/data/route_paths.parquet"

YELLOW = {
    "Koteshwar Road", "Vishwakarma College", "Tapovan Circle", "Narmada Canal",
    "Koba Circle", "Juna Koba", "Koba Gam", "GNLU", "Raysan", "Randesan",
    "Dholakuva Circle", "Infocity", "Sector-1", "Sector 10A", "Sachivalaya",
    "Akshardham", "Juna Sachivalaya", "Sector-16", "Sector-24", "Mahatama Mandir",
}


def gtfs(name):
    return pd.read_csv(GTFS / name, dtype=str, encoding="utf-8-sig")


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def hms_to_sec(s):
    h, m, sec = s.split(":")
    return (int(h) * 3600 + int(m) * 60 + int(sec)) % 86400  # GTFS wrap: hours >= 24


def metro_lines(stops):
    """gmrc id -> list of line codes (same membership rules as build_map_data.py)."""
    lines = defaultdict(list)
    for sid, name in stops.loc[stops["mode"] == "metro", ["stop_id", "name"]].itertuples(index=False):
        gmrc_id = int(sid.split("_")[1])
        if gmrc_id <= 18:
            ls = ["blue"]
        elif gmrc_id <= 32:
            ls = ["red"]
        elif name in YELLOW:
            ls = ["yellow"]
        else:
            ls = ["violet"]
        if name == "Old High Court":
            ls = ["blue", "red"]
        elif name == "Koteshwar Road":
            ls = ["red", "yellow"]
        elif name == "GNLU":
            ls = ["yellow", "violet"]
        for ln in ls:
            lines[ln].append((gmrc_id, sid))
    out = {}
    for ln, ids in lines.items():
        sids = [sid for _, sid in sorted(ids)]
        if ln == "red":
            # OD-matrix evidence: t(11,19)=14, t(11,33)=21, t(19,33)=36 min -> Old High
            # Court (GMRC_11) sits mid-line between Gandhigram (24) and Usmanpura (25),
            # not at the low-id end. 14+21 = 35 min matches the published end-to-end;
            # naive id-sort yields 54 min.
            ohc = next(s for s in sids if int(s.split("_")[1]) == 11)
            rest = [s for s in sids if s != ohc]
            pos = next(i for i, s in enumerate(rest) if int(s.split("_")[1]) >= 25)
            rest.insert(pos, ohc)
            sids = rest
        out[ln] = sids
    return out


def build_bus_edges():
    st = gtfs("stop_times.txt")
    trips = gtfs("trips.txt")[["trip_id", "route_id"]]
    routes = gtfs("routes.txt")[["route_id", "agency_id"]]

    st["stop_sequence"] = pd.to_numeric(st["stop_sequence"])
    st = st.sort_values(["trip_id", "stop_sequence"], kind="mergesort").reset_index(drop=True)

    g = st.groupby("trip_id", sort=False)
    nxt = pd.DataFrame(
        {
            "trip_id": st["trip_id"],
            "from_stop_id": st["stop_id"],
            "to_stop_id": g["stop_id"].shift(-1),
            "dep_sec": st["departure_time"].map(hms_to_sec),
            "arr_sec_next": g["arrival_time"].shift(-1),
            "dep_sec_next": g["departure_time"].shift(-1),
        }
    )
    nxt = nxt.dropna(subset=["to_stop_id"])  # last stop of each trip
    nxt["arr_next"] = nxt["arr_sec_next"].map(hms_to_sec).fillna(nxt["dep_sec_next"].map(hms_to_sec))

    nxt["dur"] = nxt["arr_next"] - nxt["dep_sec"]
    total_rows = len(nxt)
    neg = int((nxt["dur"] <= 0).sum())
    edges = nxt[nxt["dur"] > 0].copy()

    edges = edges.merge(trips, on="trip_id", how="left").merge(routes, on="route_id", how="left")
    missing_route = int(edges["agency_id"].isna().sum().item())

    agg = (
        edges.groupby(["route_id", "from_stop_id", "to_stop_id"], sort=False)
        .agg(agency=("agency_id", "first"), n_trips=("trip_id", "nunique"), med_sec=("dur", "median"))
        .reset_index()
    )
    return agg, {"rows_from_stop_times": total_rows, "dropped_nonpos_dur": neg, "rows_missing_route": missing_route}


def build_metro_edges(stops):
    od = pd.read_csv(OD, dtype=str, encoding="utf-8-sig")
    key = {}
    for a, b, dist, tt in od[["from_id", "to_id", "distance_km", "travel_time_min"]].itertuples(index=False):
        key[(int(a), int(b))] = (float(dist), int(tt) * 60)
    by_name = dict(zip(stops.loc[stops["mode"] == "metro", "name"], stops.loc[stops["mode"] == "metro", "stop_id"]))

    rows = []
    for ln, sids in metro_lines(stops).items():
        for a, b in zip(sids, sids[1:]):
            ga, gb = int(a.split("_")[1]), int(b.split("_")[1])
            if (ga, gb) in key:
                dist, sec = key[(ga, gb)]
            elif (gb, ga) in key:
                dist, sec = key[(gb, ga)]  # reverse lookup
            else:
                raise KeyError(f"OD matrix has no pair {a}<->{b} (line {ln})")
            rows.append(
                {
                    "route_id": f"GMRC_{ln.upper()}",
                    "agency": "metro",
                    "from_stop_id": a,
                    "to_stop_id": b,
                    "n_trips": 0,
                    "med_sec": float(sec),
                }
            )
    # symmetric edges: a line is traversable both directions
    rows += [
        {**r, "from_stop_id": r["to_stop_id"], "to_stop_id": r["from_stop_id"]} for r in rows
    ]
    return pd.DataFrame(rows)


def add_med_km(edges, stops):
    coord = stops.set_index("stop_id")[["lat", "lon"]]
    f = edges["from_stop_id"].map(coord["lat"]).to_numpy()
    g = edges["from_stop_id"].map(coord["lon"]).to_numpy()
    h = edges["to_stop_id"].map(coord["lat"]).to_numpy()
    i = edges["to_stop_id"].map(coord["lon"]).to_numpy()
    n_missing = int(pd.isna(f).sum().item() + pd.isna(h).sum().item())
    edges["med_km"] = [haversine_km(*t) for t in zip(f, g, h, i)]
    return edges, n_missing


def build_route_paths(stops):
    """One row per route_id: stop sequence of that route's LONGEST trip."""
    st = gtfs("stop_times.txt")[["trip_id", "stop_id", "stop_sequence"]]
    st["seq"] = pd.to_numeric(st["stop_sequence"])
    st = st.sort_values(by=["trip_id", "seq"], kind="mergesort")
    st = st.merge(gtfs("trips.txt")[["trip_id", "route_id"]], on="trip_id", how="left")

    # longest trip per route: most stops; tie-break by trip_id for determinism
    counts = st.groupby(["route_id", "trip_id"], sort=False).size().to_frame("n").reset_index()
    best = counts.sort_values(["n", "trip_id"], ascending=[False, True]).drop_duplicates("route_id")
    longest = st[st["trip_id"].isin(best["trip_id"].tolist())]
    seq = longest.groupby("route_id", sort=False)["stop_id"].apply(lambda s: "|".join(s))

    agency = gtfs("routes.txt").set_index("route_id")["agency_id"]
    rows = [
        {"route_id": rid, "agency": agency.get(rid), "n_stops": len(s.split("|")), "stop_ids_str": s}
        for rid, s in seq.items()
    ]

    for ln, sids in metro_lines(stops).items():
        rows.append(
            {
                "route_id": f"GMRC_{ln.upper()}",
                "agency": "metro",
                "n_stops": len(sids),
                "stop_ids_str": "|".join(sids),
            }
        )
    return pd.DataFrame(rows)


def connectivity_report(edges, stops):
    adj = defaultdict(set)
    for a, b in edges[["from_stop_id", "to_stop_id"]].itertuples(index=False):
        adj[a].add(b)
        adj[b].add(a)

    seen, comps = set(), []
    for s in adj:
        if s in seen:
            continue
        q, comp = deque([s]), 0
        seen.add(s)
        while q:
            u = q.popleft()
            comp += 1
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        comps.append(comp)
    comps.sort(reverse=True)

    all_ids = set(stops["stop_id"])
    isolated = sorted(all_ids - set(adj))  # zero-degree stops
    name_of = stops.set_index("stop_id")["name"]
    top_deg = sorted(adj.items(), key=lambda kv: -len(kv[1]))[:5]

    start = "AMC_2961" if "AMC_2961" in adj else max(adj, key=lambda s: len(adj[s]))
    print("\n===== connectivity (undirected, ride edges only) =====")
    print(f"BFS start: {start} ({name_of.get(start, '?')})")
    print(f"largest component: {comps[0]} stops ({comps[0] / len(all_ids) * 100:.1f}% of {len(all_ids)})")
    print(f"top-5 components: {comps[:5]}")
    print(f"isolated stops (degree 0): {len(isolated)}")
    for sid, nbrs in top_deg:
        print(f"  deg {len(nbrs):4d}  {sid}  {name_of.get(sid, '?')}")
    return comps, isolated


def main():
    stops = pd.read_csv(STOPS, dtype=str, encoding="utf-8-sig")
    stops[["lat", "lon"]] = stops[["lat", "lon"]].astype(float)

    bus, stats = build_bus_edges()
    metro = build_metro_edges(stops)
    edges = pd.concat([bus, metro], ignore_index=True)
    edges, n_nocoord = add_med_km(edges, stops)

    # merge metro duplicates into a single undirected-pair row set? No: keep directed pairs as built.
    edges = edges[
        ["route_id", "agency", "from_stop_id", "to_stop_id", "n_trips", "med_sec", "med_km"]
    ]
    edges.to_parquet(OUT_EDGES, index=False)

    paths = build_route_paths(stops)
    paths.to_parquet(OUT_PATHS, index=False)

    print(f"wrote {OUT_EDGES}: {len(edges)} rows")
    print(f"wrote {OUT_PATHS}: {len(paths)} rows")
    print("\nedges by agency:")
    print(edges.groupby("agency").size().to_string())
    med_sec_series = pd.Series(edges["med_sec"])
    print(f"\nmedian med_sec: {med_sec_series.median():.0f}")
    print(f"metro edges: {(edges['agency'] == 'metro').sum()}")

    print("\n===== stop_times anomalies =====")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print(f"edge endpoints missing from stops_unified: {n_nocoord}")

    connectivity_report(edges, stops)


if __name__ == "__main__":
    main()
