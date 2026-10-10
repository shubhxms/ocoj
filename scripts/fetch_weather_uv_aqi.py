"""Fetch UV index + composite AQI (Open-Meteo archive) for Ahmedabad & Gandhinagar,
2024-10-01 to 2026-10-08, hourly IST — same window as the pack's weather CSVs —
and derive a daily rollup joining weather + UV + AQI.

Writes:
  metro-hacks-data/04_population_pollution/weather/openmeteo_hourly_uv_ahmedabad_...csv   (+ gandhinagar)
  metro-hacks-data/04_population_pollution/air_quality/openmeteo_hourly_aqi_ahmedabad_...csv (+ gandhinagar)
  metro-hacks-data/04_population_pollution/weather/daily_weather_aqi_uv_ahd_gnr_2024-10_2026-10.csv
"""
import io
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

CITIES = {"ahmedabad": (23.0225, 72.5714), "gandhinagar": (23.2156, 72.6369)}
START, END = "2024-10-01", "2026-10-08"
WEATHER_DIR = Path("metro-hacks-data/04_population_pollution/weather")
AQ_DIR = Path("metro-hacks-data/04_population_pollution/air_quality")

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
AQAPI = "https://air-quality-api.open-meteo.com/v1/air-quality"


def get(url, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{url} -> HTTP {e.code}: {e.read().decode()[:300]}")


def fetch(url, prefix, hourly, lat, lon):
    d = get(url, dict(latitude=lat, longitude=lon, hourly=hourly,
                      start_date=START, end_date=END, timezone="Asia/Kolkata"))
    return pd.DataFrame(dict(time=d["hourly"]["time"], **{k: d["hourly"][k] for k in hourly.split(",")}))


frames_daily = {}
for city, (lat, lon) in CITIES.items():
    uv = fetch(ARCHIVE, "uv", "uv_index,uv_index_clear_sky", lat, lon)
    time.sleep(1)
    aqi = fetch(AQAPI, "aqi", "us_aqi,european_aqi,pm2_5,pm10,ozone,nitrogen_dioxide", lat, lon)
    u = uv.merge(aqi, on="time", how="outer").sort_values("time")
    u.to_csv(WEATHER_DIR / f"openmeteo_hourly_uv_{city}_{START}_to_{END}_IST.csv", index=False)
    u.to_csv(AQ_DIR / f"openmeteo_hourly_aqi_{city}_{START}_to_{END}_IST.csv", index=False)

    w = pd.read_csv(WEATHER_DIR / f"openmeteo_hourly_{city}_2024-10-01_to_2026-10-08_IST.csv")
    m = w.merge(u, on="time", how="left")
    m["date"] = m.time.str[:10]
    frames_daily[city] = m.groupby("date").agg(
        temp_min_c=("temperature_2m (°C)", "min"),
        temp_mean_c=("temperature_2m (°C)", "mean"),
        temp_max_c=("temperature_2m (°C)", "max"),
        rh_mean_pct=("relative_humidity_2m (%)", "mean"),
        rain_mm=("rain (mm)", "sum"),
        wind_max_kmh=("wind_speed_10m (km/h)", "max"),
        uv_max=("uv_index", "max"),
        us_aqi_mean=("us_aqi", "mean"),
        us_aqi_max=("us_aqi", "max"),
        eu_aqi_mean=("european_aqi", "mean"),
        pm25_mean_ugm3=("pm2_5", "mean"),
    ).round(1)
    print(city, "hourly rows:", len(u), "| daily rows:", len(frames_daily[city]))

daily = pd.concat(frames_daily, names=["city", "date"]).reset_index()
out = WEATHER_DIR / f"daily_weather_aqi_uv_ahd_gnr_{START}_{END}.csv"
daily.to_csv(out, index=False)
print("wrote", out)
print(daily.head(3).to_string())
