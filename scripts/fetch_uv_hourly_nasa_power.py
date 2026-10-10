"""Hourly UV index for Ahmedabad & Gandhinagar from NASA POWER (satellite-derived).

Open-Meteo does not serve historical uv_index for this region (both the archive
and air-quality endpoints returned "undefined" — the columns in the pack's
openmeteo_hourly_uv_*.csv are empty), so this fetches ALLSKY_SFC_UV_INDEX at
hourly resolution for the same window from NASA POWER instead.

Writes:
  metro-hacks-data/04_population_pollution/weather/nasa_power_hourly_uv_{city}_2024-10-01_2026-10-08_LST.csv
(time in local solar time, the finest clock POWER offers; Ahmedabad is IST+18 min)
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

CITIES = {"ahmedabad": (23.0225, 72.5714), "gandhinagar": (23.2156, 72.6369)}
START, END = "20241001", "20261008"
WEATHER_DIR = Path("metro-hacks-data/04_population_pollution/weather")

for city, (lat, lon) in CITIES.items():
    url = ("https://power.larc.nasa.gov/api/temporal/hourly/point"
           f"?parameters=ALLSKY_SFC_UV_INDEX&community=SB&latitude={lat}"
           f"&longitude={lon}&start={START}&end={END}"
           "&time-standard=LST&format=JSON")
    try:
        with urllib.request.urlopen(url, timeout=600) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"POWER -> HTTP {e.code}: {e.read().decode()[:300]}")
    prop = d["properties"]["parameter"]["ALLSKY_SFC_UV_INDEX"]
    s = (pd.Series(prop, name="uv_index").rename_axis("time")
           .replace(-999.0, float("nan")).round(2))
    s.index = pd.to_datetime(s.index, format="%Y%m%d%H").strftime("%Y-%m-%d %H:00")
    out = WEATHER_DIR / f"nasa_power_hourly_uv_{city}_2024-10-01_2026-10-08_LST.csv"
    s.to_csv(out, index_label="time")
    print(f"{city}: {len(s)} rows, nulls {s.isna().sum()}, "
          f"range {s.min()}-{s.max()} -> {out.name}")
    time.sleep(2)
