"""Combined map: vegetation cover + Ahmedabad Metro / BRTS / AMTS stops.

Renders the 10 m class raster to an RGBA PNG — transparent everywhere except
vegetated classes, so the street basemap shows through and the green that IS
there stands out — and builds a folium map:
  - vegetation overlay (toggleable)
  - metro stations, large dots coloured by green share inside 500 m, with
    detailed popups and 500 m / 1,000 m walkshed rings
  - BRTS and AMTS stops, small dots with the same colour scale, tooltip only
Reads BTH/out/station_green_cover.csv (run 01_station_green.py first).

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
STATS = HERE / "out/station_green_cover.csv"
OUT_HTML = HERE / "out/metro_tree_cover.html"
OUT_PNG = HERE / "out/landcover_amd_gnr.png"

# Vegetated classes only get a tint; built-up / crop / bare / water stay
# transparent so the basemap shows through. "Mostly no cover" reads as mostly
# untinted, and whatever is green on screen is cover.
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
POPUP_KEYS = ("name", "line", "has_transfer_links", "green_rank",
              "green_share_500m", "green_share_1000m",
              "tree_share_500m", "tree_share_1000m",
              "built_share_500m", "built_share_1000m",
              "water_share_500m", "water_share_1000m")


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


def marker_color(share, vmin=0.0, vmax=0.70):
    """RdYlGn colour for a green share; >=70 % saturates to full green."""
    t = min(max((share - vmin) / (vmax - vmin), 0.0), 1.0)
    r, g, b, _ = plt.get_cmap("RdYlGn")(t)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def metro_popup(row):
    pct = lambda x: f"{x:.1%}"  # noqa: E731
    verdict = ("tree-rich walkshed" if row["green_share_500m"] >= 0.25 else
               "moderate tree cover" if row["green_share_500m"] >= 0.10 else
               "tree-poor walkshed")
    return folium.Html("""
      <div style="font-family: sans-serif; min-width: 250px">
        <b>{name}</b> &mdash; {line}{ix}<br>
        <span style="color:#666">{verdict} &middot; green rank {rank}/53</span>
        <table style="margin-top:6px; font-size:12px" border="0">
          <tr align="right"><td></td><td><b>500 m</b></td><td><b>1 km</b></td></tr>
          <tr align="right"><td>green</td><td>{g500}</td><td>{g1000}</td></tr>
          <tr align="right"><td>tree</td><td>{t500}</td><td>{t1000}</td></tr>
          <tr align="right"><td>built-up</td><td>{b500}</td><td>{b1000}</td></tr>
          <tr align="right"><td>water</td><td>{w500}</td><td>{w1000}</td></tr>
        </table>
        <span style="color:#888; font-size:11px">ESA WorldCover 2021, straight-line radius</span>
      </div>
    """.format(name=row["name"], line=LINE_LABEL.get(row["line"], ""),
               ix=" &middot; <b>transfer links (BRTS/AMTS)</b>" if row["has_transfer_links"] else "",
               verdict=verdict, rank=int(row["green_rank"]),
               g500=pct(row["green_share_500m"]), g1000=pct(row["green_share_1000m"]),
               t500=pct(row["tree_share_500m"]), t1000=pct(row["tree_share_1000m"]),
               b500=pct(row["built_share_500m"]), b1000=pct(row["built_share_1000m"]),
               w500=pct(row["water_share_500m"]), w1000=pct(row["water_share_1000m"])))


def main():
    rgba, lat_bounds, lon_bounds = render_landcover()
    stats = pd.read_csv(STATS)

    m = folium.Map(location=(23.05, 72.58), zoom_start=12, tiles=None,
                   control_scale=True, prefer_canvas=True)
    # Esri's keyless ArcGIS canvas tiles. OSM's tile server started blocking
    # keyless file:// apps ("not following the tile usage policy"), Carto now
    # demands an API key; ArcGIS canvas is the reliable keyless option.
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
    groups = {mode: folium.FeatureGroup(name=f"{MODE_LABEL[mode]} stops "
                                        f"({(stats['mode'] == mode).sum()})")
              for mode in ("metro", "brts", "amts")}

    for row in stats.to_dict(orient="records"):
        latlon, mode, share = (row["lat"], row["lon"]), row["mode"], row["green_share_500m"]
        colour = marker_color(share)
        if mode == "metro":
            folium.Circle(latlon, radius=500, color=colour, weight=1.5,
                          fill=False, opacity=0.8).add_to(rings500)
            folium.Circle(latlon, radius=1000, color="#555555", weight=1,
                          dash_array="4,6", fill=False, opacity=0.6).add_to(rings1000)
            folium.CircleMarker(
                latlon, radius=MODE_RADIUS[mode], color="#ffffff", weight=1.5,
                fill=True, fill_color=colour, fill_opacity=0.95,
                tooltip=f"{row['name']} — green 500 m: {share:.1%}",
                popup=folium.Popup(
                    metro_popup({k: row[k] for k in POPUP_KEYS}), max_width=320),
            ).add_to(groups[mode])
        else:
            folium.CircleMarker(
                latlon, radius=MODE_RADIUS[mode],
                color="#333333" if mode == "brts" else colour, weight=0.5,
                fill=True, fill_color=colour, fill_opacity=0.9,
                tooltip=(f"{row['name']} — {MODE_LABEL[mode]}, green 500 m: "
                         f"{share:.0%}"),
            ).add_to(groups[mode])

    for grp in (rings500, rings1000, *groups.values()):
        grp.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # ---- title + legends -------------------------------------------------
    swatches = "".join(
        f'<span style="background:{hexc};margin-right:4px">&nbsp;&nbsp;&nbsp;</span>{label}&nbsp;&nbsp;'
        for hexc, label in VEG_STYLE.values())
    scale = "".join(
        f'<span style="background:{marker_color(s)}">&nbsp;&nbsp;&nbsp;</span>'
        for s in np.linspace(0.0, 0.70, 8))
    dots = "".join(
        f'<span style="display:inline-block;border-radius:50%;width:{r * 2}px;height:{r * 2}px;'
        f'background:#888;border:1px solid #fff;vertical-align:middle;margin-right:4px"></span>'
        f'{MODE_LABEL[mode]}&nbsp;&nbsp;&nbsp;'
        for mode, r in MODE_RADIUS.items())
    legend = folium.Element("""
      <div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;
                  background:rgba(255,255,255,.95);padding:8px 14px;border-radius:6px;
                  box-shadow:0 1px 5px rgba(0,0,0,.3);font-family:sans-serif">
        <b style="font-size:15px">BTH &middot; Tree cover around Ahmedabad Metro, BRTS &amp; AMTS stops</b><br>
        <span style="font-size:11px;color:#555">colour = tree+green share within 500 m &middot;
        green tint = vegetation (ESA WorldCover 2021) &middot; untinted = no vegetation
        (built-up, crop, bare, water)</span>
      </div>
      <div style="position:fixed;bottom:20px;left:10px;z-index:9999;background:rgba(255,255,255,.95);
                  padding:8px 10px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
                  font-size:11px;font-family:sans-serif;line-height:1.7">
        <b>Vegetation</b><br>{swatches}<br>
        <b style="display:block;margin-top:4px">Green share, 500 m</b>{scale}
        <span style="float:right">70&nbsp;%</span>0&nbsp;%
        <b style="display:block;margin-top:4px">Stops</b>{dots}
      </div>
    """.format(swatches=swatches, scale=scale, dots=dots))
    m.get_root().html.add_child(legend)  # type: ignore[attr-defined]  # folium untyped

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    m.save(OUT_HTML)
    print(f"wrote {OUT_HTML}  ({OUT_HTML.stat().st_size / 1e6:.1f} MB, self-contained)")


if __name__ == "__main__":
    main()
