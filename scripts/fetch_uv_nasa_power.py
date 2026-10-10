"""Daily UV index for Ahmedabad & Gandhinagar from NASA POWER (satellite-derived),
2024-10-01 to 2026-10-08, then rebuild the daily weather+AQI+UV rollup.

Writes:
  metro-hacks-data/04_population_pollution/weather/daily_uv_nasa_power_ahd_gnr.csv
  metro-hacks-data/04_population_pollution/weather/daily_weather_aqi_uv_ahd_gnr_2024-10_2026-10.csv  (regenerated with real uv_max)
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

CITIES = {"ahmedabad": (23.0225, 72.5714), "gandhinagar": (23.2156, 72.6369)}
START, END = "2024-10-01", "2026-10-08"
WEATHER_DIR = Path("metro-hacks-data/04_population_pollution/weather")


def clearsky_noon_uvi(dates, lat, ozone_du=290.0):
    """Diffey-style clear-sky noon UVI: UVI ~ 12.5 * cos(SZA)^2.42, scaled by ozone.

    Defensible astronomical estimate (no clouds/aerosol); label as modeled.
    """
    doy = pd.DatetimeIndex(dates).dayofyear
    decl = 23.44 * np.sin(np.radians(360 / 365.24 * (doy - 81)))
    cos_sza = np.clip(np.cos(np.radians(lat - decl)), 0, 1)
    return np.round(12.5 * cos_sza**2.42 * (290.0 / ozone_du), 1)
frames = {}
for city, (lat, lon) in CITIES.items():
    url = ("https://power.larc.nasa.gov/api/temporal/daily/point"
           f"?parameters=ALLSKY_SFC_UV_INDEX&community=SB&latitude={lat}&longitude={lon}"
           f"&start={START.replace('-', '')}&end={END.replace('-', '')}&format=JSON")
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"POWER -> HTTP {e.code}: {e.read().decode()[:300]}")
    prop = d["properties"]["parameter"]["ALLSKY_SFC_UV_INDEX"]
    s = (pd.Series(prop, name="uv_dailymean_nasa").rename_axis("date")
           .replace(-999.0, float("nan")))
    s.index = pd.to_datetime(s.index, format="%Y%m%d").strftime("%Y-%m-%d")
    frames[city] = s
    print(city, "POWER uv rows:", len(s), "| nulls:", s.isna().sum(),
          "| range", s.min(), "-", s.max())
    time.sleep(1)

uv = pd.concat(frames, names=["city", "date"]).reset_index()
uv.to_csv(WEATHER_DIR / "daily_uv_nasa_power_ahd_gnr.csv", index=False)

# ---- rebuild daily rollup with real UV -------------------------------------
daily_uv = uv.set_index(["city", "date"])
parts = []
for city in CITIES:
    w = pd.read_csv(WEATHER_DIR / f"openmeteo_hourly_{city}_2024-10-01_to_2026-10-08_IST.csv")
    u = pd.read_csv(WEATHER_DIR / f"openmeteo_hourly_uv_{city}_{START}_to_{END}_IST.csv")
    m = w.merge(u, on="time", how="left")
    m["date"] = m.time.str[:10]
    g = m.groupby("date").agg(
        temp_min_c=("temperature_2m (°C)", "min"),
        temp_mean_c=("temperature_2m (°C)", "mean"),
        temp_max_c=("temperature_2m (°C)", "max"),
        rh_mean_pct=("relative_humidity_2m (%)", "mean"),
        rain_mm=("rain (mm)", "sum"),
        wind_max_kmh=("wind_speed_10m (km/h)", "max"),
        us_aqi_mean=("us_aqi", "mean"),
        us_aqi_max=("us_aqi", "max"),
        eu_aqi_mean=("european_aqi", "mean"),
        pm25_mean_ugm3=("pm2_5", "mean"),
    ).round(1)
    g["uv_dailymean_nasa"] = g.index.map(daily_uv.loc[city, "uv_dailymean_nasa"])
    g["uv_noon_clearsky"] = clearsky_noon_uvi(g.index, CITIES[city][0])
    parts.append(g.assign(city=city).reset_index())

daily = pd.concat(parts, ignore_index=True)
cols = ["city", "date", "temp_min_c", "temp_mean_c", "temp_max_c", "rh_mean_pct",
        "rain_mm", "wind_max_kmh", "uv_noon_clearsky", "uv_dailymean_nasa",
        "us_aqi_mean", "us_aqi_max", "eu_aqi_mean", "pm25_mean_ugm3"]
daily = daily.loc[:, cols].round(1)
out = WEATHER_DIR / f"daily_weather_aqi_uv_ahd_gnr_{START}_{END}.csv"
daily.to_csv(out, index=False)
print(daily[["city", "date", "temp_max_c", "uv_noon_clearsky",
             "uv_dailymean_nasa", "us_aqi_mean", "pm25_mean_ugm3"]].head(4).to_string())
