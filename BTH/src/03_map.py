"""Combined map: vegetation cover + AQI/UV + Ahmedabad Metro/BRTS/AMTS stops.

Renders the 10 m class raster to an RGBA PNG — transparent everywhere except
vegetated classes — and builds a folium map:
  - vegetation overlay + Esri basemap (toggleable)
  - metro stations in three toggleable colour views (green share / CPCB AQI /
    shade-adjusted UV), green view on by default; rings + detailed popups
  - BRTS and AMTS stops as small dots (green-share colours, rich tooltips)
Reads BTH/out/station_green_cover.csv and BTH/out/stop_exposure.csv.

Output: BTH/out/metro_tree_cover.html (self-contained) + landcover_amd_gnr.png.
"""
from pathlib import Path

import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from folium.raster_layers import ImageOverlay
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
RASTER = HERE.parent / "metro-hacks-data/04_population_pollution/landcover_worldcover_2021_amd_gnr.tif"
GREEN = HERE / "out/station_green_cover.csv"
EXPOSURE = HERE / "out/stop_exposure.csv"
OUT_HTML = HERE / "out/metro_tree_cover.html"
OUT_PNG = HERE / "out/landcover_amd_gnr.png"

# Vegetated classes only get a tint; built-up / crop / bare / water stay
# transparent so the basemap shows through.
VEG_STYLE = {
    10: ("#1b7a1b", "Tree"),
    20: ("#4e9a3f", "Shrub"),
    30: ("#8fca66", "Grass"),
    90: ("#3f8f8a", "Wetland"),
}
LINE_LABEL = {"blue": "Blue line (E-W)", "red": "Red line (N-S)",
              "violet": "Violet line (Gandhinagar)"}
MODE_LABEL = {"metro": "Metro", "brts": "BRTS", "amts": "AMTS"}
MODE_RADIUS = {"metro": 7, "brts": 3.5, "amts": 2.2}
AQI_BAND_COLOR = [("Good", "#2e7d32"), ("Satisfactory", "#9ccc65"),
                  ("Moderate", "#ffca28"), ("Poor", "#ef6c00"),
                  ("Very Poor", "#b71c1c"), ("Severe", "#6a1b9a")]


def aqi_color(aqi):
    """CPCB band colours for a winter-mean AQI value."""
    for hi, col in [(50, "#2e7d32"), (100, "#9ccc65"), (200, "#ffca28"),
                    (300, "#ef6c00"), (400, "#b71c1c")]:
        if aqi <= hi:
            return col
    return "#6a1b9a"


def scale_color(share, cmap_name, vmin, vmax):
    """Continuous colour from a matplotlib ramp."""
    t = min(max((share - vmin) / (vmax - vmin), 0.0), 1.0)
    r, g, b, _ = plt.get_cmap(cmap_name)(t)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def render_landcover():
    """RGBA array + PNG artifact: vegetation tinted, everything else alpha 0."""
    with rasterio.open(RASTER) as src:
        cls = src.read(1)
        b = src.bounds
    rgba = np.zeros((*cls.shape, 4), dtype=np.uint8)
    for code, (hexc, _) in VEG_STYLE.items():
        m = cls == code
        rgba[m, 0] = int(hexc[1:3], 16)
        rgba[m, 1] = int(hexc[3:5], 16)
        rgba[m, 2] = int(hexc[5:7], 16)
        rgba[m, 3] = 255
    Image.fromarray(rgba, "RGBA").save(OUT_PNG, optimize=True)
    print(f"wrote {OUT_PNG.name} ({OUT_PNG.stat().st_size / 1e6:.1f} MB)")
    return rgba, (b.bottom, b.top), (b.left, b.right)


def metro_popup(row):
    pct = lambda x: f"{x:.1%}"  # noqa: E731
    verdict = ("tree-rich walkshed" if row["green_share_500m"] >= 0.25 else
               "moderate tree cover" if row["green_share_500m"] >= 0.10 else
               "tree-poor walkshed")
    return folium.Html("""
      <div style="font-family: sans-serif; min-width: 260px">
        <b>{name}</b> &mdash; {line}{ix}<br>
        <span style="color:#666">{verdict} &middot; green rank {rank}/53</span>
        <table style="margin-top:6px; font-size:12px" border="0">
          <tr align="right"><td></td><td><b>500 m</b></td><td><b>1 km</b></td></tr>
          <tr align="right"><td>green</td><td>{g500}</td><td>{g1000}</td></tr>
          <tr align="right"><td>tree</td><td>{t500}</td><td>{t1000}</td></tr>
          <tr align="right"><td>built-up</td><td>{b500}</td><td>{b1000}</td></tr>
        </table>
        <table style="margin-top:6px; font-size:12px" border="0">
          <tr><td><b>AQI</b> (2-yr / winter)</td>
              <td align="right">{aqi_mean} / <b>{aqi_wint}</b> &middot; {band}</td></tr>
          <tr><td><b>UV midday</b> (amb &times; shade)</td>
              <td align="right">UVI {uvm} <span style="color:#888">(open {uva})</span></td></tr>
        </table>
        <span style="color:#888; font-size:11px">WorldCover 2021 &middot; CPCB 2024-26,
        nearest monitor: {mon} ({mdist:.0f} m)</span>
      </div>
    """.format(name=row["name"], line=LINE_LABEL.get(row["line"], ""),
               ix=" &middot; <b>transfer links</b>" if row["has_transfer_links"] else "",
               verdict=verdict, rank=int(row["green_rank"]),
               g500=pct(row["green_share_500m"]), g1000=pct(row["green_share_1000m"]),
               t500=pct(row["tree_share_500m"]), t1000=pct(row["tree_share_1000m"]),
               b500=pct(row["built_share_500m"]), b1000=pct(row["built_share_1000m"]),
               aqi_mean=f"{row['aqi_mean_est']:.0f}", aqi_wint=f"{row['aqi_winter_est']:.0f}",
               band=row["aqi_winter_band"],
               uvm=f"{row['uv_exposure_midday']:.1f}", uva=f"{row['uv_ambient_day']:.1f}",
               mon=row["nearest_monitor"], mdist=row["monitor_dist_m"]))


def main():
    rgba, lat_bounds, lon_bounds = render_landcover()
    green = pd.read_csv(GREEN)
    expo = pd.read_csv(EXPOSURE)
    stats = green.merge(expo.drop(columns=["name", "mode", "lat", "lon",
                                           "tree_share_500m"]),
                        on="stop_id")

    m = folium.Map(location=(23.05, 72.58), zoom_start=12, tiles=None,
                   control_scale=True, prefer_canvas=True)
    # Esri's keyless ArcGIS canvas tiles: OSM blocks keyless file:// apps,
    # Carto demands an API key.
    basemap = folium.FeatureGroup(name="Basemap (Esri light gray, online)")
    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"),
        attr="Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ",
        max_zoom=16,
    ).add_to(basemap)
    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"),
        attr="Labels &copy; Esri", max_zoom=16, transparent=True,
    ).add_to(basemap)
    basemap.add_to(m)

    ImageOverlay(image=rgba, bounds=(lat_bounds, lon_bounds), opacity=0.9,
                 mercator_project=True, show=True,
                 name="Vegetation — ESA WorldCover 2021 (10 m)").add_to(m)

    rings500 = folium.FeatureGroup(name="Metro 500 m walkshed", show=True)
    rings1000 = folium.FeatureGroup(name="Metro 1,000 m walkshed", show=False)

    # ---- metro stations: three toggleable colour views -------------------
    def add_metro_view(name, show, colour_fn, tooltip_fn):
        grp = folium.FeatureGroup(name=name, show=show)
        for row in stats[stats["mode"] == "metro"].to_dict(orient="records"):
            folium.CircleMarker(
                (row["lat"], row["lon"]), radius=MODE_RADIUS["metro"],
                color="#ffffff", weight=1.5, fill=True,
                fill_color=colour_fn(row), fill_opacity=0.95,
                tooltip=tooltip_fn(row),
                popup=folium.Popup(metro_popup(row), max_width=330),
            ).add_to(grp)
        grp.add_to(m)

    add_metro_view(
        "Metro — colour: green share 500 m (default)", True,
        lambda r: scale_color(r["green_share_500m"], "RdYlGn", 0.0, 0.70),
        lambda r: (f"{r['name']} — green 500 m: {r['green_share_500m']:.1%}, "
                   f"AQI winter ~{r['aqi_winter_est']:.0f}, "
                   f"UV midday {r['uv_exposure_midday']:.1f}"))
    add_metro_view(
        "Metro — colour: AQI (winter mean)", False,
        lambda r: aqi_color(r["aqi_winter_est"]),
        lambda r: (f"{r['name']} — AQI winter ~{r['aqi_winter_est']:.0f} "
                   f"({r['aqi_winter_band']}), nearest: {r['nearest_monitor']}"))
    add_metro_view(
        "Metro — colour: UV exposure (midday)", False,
        lambda r: scale_color(r["uv_exposure_midday"], "YlOrRd", 1.0, 7.0),
        lambda r: (f"{r['name']} — UV midday {r['uv_exposure_midday']:.1f} UVI "
                   f"(open-sky {r['uv_ambient_day']:.1f})"))

    # ---- BRTS / AMTS dots (green-share colours, rich tooltip) ------------
    mode_groups = {}
    for mode in ("brts", "amts"):
        n = (stats["mode"] == mode).sum()
        mode_groups[mode] = folium.FeatureGroup(name=f"{MODE_LABEL[mode]} stops ({n})")
    for row in stats[stats["mode"] != "metro"].to_dict(orient="records"):
        folium.CircleMarker(
            (row["lat"], row["lon"]), radius=MODE_RADIUS[row["mode"]],
            color="#333333" if row["mode"] == "brts" else scale_color(
                row["green_share_500m"], "RdYlGn", 0.0, 0.70),
            weight=0.5, fill=True,
            fill_color=scale_color(row["green_share_500m"], "RdYlGn", 0.0, 0.70),
            fill_opacity=0.9,
            tooltip=(f"{row['name']} — {MODE_LABEL[row['mode']]} · green "
                     f"{row['green_share_500m']:.0%} · AQI "
                     f"~{row['aqi_winter_est']:.0f} · UV "
                     f"{row['uv_exposure_midday']:.1f}"),
        ).add_to(mode_groups[row["mode"]])
    for grp in mode_groups.values():
        grp.add_to(m)

    for grp in (rings500, rings1000):
        grp.add_to(m)
    for row in stats[stats["mode"] == "metro"].to_dict(orient="records"):
        colour = scale_color(row["green_share_500m"], "RdYlGn", 0.0, 0.70)
        folium.Circle((row["lat"], row["lon"]), radius=500, color=colour,
                      weight=1.5, fill=False, opacity=0.8).add_to(rings500)
        folium.Circle((row["lat"], row["lon"]), radius=1000, color="#555555",
                      weight=1, dash_array="4,6", fill=False,
                      opacity=0.6).add_to(rings1000)
    folium.LayerControl(collapsed=False).add_to(m)

    # ---- title + legends -------------------------------------------------
    swatches = "".join(
        f'<span style="background:{hexc};margin-right:4px">&nbsp;&nbsp;&nbsp;</span>{label}&nbsp;&nbsp;'
        for hexc, label in VEG_STYLE.values())
    green_scale = "".join(
        f'<span style="background:{scale_color(s, "RdYlGn", 0.0, 0.70)}">&nbsp;&nbsp;&nbsp;</span>'
        for s in np.linspace(0.0, 0.70, 8))
    aqi_swatches = "".join(
        f'<span style="background:{col};margin-right:3px">&nbsp;&nbsp;&nbsp;</span>{lab}&nbsp;&nbsp;'
        for lab, col in AQI_BAND_COLOR)
    uv_scale = "".join(
        f'<span style="background:{scale_color(u, "YlOrRd", 1.0, 7.0)}">&nbsp;&nbsp;&nbsp;</span>'
        for u in np.linspace(1, 7, 7))
    dots = "".join(
        f'<span style="display:inline-block;border-radius:50%;width:{r * 2}px;height:{r * 2}px;'
        f'background:#888;border:1px solid #fff;vertical-align:middle;margin-right:4px"></span>'
        f'{MODE_LABEL[mode]}&nbsp;&nbsp;&nbsp;'
        for mode, r in MODE_RADIUS.items())
    legend = folium.Element("""
      <div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;
                  background:rgba(255,255,255,.95);padding:8px 14px;border-radius:6px;
                  box-shadow:0 1px 5px rgba(0,0,0,.3);font-family:sans-serif">
        <b style="font-size:15px">BTH &middot; Tree cover, AQI &amp; UV exposure —
        Ahmedabad Metro, BRTS &amp; AMTS stops</b><br>
        <span style="font-size:11px;color:#555">switch the metro colour view in the layer
        control &middot; green tint = vegetation (WorldCover 2021) &middot; untinted = no cover</span>
      </div>
      <div style="position:fixed;bottom:16px;left:10px;z-index:9999;background:rgba(255,255,255,.95);
                  padding:8px 10px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
                  font-size:11px;font-family:sans-serif;line-height:1.7">
        <b>Vegetation</b><br>{swatches}<br>
        <b style="display:block;margin-top:4px">Green share, 500 m</b>{green_scale}
        <span style="float:right">70&nbsp;%</span>0&nbsp;%
        <b style="display:block;margin-top:4px">CPCB AQI (winter mean)</b>{aqi_swatches}
        <b style="display:block;margin-top:4px">UV exposure, midday</b>{uv_scale}
        <span style="float:right">UVI&nbsp;7</span>UVI&nbsp;1
        <b style="display:block;margin-top:4px">Stops</b>{dots}
      </div>
    """.format(swatches=swatches, green_scale=green_scale,
               aqi_swatches=aqi_swatches, uv_scale=uv_scale, dots=dots))
    m.get_root().html.add_child(legend)  # type: ignore[attr-defined]  # folium untyped

    m.save(OUT_HTML)
    print(f"wrote {OUT_HTML}  ({OUT_HTML.stat().st_size / 1e6:.1f} MB, self-contained)")


if __name__ == "__main__":
    main()
