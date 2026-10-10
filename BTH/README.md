# BTH — tree cover, AQI & UV exposure around Ahmedabad transit stops

One combined map: ESA WorldCover vegetation (10 m) under every metro, BRTS and
AMTS stop (5,108 stops inside the data window), each coloured by how much
tree/green cover sits inside its 500 m walkshed — plus, per stop, CPCB air
quality and shade-adjusted UV exposure. Only vegetation is tinted; everything
else stays transparent so the street basemap shows through. The point: see at
a glance which stops bake and which breathe, and where planting shade buys
the most health.

The problem brief also carried the AMTS stage-fare table (01-07-2023); it is
transcribed at `data/amts_fares_stage_2023-07-01.csv` (verbatim from the pack;
fare/routing work itself lives in `../OCOJ`). This folder's deliverable is the
environmental-exposure view.

## Open this first

`out/metro_tree_cover.html` — self-contained (map + vegetation raster embedded
as base64). Works fully offline; the Esri basemap needs network and simply
doesn't load without it. Stations are one layer per mode; the **"Colour stops
by" buttons (green share / AQI / UV)** restyle the same markers in place.
Click a metro station for class shares, AQI and UV; hover any stop for all
three numbers.

## Pipeline

```
src/01_station_green.py   # per-stop class shares @ 500 m & 1 km -> out/station_green_cover.csv
src/02_aqi_uv.py          # CPCB AQI + shade-adjusted UV       -> out/stop_exposure.csv
src/03_map.py             # vegetation PNG + folium map         -> out/metro_tree_cover.html
src/04_ranking.py         # ranked bar chart (metro line)       -> out/station_green_ranking.png
```

Run in order from the repo root: `uv run --project .. python src/01_station_green.py` (etc.).

## Data (all paths from repo root)

| Asset | Path | Vintage / trust |
|---|---|---|
| Stops: 53 metro + 583 BRTS + 4,472 AMTS | `metro-hacks-data/01_transit/secondary_scraped/stops.csv` | scraped 2026-06, secondary; 303 stops outside the raster window skipped |
| Land cover 10 m | `metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif` | ESA WorldCover v200 2021, institutional |
| CPCB/GPCB/IITM CAAQMS hourly (12 monitors) | `metro-hacks-data/04_population_pollution/air_quality/cpcb_hourly/` | official, 2024–2026 (~1,000 days/site) |
| Hourly ambient UVI | `.../weather/nasa_power_hourly_uv_*_LST.csv` | NASA POWER satellite-derived, 2024-10–2026-10 (open-meteo serves no historical UV here — both its endpoints returned "undefined"; the pack's openmeteo uv files are empty) |
| AMTS stage fares | `data/amts_fares_stage_2023-07-01.csv` | user-supplied, unverified vintage |

## Method — standard algorithms, no hand-rolled physics

- **Green share**: pixels whose centres fall within r (straight-line,
  flat-earth metres) of a stop, averaged. "green" = tree + shrub + grass +
  wetland — the same classes tinted on the map.
- **AQI = CPCB National AQI** (CPCB 2014 Expert Group — the standard Indian
  algorithm): daily PM2.5/PM10/NO2/SO2/NH3 24 h means, CO & O3 max rolling 8 h
  means → sub-index by piecewise-linear interpolation between the official
  breakpoint table (numpy.interp) → AQI = max sub-index. No maintained Python
  library implements the CPCB index, so the official table is encoded here.
  Stop values: inverse-distance weighting (power 2, k = 3 nearest monitors).
- **UV exposure = ambient UVI × sky-view factor**, the "effective UVI"
  formulation of the Brisbane street-tree study (Eq. 2: diffuse term scaled by
  F_s): `UVI_eff = UVI_amb × F_s`, `F_s = 1 − tree_share_500m`. Ambient hourly
  UVI from NASA POWER. Consistent with measured shade/sun exposure ratios of
  0.16–0.49 under 50–85 % canopy (Parisi et al.). Midday = 11–15 h local.

## What it says (straight-line radii, 2021 canopy, 2024–26 air)

- **The city is tree-poor where riders actually stand.** Median green share in
  the 500 m walkshed: metro 14 %, AMTS 10 %, BRTS 7 %. Worst metro walksheds:
  Rabari Colony, Amraivadi, Gheekanta, Shahpur, Vastral — all ≤1 %.
- **Exposure compounds.** Winter AQI is fairly uniform city-wide (≈ 125–150,
  "Moderate", PM10-predominant everywhere) — so *shade*, not air, is what
  differs stop to stop. The worst UV-exposed metros are the tree-poor east:
  Rabari Colony, Vastral, Amraivadi (~5.6 UVI midday vs ~1.9 at Akshardham).
- The Gandhinagar line breathes: violet-line green median ~30 % (Akshardham
  69 %, Gift City 64 %).
- Policy-shaped reading: air quality is a city problem, shade is a *site*
  problem — planting budgets targeted at the bottom dozen stops buy the most
  UV relief per tree.

## Caveats (read before quoting)

- **WorldCover is 2021** and under-detects scattered urban vegetation in
  Indian cities (spot-check: Law Garden reads 1.6 %). Independent anchors:
  ISFR 2023 puts Ahmedabad forest cover at 3.27 % of its area (lowest of the
  big metros); HUGSI ≈ 10 % tree cover — our city-scale numbers sit right
  there, so treat stop-level values as *lower bounds* on canopy.
- **UV model is canopy-only**: F_s uses tree share, ignores building shade
  (street canyons are shaded too) and wall reflection — so it ranks stops
  fairly but overestimates absolute exposure in dense cores. POWER UV is
  satellite-derived (14 % hours missing) and in local solar time.
- **AQI is interpolated from 12 monitors** (IDW) — fine for gradients, not
  for street-level values; monitors cluster in the city, so peripheral stops
  lean on distant monitors. CPCB daily AQI uses 24 h/8 h statistics per
  protocol; it is a background indicator, not a kerbside reading.
- **Straight-line ≠ walking**: use for comparison across stops, not
  accessibility.
- `has_transfer_links` marks stops co-located with another mode in the
  scraped interchange groups; it never links two metro stops.

## Pack repairs made along the way

- `scripts/fetch_tree_cover.py` wrote the cropped raster with the full
  WorldCover-tile transform; fixed and the GeoTIFF repaired in place
  (bounds 72.4–72.8 E, 22.9–23.3 N).
- The pack's `openmeteo_hourly_uv_*.csv` have an entirely empty `uv_index`
  column (open-meteo does not serve historical UV for this region);
  `scripts/fetch_uv_hourly_nasa_power.py` fetches NASA POWER hourly UVI
  instead (see weather/).
