"""Combined map: vegetation cover + AQI/UV + Ahmedabad Metro/BRTS/AMTS stops.

Geometry and metrics are kept separate: ONE marker layer per transport mode;
a colour-mode switcher (green share / CPCB AQI / UV exposure) restyles the
same markers in place. Renders the 10 m class raster to an RGBA PNG —
transparent everywhere except vegetated classes — under everything.

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

VEG_STYLE = {                              # only vegetated classes get a tint
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

# @@MAP@@ is replaced with the folium map's JS variable name at build time.
UI_TEMPLATE = """
<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;
            background:rgba(255,255,255,.95);padding:8px 14px;border-radius:6px;
            box-shadow:0 1px 5px rgba(0,0,0,.3);font-family:sans-serif">
  <b style="font-size:15px">BTH &middot; Tree cover, AQI &amp; UV —
  Ahmedabad Metro, BRTS &amp; AMTS stops</b><br>
  <span style="font-size:11px;color:#555">5,108 stops &middot; one station layer, colour switched
  by metric &middot; green tint = vegetation (WorldCover 2021)</span>
</div>
<div style="position:fixed;top:78px;left:50%;transform:translateX(-50%);z-index:9999;
            background:rgba(255,255,255,.95);padding:5px 8px;border-radius:6px;
            box-shadow:0 1px 5px rgba(0,0,0,.3);font-family:sans-serif;font-size:12px">
  <span style="color:#555;margin-right:6px">Colour stops by</span>
  <button class="bth-mode-btn active" data-mode="green" onclick="setMetricMode('green')">Green share</button>
  <button class="bth-mode-btn" data-mode="aqi" onclick="setMetricMode('aqi')">AQI (winter)</button>
  <button class="bth-mode-btn" data-mode="uv" onclick="setMetricMode('uv')">UV (midday)</button>
</div>
<style>
  .bth-mode-btn {border:1px solid #bbb;border-radius:4px;background:#fff;
    padding:2px 10px;cursor:pointer;font-size:12px}
  .bth-mode-btn.active {background:#2b2b2b;color:#fff;border-color:#2b2b2b}
</style>
<div style="position:fixed;bottom:16px;left:10px;z-index:9999;background:rgba(255,255,255,.95);
            padding:8px 10px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
            font-size:11px;font-family:sans-serif;line-height:1.7">
  <b>Vegetation</b><br>@@VEG@@<br>
  <div id="leg-green" class="bth-leg"><b style="display:block;margin-top:4px">Green share, 500 m</b>
    @@GREEN_SCALE@@<span style="float:right">70&nbsp;%</span>0&nbsp;%</div>
  <div id="leg-aqi" class="bth-leg" style="display:none"><b style="display:block;margin-top:4px">CPCB AQI (winter mean)</b>
    @@AQI_SWATCHES@@</div>
  <div id="leg-uv" class="bth-leg" style="display:none"><b style="display:block;margin-top:4px">UV exposure, midday</b>
    @@UV_SCALE@@<span style="float:right">UVI&nbsp;7</span>UVI&nbsp;1</div>
  <b style="display:block;margin-top:4px">Stops</b>@@DOTS@@
</div>
<script>
  function setMetricMode(mode) {
    @@MAP@@.eachLayer(function (layer) {
      var t = layer.options && layer.options.tags;
      if (t && t["m_" + mode]) {
        layer.setStyle({fillColor: t["m_" + mode]});
      }
    });
    document.querySelectorAll(".bth-mode-btn").forEach(function (b) {
      b.classList.toggle("active", b.dataset.mode === mode);
    });
    document.querySelectorAll(".bth-leg").forEach(function (el) {
      el.style.display = el.id === "leg-" + mode ? "block" : "none";
    });
  }
</script>
"""


def aqi_color(aqi):
    for hi, col in [(50, "#2e7d32"), (100, "#9ccc65"), (200, "#ffca28"),
                    (300, "#ef6c00"), (400, "#b71c1c")]:
        if aqi <= hi:
            return col
    return "#6a1b9a"


def scale_color(value, cmap_name, vmin, vmax):
    t = min(max((value - vmin) / (vmax - vmin), 0.0), 1.0)
    r, g, b, _ = plt.get_cmap(cmap_name)(t)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def metric_colors(row):
    """The three fill colours, carried on every marker via the `tags` option
    (folium drops arbitrary kwargs; `tags` passes through, Leaflet ignores it)."""
    return {"tags": {"m_green": scale_color(row["green_share_500m"], "RdYlGn", 0.0, 0.70),
                     "m_aqi": aqi_color(row["aqi_winter_est"]),
                     "m_uv": scale_color(row["uv_exposure_midday"], "YlOrRd", 1.0, 7.0)}}


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


def stop_tooltip(row):
    return (f"{row['name']} — {MODE_LABEL[row['mode']]} · green "
            f"{row['green_share_500m']:.0%} · AQI ~{row['aqi_winter_est']:.0f} "
            f"({row['aqi_winter_band']}) · UV midday "
            f"{row['uv_exposure_midday']:.1f}")


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
    for row in stats[stats["mode"] == "metro"].to_dict(orient="records"):
        folium.Circle((row["lat"], row["lon"]), radius=500, color="#777777",
                      weight=1.2, fill=False, opacity=0.7).add_to(rings500)
        folium.Circle((row["lat"], row["lon"]), radius=1000, color="#555555",
                      weight=1, dash_array="4,6", fill=False,
                      opacity=0.5).add_to(rings1000)

    # ---- ONE marker layer per mode; colour switched via m_* options ------
    groups = {mode: folium.FeatureGroup(
                  name=f"{MODE_LABEL[mode]} stops "
                       f"({(stats['mode'] == mode).sum()})",
                  show=mode == "metro")
              for mode in MODE_LABEL}
    for row in stats.to_dict(orient="records"):
        folium.CircleMarker(
            (row["lat"], row["lon"]), radius=MODE_RADIUS[row["mode"]],
            color="#ffffff" if row["mode"] == "metro" else "#333333",
            weight=1.5 if row["mode"] == "metro" else 0.5,
            fill=True, fill_opacity=0.95,
            tooltip=stop_tooltip(row),
            popup=(folium.Popup(metro_popup(row), max_width=330)
                   if row["mode"] == "metro" else None),
            **metric_colors(row),
        ).add_to(groups[row["mode"]])
    for grp in (rings500, rings1000, *groups.values()):
        grp.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # ---- colour-mode switcher + legends ----------------------------------
    ui_html = (UI_TEMPLATE
               .replace("@@MAP@@", m.get_name())
               .replace("@@VEG@@", "".join(
                   f'<span style="background:{h};margin-right:4px">&nbsp;&nbsp;&nbsp;</span>{lab}&nbsp;&nbsp;'
                   for h, lab in VEG_STYLE.values()))
               .replace("@@GREEN_SCALE@@", "".join(
                   f'<span style="background:{scale_color(s, "RdYlGn", 0.0, 0.70)}">&nbsp;&nbsp;&nbsp;</span>'
                   for s in np.linspace(0.0, 0.70, 8)))
               .replace("@@AQI_SWATCHES@@", "".join(
                   f'<span style="background:{c};margin-right:3px">&nbsp;&nbsp;&nbsp;</span>{lab}&nbsp;&nbsp;'
                   for lab, c in AQI_BAND_COLOR))
               .replace("@@UV_SCALE@@", "".join(
                   f'<span style="background:{scale_color(u, "YlOrRd", 1.0, 7.0)}">&nbsp;&nbsp;&nbsp;</span>'
                   for u in np.linspace(1, 7, 7)))
               .replace("@@DOTS@@", "".join(
                   f'<span style="display:inline-block;border-radius:50%;width:{r * 2}px;'
                   f'height:{r * 2}px;background:#888;border:1px solid #fff;'
                   f'vertical-align:middle;margin-right:4px"></span>'
                   f'{MODE_LABEL[mode]}&nbsp;&nbsp;&nbsp;'
                   for mode, r in MODE_RADIUS.items())))
    m.get_root().html.add_child(folium.Element(ui_html))  # type: ignore[attr-defined]

    m.save(OUT_HTML)
    print(f"wrote {OUT_HTML}  ({OUT_HTML.stat().st_size / 1e6:.1f} MB, self-contained)")


if __name__ == "__main__":
    main()
