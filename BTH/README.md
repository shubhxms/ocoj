# BTH — tree cover around Ahmedabad Metro stations

One combined map: ESA WorldCover land cover (10 m) under every one of the 53
operational metro stations, each station coloured by how much tree/green cover
sits inside its 500 m walkshed. The point: see at a glance which stations bake
and which breathe — the stations where planting shade matters most.

The problem brief also carried the AMTS stage-fare table (01-07-2023); it is
transcribed at `data/amts_fares_stage_2023-07-01.csv` (verbatim from the pack;
fare/routing work itself lives in `../OCOJ`). This folder's deliverable is the
green-cover view.

## Open this first

`out/metro_tree_cover.html` — self-contained (map + land-cover raster embedded
as base64). Works fully offline; the OpenStreetMap basemap needs network and
simply doesn't load without it. Toggle layers top-right; click a station for
its 500 m / 1 km class shares and green rank.

## Pipeline

```
src/01_station_green.py   # per-station class shares @ 500 m & 1 km -> out/station_green_cover.csv
src/02_map.py             # land-cover PNG + folium map   -> out/metro_tree_cover.html
src/03_ranking.py         # ranked bar chart              -> out/station_green_ranking.png
```

Run in order from the repo root: `uv run --project .. python src/01_station_green.py` (etc.).

## Data (all paths from repo root)

| Asset | Path | Vintage / trust |
|---|---|---|
| Metro stations (53, lat-lon, line prefix) | `metro-hacks-data/01_transit/secondary_scraped/stops.csv` (`agency=metro`) | scraped 2026-06, secondary; cross-checkable against `01_transit/metro/fares_stations/gmrc_station_list_official_site.csv` |
| Land cover 10 m classes | `metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif` | ESA WorldCover v200 2021, institutional |
| AMTS stage fares | `data/amts_fares_stage_2023-07-01.csv` | user-supplied, unverified vintage |

## Method, in one paragraph

For each station, take a raster window, keep pixels whose centres fall within
r = 500 m and 1,000 m (straight-line, flat-earth metres at 23°N — good to
~0.1 %), and average class indicators. "green" = tree + shrub + grass +
wetland. Station colour uses the same 0–70 % RdYlGn scale on map and chart.

## What it says (2021 data, straight-line radii)

- **The city lines are tree-poor.** Median green share in the 500 m walkshed:
  ~6 % on both blue and red lines. Worst five: Rabari Colony (0.1 %),
  Amraivadi, Gheekanta, Shahpur, Vastral — all ≤1 %.
- **The Gandhinagar line breathes.** Violet-line median ~30 %; Akshardham
  (69 %), Gift City (64 %), Sachivalaya, Infocity lead.
- Policy-shaped reading: heat exposure at station level is highly skewed —
  planting budgets targeted at the bottom dozen city stations buy the most
  shade per tree, and those are exactly the dense-core stations with the most
  walkers.

## Caveats (read before quoting)

- **WorldCover is 2021**; the city has changed. It also has a known bias in
  Indian urban peripheries: lots of what a pedestrian would call scrub or
  vacant land is classed "crop". Treat "green" as an upper-ish bound, "tree"
  (class 10) as the conservative one.
- **Straight-line ≠ walking.** A 500 m circle ignores street networks, walls
  and the river; use it for comparison across stations, not for "who can walk
  here".
- Station coords are the scraped ones; a few may sit at the station entrance
  rather than centroid — immaterial at 500 m.
- `has_transfer_links` marks metro stops that co-locate with BRTS/AMTS stops
  in the scraped interchange groups; it never links two metro stops, so it is
  NOT a metro↔metro interchange flag.

## Pack repair note

`scripts/fetch_tree_cover.py` originally wrote the cropped raster with the
full WorldCover-tile transform (origin 72°E/24°N — 0.4°/0.4° off). The script
now sets `transform=window_transform`; the GeoTIFF on disk was repaired in
place (bounds 72.4–72.8 E, 22.9–23.3 N). The 100 m CSV was never affected.
