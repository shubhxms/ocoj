# OCOJ — fare↔time Pareto routing for Ahmedabad transit

**One question:** for a given bus/metro ride across Ahmedabad, what minimizes cost vs
time — and what does cheapness cost you in minutes, or speed in rupees?

OCOJ is a multi-modal journey planner (metro + BRTS + AMTS bus, 3,288 stops,
14,802 scheduled trips) that returns the **full Pareto front of journeys over
(arrival time, fare)** — with a twist: it can also route around **air quality, UV
and tree cover** when you'd rather not bake at a transfer stop.

```
$ .venv/bin/python OCOJ/src/04_router.py AMC_318 GMRC_20 --at 09:00

arrive 09:29 | 29 min | Rs 20 | 1 transfers | walk 0.21 km     ← FASTEST
  09:00->09:05  brts   AMC_318 -> AMC_7         (Rs 5)
  09:05->09:07  walk   AMC_7 -> GMRC_27         (0.21 km)
  09:10->09:29  metro  GMRC_27 -> GMRC_20       (Rs 15)

arrive 09:41 | 41 min | Rs 15 | 0 transfers | walk 0.73 km     ← CHEAPEST
  09:00->09:09  walk   AMC_318 -> GMRC_28       (0.73 km)
  09:17->09:41  metro  GMRC_28 -> GMRC_20       (Rs 15)
```

₹5 buys you 12 minutes. That trade — measured across thousands of rides — is the
project's output.

## What we found

Running real 2015 BRTS demand (demand-weighted) + uniform random rides through the
router (n = 352 rides, radius sweep on n = 100/level):

| walk radius | cheapest ≠ fastest | extra min if cheapest | fare saved | median cheapest fare |
|---|---|---|---|---|
| 300 m | 38% | 14 | ₹10 | ₹15 |
| 500 m | 52% | 10.5 | ₹5 | ₹15 |
| 800 m | **81%** | 12 | ₹5 | **₹10** |
| 1200 m | 92% | 17 | ₹5 | ₹10 |

1. **Willingness to walk is worth ₹5.** Allowing 800 m transfers halves the median
   cheapest fare (₹15 → ₹10) — you walk past fare-paying boards.
2. **Speed costs money by construction.** Ahmedabad fares are per-leg with no
   inter-operator integration; every transfer re-pays a base fare (₹5–15).
   Multi-board journeys are expensive because of the tariff, not the router.
3. **Fastest journeys are BRTS-dominated** on 2015-demand corridors; metro shows up
   in ~10% of fastest journeys but dominates the long cross-city ones.
4. **Shade-seeking is occasionally free.** With exposure-weighted walking, some
   journeys are cheaper *and* zero-walk (e.g. 17:14 · ₹20 · exp 0 vs 17:04 · ₹35).

## Try it

```
git clone https://github.com/shubhxms/ocoj && cd ocoj
uv sync
.venv/bin/python OCOJ/src/11_server.py     # → http://127.0.0.1:8787
```

Native-component brutalist UI: ⅓ sidebar (controls + Pareto table with FASTEST /
CHEAPEST tags, exposure column, expandable legs) + ⅔ map (vendored Leaflet, no CDN;
selected journey drawn leg-by-leg in mode colors, walks dashed). Toggle
**shade-seeking** to penalize walking through hot, treeless stops.

CLI without the UI:

```
.venv/bin/python OCOJ/src/04_router.py <origin_stop_id> <dest_stop_id> --at 09:00 --radius 800
```

## How it works

```
GTFS (AMTS+AJL+GTSL buses)      GMRC official data            BTH exposure layer
 646k stop_times, 978 routes     1,378-pair fare matrix        CPCB AQI · NASA UV
        │                        + station list/headways       ESA WorldCover trees
        ▼                              ▼                              ▼
 01 stops ──► 02b timetable ──► 04 McRAPTOR router ◄── 03 fares ◄── 10 exposure join
 (3,288)      (14,802 trips)     (time,fare) Pareto front      (90% stops matched)
                                        │
                    ┌───────────────────┼──────────────────┐
                    ▼                   ▼                  ▼
             05 rides (3,000      08 experiments      11 server + UI
             demand-weighted)     radius sweep        shade mode scales walk
```

- **Algorithm: McRAPTOR** (multi-criteria round-based public transit routing;
  Delling/Pajor/Werneck 2012) — the same family production planners run
  (OpenTripPlanner 2 = Range-RAPTOR). Hand-rolled in ~450 lines of stdlib Python.
  No off-the-shelf engine optimizes on fare, which is the whole point here.
- **Fares are leg-based, not edge-additive.** Three 2-km hops on one bus cost ₹5
  (stage 1); on three buses ₹15. So fare is charged at alighting per leg:
  AMTS = 2023 stage table by distance-along-route (haversine × 1.3 circuity — the
  GTFS has no shapes), BRTS = GTFS Fares-v2 area rules, metro = official OD matrix.
- **Exposure index** per stop: `0.6·AQI/300 + 0.4·shade_adjusted_UV/11`. Trees
  enter only through BTH's already shade-adjusted UV — no double counting.
  Shade mode scales footpath seconds by endpoint exposure.
- **Validation: 95 automated checks** (`OCOJ/src/07_validate.py`) — fare arithmetic
  exact vs the fare engine, Pareto validity, determinism, chronology, edge cases.

## Layout

| Path | What |
|---|---|
| `OCOJ/` | the router: pipeline `src/01…09`, data (derived, committed), UI, server, validation report |
| `BTH/` | exposure pipeline: ESA WorldCover tree share @500 m per stop, CPCB AQI, NASA POWER UV → `out/stop_exposure.csv` + offline map |
| `metro-hacks-data/` | (not in repo) ~350 MB curated public data pack with per-file provenance & trust notes — `START_HERE.md` inside describes it |

## Data sources & honesty

| Source | Used for | Vintage / trust |
|---|---|---|
| BLRTransit GTFS scrape (AMTS/AJL/GTSL) | stops, routes, schedules | secondary, 2026 feed |
| GMRC official fare API | metro fares, times, distances | official |
| GMRC line-frequency notice | metro headways/spans → synthesized timetable (dwell=0) | official |
| AMTS fare revision 01-07-2023 | bus stage fares | user-transcribed, unverified vintage |
| 2015 BRTS station OD (Janmarg study) | demand weights for ride sampling | institutional, old |
| CPCB/GPCB/IITM CAAQMS | AQI (IDW from 12 monitors) | official 2024–26 |
| NASA POWER | hourly UV | satellite-derived |
| ESA WorldCover 2021 | tree share @500 m | institutional |

Known approximations (all flagged in code docstrings, never silent): straight-line
leg geometry; ×1.3 circuity for stage distance; metro dwell = 0; GTFS minute
granularity normalized to ≥30 s segments; GTSL (Gandhinagar buses) excluded — no
fare data exists; AQI varies smoothly city-wide (12 monitors), so shade mode
reranks rather than transforms.

## Reproduce

```
uv run python OCOJ/src/01_stops.py        # unified stops (needs metro-hacks-data/)
uv run python OCOJ/src/02_edges.py        # route edges + connectivity report
uv run python OCOJ/src/02b_timetable.py   # per-trip timetable + walk edges
uv run python OCOJ/src/03_fares.py        # fare engine self-checks
uv run python OCOJ/src/07_validate.py     # 95-check router battery
uv run python OCOJ/src/08_experiments.py --radius 800 --tag r800   # checkpointed
uv run python OCOJ/src/09_results.py      # summary tables + figure
```

Derived artifacts in `OCOJ/data/` are committed, so the server/UI run without the
raw pack. Regenerating from source requires the pack (see `metro-hacks-data/`
paths inside each script).
