"""Stop-level AQI and shade-adjusted UV exposure for BTH.

AQI — official CPCB National AQI (CPCB, 2014 Expert Group methodology):
    daily pollutant statistics (PM2.5/PM10/NO2/SO2/NH3 24 h mean, CO & O3 max
    rolling 8 h mean) -> sub-index by piecewise-linear interpolation between
    the CPCB breakpoint table -> AQI = max sub-index (predominant = argmax).
    Sub-indices computed with numpy.interp (no maintained library implements
    the CPCB index; the breakpoint table IS the standard algorithm).
    Per stop: inverse-distance-weighted (power 2, k = 3 nearest) from the 12
    CAAQMS monitors, full-record mean and winter (Nov-Feb) mean.

UV exposure — ambient UVI attenuated by canopy via sky-view factor, per
    "effective UVI" formulation of the Brisbane street-tree study (Eq. 2:
    diffuse term scaled by F_s) — UVI_eff = UVI_ambient x F_s, with
    F_s = 1 - tree_share_500 m (canopy cover as the SVF proxy). Validated by
    Parisi et al.'s measured shade/sun exposure ratios 0.16-0.49 for canopy
    ~50-85 %. Ambient hourly UVI: open-meteo 2024-10..2026-10 (IST), Ahmedabad
    file south of 23.12 N, Gandhinagar file north.

Input:  metro-hacks-data/04_population_pollution/{air_quality,weather}/...
Reads   BTH/out/station_green_cover.csv (tree share per stop)
Output: BTH/out/stop_exposure.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
AQ = HERE.parent / "metro-hacks-data/04_population_pollution/air_quality"
WX = HERE.parent / "metro-hacks-data/04_population_pollution/weather"
GREEN = HERE / "out/station_green_cover.csv"
OUT = HERE / "out/stop_exposure.csv"

# CPCB 2014 breakpoints: concentration upper bounds -> index [0,50,...,500]
IDX_BOUNDS = [0, 50, 100, 200, 300, 400, 500]
BREAKPOINTS = {
    "pm25": ([0, 30, 60, 90, 120, 250, 380], "PM2.5 (µg/m³)", "24h"),
    "pm10": ([0, 50, 100, 250, 350, 430, 510], "PM10 (µg/m³)", "24h"),
    "no2": ([0, 40, 80, 180, 280, 400, 580], "NO2 (µg/m³)", "24h"),
    "so2": ([0, 40, 80, 380, 800, 1600, 2400], "SO2 (µg/m³)", "24h"),
    "nh3": ([0, 200, 400, 800, 1200, 1800, 2400], "NH3 (µg/m³)", "24h"),
    "co": ([0, 1, 2, 10, 17, 34, 51], "CO (mg/m³)", "8h"),
    "o3": ([0, 50, 100, 168, 208, 748, 1000], "Ozone (µg/m³)", "8h"),
}
AQI_BANDS = [(50, "Good"), (100, "Satisfactory"), (200, "Moderate"),
             (300, "Poor"), (400, "Very Poor"), (500, "Severe")]
WINTER_MONTHS = (11, 12, 1, 2)
MIDDAY_HOURS = range(11, 16)             # Ahmedabad solar noon ~12:40 IST
GNR_LAT = 23.12                          # north of this -> Gandhinagar UV grid


def daily_aqi(df):
    """Daily AQI series + predominant pollutant for one monitor."""
    ts = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.assign(date=ts.dt.date)
    rows = []
    for date, d in df.groupby("date"):
        subs = {}
        for key, (_, col, win) in BREAKPOINTS.items():
            if col not in d.columns:
                continue
            s = pd.to_numeric(d[col], errors="coerce")
            s = s.where(s >= 0)                       # negative sentinels -> NaN
            val = (s.rolling(8, min_periods=6).mean().max() if win == "8h"
                   else s.mean())
            subs[key] = (float(np.interp(val, BREAKPOINTS[key][0], IDX_BOUNDS))
                         if np.isfinite(val) else np.nan)
        if not subs:
            continue
        subs = pd.Series(subs).dropna()
        if subs.empty:
            continue
        rows.append({"date": date, "aqi": subs.max(),
                     "predominant": subs.idxmax()})
    return pd.DataFrame(rows)


def monitor_summaries():
    """Per-monitor mean / winter-mean CPCB AQI over the whole record."""
    sites = pd.read_csv(AQ / "cpcb_caaqms_stations_amd_gnr.csv")
    out = []
    for site_id, name, lat, lon in zip(sites["site_id"], sites["station_name"],
                                       sites["latitude"], sites["longitude"]):
        site_num = site_id.split("_")[1]
        files = sorted((AQ / "cpcb_hourly").glob(f"site_{site_num}_*.csv"))
        if not files:
            continue
        df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
        daily = daily_aqi(df)
        months = pd.to_datetime(daily["date"]).dt.month
        winter = daily.loc[months.isin(WINTER_MONTHS), "aqi"]
        out.append({
            "site": site_num, "name": name, "lat": lat, "lon": lon,
            "aqi_mean": round(daily["aqi"].mean(), 1),
            "aqi_winter": round(winter.mean(), 1) if len(winter) else np.nan,
            "days": len(daily),
            "predominant": daily["predominant"].mode().iat[0],
        })
        print(f"monitor {name[:34]:<36} aqi={out[-1]['aqi_mean']:<6} "
              f"winter={out[-1]['aqi_winter']:<6} ({len(daily)} d, "
              f"{out[-1]['predominant']})")
    return pd.DataFrame(out)


def idw(stops, monitors, k=3, power=2.0):
    """Inverse-distance-weighted monitor AQI onto stops (numpy)."""
    mlat = np.radians(monitors["lat"].to_numpy())
    mlon = np.radians(monitors["lon"].to_numpy())
    slat = np.radians(stops["lat"].to_numpy())[:, None]
    slon = np.radians(stops["lon"].to_numpy())[:, None]
    dist = (np.sqrt((mlon - slon) ** 2 * np.cos(slat) ** 2
                    + (mlat - slat) ** 2) * 6_371_000.0)
    kk = min(k, len(monitors))
    idx = np.argpartition(dist, kk - 1, axis=1)[:, :kk]
    w = 1.0 / np.power(np.take_along_axis(dist, idx, 1), power)
    for col in ("aqi_mean", "aqi_winter"):
        vals = monitors[col].to_numpy()[idx]
        stops[f"{col}_est"] = np.round((vals * w).sum(1) / w.sum(1), 1)
    stops["monitor_dist_m"] = np.take_along_axis(dist, idx, 1)[:, 0].round(0)
    stops["nearest_monitor"] = monitors["name"].to_numpy()[dist.argmin(axis=1)]
    return stops


def uv_climatology(city):
    """Daytime + midday mean ambient UVI from NASA POWER hourly (LST)."""
    df = pd.read_csv(WX / f"nasa_power_hourly_uv_{city}_2024-10-01_2026-10-08_LST.csv")
    hour = pd.to_datetime(df["time"]).dt.hour
    uvi = pd.to_numeric(df["uv_index"], errors="coerce").where(lambda s: s > 0)
    return {"day": float(uvi.mean()),
            "midday": float(uvi[hour.isin(MIDDAY_HOURS)].mean())}


def main():
    monitors = monitor_summaries()
    amb = {c: uv_climatology(c) for c in ("ahmedabad", "gandhinagar")}
    print("ambient UVI (day / midday 11-15):",
          {c: (round(v["day"], 2), round(v["midday"], 2))
           for c, v in amb.items()})

    stops = pd.read_csv(GREEN)
    stops = idw(stops, monitors)
    stops["aqi_winter_band"] = [
        next((word for hi, word in AQI_BANDS if a <= hi), "Severe")
        for a in stops["aqi_winter_est"]]

    in_gnr = stops["lat"] >= GNR_LAT
    amb_day = np.where(in_gnr, amb["gandhinagar"]["day"], amb["ahmedabad"]["day"])
    amb_mid = np.where(in_gnr, amb["gandhinagar"]["midday"],
                       amb["ahmedabad"]["midday"])
    fs = (1 - stops["tree_share_500m"]).clip(0, 1)   # canopy sky-view proxy
    stops["uv_ambient_day"] = np.round(amb_day, 2)
    stops["uv_exposure_day"] = np.round(amb_day * fs, 2)
    stops["uv_exposure_midday"] = np.round(amb_mid * fs, 2)
    stops["sky_view_factor"] = np.round(fs, 3)

    cols = ["stop_id", "name", "mode", "lat", "lon",
            "aqi_mean_est", "aqi_winter_est", "aqi_winter_band",
            "nearest_monitor", "monitor_dist_m",
            "uv_ambient_day", "uv_exposure_day", "uv_exposure_midday",
            "sky_view_factor", "tree_share_500m"]
    stops[cols].to_csv(OUT, index=False)
    print(f"\nwrote {OUT} ({len(stops)} stops)")
    print(stops.groupby("mode")[["aqi_winter_est", "uv_exposure_midday"]]
          .median().round(2).to_string())
    print("\nmost UV-exposed metro stops (midday):")
    metro = (stops[stops["mode"] == "metro"]
        .sort_values(by=["uv_exposure_midday"], ascending=False).head(5))
    print(metro[["name", "uv_exposure_midday", "aqi_winter_est"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
