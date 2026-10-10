# OCOJ — Optimal Combination of Journeys (Ahmedabad multi-modal router)

Multi-modal (metro + BRTS + AMTS bus) journey planner over real GTFS schedules and
official fares. Question: for a set of rides, what minimizes cost vs time vs distance,
and how does the walk-transfer threshold change the answer?

## Assets already on disk (all paths relative to repo root)

| Asset | Path | What it gives |
|---|---|---|
| Bus GTFS (AMTS + AJL/BRTS + GTSL) | `metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted/` | 3,235 stops w/ lat-lon, 978 routes, 14,323 trips, 646,854 stop_times — real schedules |
| Metro stations w/ coords | `metro-hacks-data/01_transit/secondary_scraped/stops.csv` (agency = metro) | 53 operational stations, lat/lon |
| Metro fare + time OD matrix | `metro-hacks-data/01_transit/metro/fares_stations/gmrc_fare_od_matrix_official_api.csv` | 1,378 station pairs: fare, distance_km, travel_time_min, interchange — authoritative, no modeling needed |
| Metro timetable | `metro-hacks-data/01_transit/metro/timetable/gmrc_timetable_phase2_wef_2026-05-18_tidy.csv` + line-frequency CSV | first/last train, headways per line |
| AMTS stage fares | `data/amts_stage_fares_2023-07-01.csv` (this folder) | the 15-stage table, transcribed |
| BRTS area fares | GTFS `fare_products.txt` + `fare_leg_rules.txt` | area-to-area fares ₹5–50 |
| Real rides (old but real) | `metro-hacks-data/01_transit/bus_brts/station_ridership_brts_2015/ajl_brts_2015_monthly_OD_station_to_station.csv` | demand weights for sampling rides |

Libraries present: pandas, numpy, folium, matplotlib, plotly, geopandas, streamlit.
**No networkx/scipy** — and we don't need them: the router is hand-rolled (stdlib `heapq`),
because networkx has no time-dependent or multi-objective search anyway.

## Design decisions (the two things people get wrong)

### 1. The graph is NOT a distance-threshold mesh
"Connect every node within X km" produces a graph where buses teleport between stops that
share no route. We already have the real topology:

- **Ride edges**: consecutive stops in a GTFS `stop_times` sequence. Weight = actual
  schedule time. Metro edges from the tidy timetable (or OD travel_time_min).
- **Walk/transfer edges**: THIS is what the threshold is for — stop-to-stop (and
  metro↔bus) walking links. k-nearest-neighbors within radius r ∈ {300, 500, 800, 1200} m.
  Weight = distance / walking speed (4.5 km/h) + fixed transfer penalty (e.g. 5 min).

So the threshold sweep stays in the plan, but only where it means something: how far
people will walk to transfer. Below ~300 m the network fragments (no metro↔bus links);
above ~1200 m Dijkstra starts recommending 20-minute walks.

### 2. Fare is not edge-additive — it's leg-based
A 2-km AMTS hop costs ₹5. Three 2-km hops on ONE bus still cost ₹5 (stage 1, 0–3 km);
on three buses they cost ₹15. So fare depends on the whole leg (board stop → alight stop
along the route), not on edges. Consequences:

- We cannot just put `fare` on edges and run Dijkstra.
- Router state must carry `(stop, arrival_time, boarded_leg)` and fare is charged at
  alighting: AMTS = stage lookup on distance accumulated along the route stop sequence;
  BRTS = area-pair fare from GTFS fare rules; metro = OD matrix lookup.
- Optimize a Pareto front over (arrival_time, fare) with multi-objective label-setting
  (a.k.a. multicriteria Dijkstra / MARTIN-style). Every non-dominated (time, cost)
  journey is returned; the "cost vs time" trade-off IS the deliverable.

### 3. Algorithm: McRAPTOR (the transit standard), not Dijkstra and not A*
- The recognized standards for schedule-based transit routing are CSA (Connection Scan)
  and the RAPTOR family (Delling/Pajor/Werneck 2012). Production engines run these:
  OpenTripPlanner 2 = Range-RAPTOR, R5 = RAPTOR, MOTIS = RAPTOR, Navitia = CSA variant.
- We implement **McRAPTOR**: round-based label-setting where round k = journeys with
  ≤ k transfers; labels carry (arrival_time, fare) and are dominance-checked per stop
  per round; fare is charged at alighting via the 03_fares API (leg-based, not
  edge-additive). Walk/transfer edges are RAPTOR footpaths. Result per query = the
  full (time, fare) Pareto front of journeys.
- Why not a library: every engine (OTP2, R5/r5py, MOTIS, Navitia) treats fares as
  display metadata, not an optimization criterion — fare-Pareto is our research
  question. GTFS Fares v2 can't express the AMTS stage table or metro OD matrix either.
  pyraptor (PyPI, MIT) is the structural reference; optional MOTIS/OTP2 as a
  time-only oracle later.
- A* buys nothing here: ~3.3k nodes, queries are milliseconds, and its heuristic is
  weak once waiting time dominates. Skip it.
- Distance is reported, not optimized: compute route length post-hoc per journey
  (sum of leg distances, incl. walk). Nobody buys a distance-optimal ticket.
## Pipeline

```
src/
  01_stops.py     # unified stop table: GTFS stops + metro stations, dedup, coords, modes
  02_graph.py     # ride edges from stop_times + metro timetable; walk edges (radius r, param);
                  #   outputs graph pickle + connectivity sanity report (largest component, degree)
  03_fares.py     # stage lookup (AMTS), area fares (BRTS), OD matrix (metro); one fare(stop_a, stop_b, mode) API
  04_router.py    # McRAPTOR: query(origin, destination, depart_after) -> Pareto front of
                  #   journeys [(leg, mode, board_time, alight_time, fare, dist)] over (time, fare)
  05_rides.py     # sample rides: (a) uniform random stop pairs, (b) BRTS-2015-OD-weighted pairs;
                  #   sweep walk radius x departure times (peak 9h / off-peak 14h / late 22h)
  06_map.py       # folium map: stops colored by mode, edges by mode, journey overlays; out/map.html
out/              # maps, sweep tables, Pareto plots (matplotlib)
```

Steps run in order; each is a standalone script with a `main()` writing to `out/`.

## Experiment matrix

| Dimension | Values |
|---|---|
| Walk radius r | 300 / 500 / 800 / 1200 m |
| Departure time | 09:00, 14:00, 22:00 |
| Objective | min time, min fare, Pareto front |
| Ride sampling | 1,000 uniform pairs + 1,000 demand-weighted (2015 BRTS OD) |

Outputs per cell: median/mean time, fare, distance, transfers, walk; share of journeys
where cheapest ≠ fastest (the interesting number); network stats vs r (fragmentation).

## Known traps

- AMTS stage table is 2023 vintage; GTFS BRTS fares differ (₹5–50, area-based). Keep both, label vintage, don't mix silently.
- GTFS `calendar.txt` is a single service day; fine — we route on a generic weekday.
- 2015 BRTS OD is old demand; use as weights, not truth. Say so in any output.
- Metro OD matrix covers operational pairs only (53 stations).
- `hello.py` at repo root is not ours to keep; OCOJ stays self-contained.

## Run

```
uv run python src/01_stops.py   # etc., in order
```
