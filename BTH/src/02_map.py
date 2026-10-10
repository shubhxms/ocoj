"""Combined map: ESA WorldCover land cover + Ahmedabad Metro stations.

Renders the 10 m class raster to a PNG (also kept as an artifact) and builds a
folium map with:
  - land-cover overlay, mercator-projected so it lines up with streets
  - every metro station, colored by tree/green share inside its 500 m circle
  - 500 m and 1,000 m straight-line walkshed rings (toggleable)
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

# class -> (hex, legend label); crop is pale straw so it never reads as green
CLS_STYLE = {
    10: ("#2d7f2d", "Tree"),
    20: ("#6aa84f", "Shrub"),
    30: ("#9fce7a", "Grass"),
    40: ("#ece6c4", "Crop / field"),
    50: ("#d9d9d9", "Built-up"),
    60: ("#d7c9a3", "Bare ground"),
    80: ("#6fa8dc", "Water"),
    90: ("#76a5af", "Wetland"),
}
LINE_LABEL = {"blue": "Blue line (E-W)", "red": "Red line (N-S)",
              "violet": "Violet line (Gandhinagar)"}
POPUP_KEYS = ("name", "line", "has_transfer_links", "green_rank",
              "green_share_500m", "green_share_1000m",
              "tree_share_500m", "tree_share_1000m",
              "built_share_500m", "built_share_1000m",
              "water_share_500m", "water_share_1000m")


def render_landcover():
    """Class raster -> RGB array + PNG artifact (palette lookup, opaque)."""
    with rasterio.open(RASTER) as src:
        cls = src.read(1)
        b = src.bounds
    pal = np.zeros((256, 3), dtype=np.uint8)
    for code, (hexc, _) in CLS_STYLE.items():
        pal[code] = tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    rgb = pal[cls]
    Image.fromarray(rgb, "RGB").save(OUT_PNG, optimize=True)
    print(f"wrote {OUT_PNG.name} ({OUT_PNG.stat().st_size / 1e6:.1f} MB)")
    return rgb, (b.bottom, b.top), (b.left, b.right)


def marker_color(share, vmin=0.0, vmax=0.70):
    """RdYlGn colour for a green share; >=70 % saturates to full green."""
    t = min(max((share - vmin) / (vmax - vmin), 0.0), 1.0)
    r, g, b, _ = plt.get_cmap("RdYlGn")(t)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def popup_html(row):
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
    """.format(name=row["name"], line=LINE_LABEL[row["line"]],
               ix=" &middot; <b>transfer links (BRTS/AMTS)</b>" if row["has_transfer_links"] else "",
               verdict=verdict, rank=int(row["green_rank"]),
               g500=pct(row["green_share_500m"]), g1000=pct(row["green_share_1000m"]),
               t500=pct(row["tree_share_500m"]), t1000=pct(row["tree_share_1000m"]),
               b500=pct(row["built_share_500m"]), b1000=pct(row["built_share_1000m"]),
               w500=pct(row["water_share_500m"]), w1000=pct(row["water_share_1000m"])))


def main():
    rgb, lat_bounds, lon_bounds = render_landcover()
    stats = pd.read_csv(STATS)[["lat", "lon", *POPUP_KEYS]]

    m = folium.Map(location=(23.05, 72.58), zoom_start=12, tiles=None,
                   control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Basemap (online)",
                     attr="&copy; OpenStreetMap contributors").add_to(m)

    ImageOverlay(image=rgb, bounds=(lat_bounds, lon_bounds), opacity=0.85,
                 mercator_project=True, show=True,
                 name="Land cover — ESA WorldCover 2021 (10 m)").add_to(m)

    rings500 = folium.FeatureGroup(name="500 m walkshed", show=True)
    rings1000 = folium.FeatureGroup(name="1,000 m walkshed", show=False)
    stations = folium.FeatureGroup(name="Metro stations (colour = green share, 500 m)")

    for row in stats.to_dict(orient="records"):
        latlon, share = (row["lat"], row["lon"]), row["green_share_500m"]
        colour = marker_color(share)
        folium.Circle(latlon, radius=500, color=colour, weight=1.5,
                      fill=False, opacity=0.8).add_to(rings500)
        folium.Circle(latlon, radius=1000, color="#555555", weight=1,
                      dash_array="4,6", fill=False, opacity=0.6).add_to(rings1000)
        folium.CircleMarker(
            latlon, radius=7, color="#ffffff", weight=1.5,
            fill=True, fill_color=colour, fill_opacity=0.95,
            tooltip=f"{row['name']} — green 500 m: {share:.1%}",
            popup=folium.Popup(popup_html(row), max_width=320),
        ).add_to(stations)

    for grp in (rings500, rings1000, stations):
        grp.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # ---- title + legends -------------------------------------------------
    swatches = "".join(
        f'<span style="background:{hexc};margin-right:4px">&nbsp;&nbsp;&nbsp;</span>{label}&nbsp;&nbsp;'
        for hexc, label in CLS_STYLE.values())
    scale = "".join(
        f'<span style="background:{marker_color(s)}">&nbsp;&nbsp;&nbsp;</span>'
        for s in np.linspace(0.0, 0.70, 8))
    legend = folium.Element("""
      <div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9999;
                  background:rgba(255,255,255,.95);padding:8px 14px;border-radius:6px;
                  box-shadow:0 1px 5px rgba(0,0,0,.3);font-family:sans-serif">
        <b style="font-size:15px">BTH &middot; Tree cover around Ahmedabad Metro stations</b><br>
        <span style="font-size:11px;color:#555">53 operational stations &middot; colour = tree+green share
        within 500 m &middot; land cover: ESA WorldCover 2021</span>
      </div>
      <div style="position:fixed;bottom:20px;left:10px;z-index:9999;background:rgba(255,255,255,.95);
                  padding:8px 10px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
                  font-size:11px;font-family:sans-serif;line-height:1.7">
        <b>Land cover</b><br>{swatches}<br>
        <b style="display:block;margin-top:4px">Green share, 500 m</b>{scale}
        <span style="float:right">70&nbsp;%</span>0&nbsp;%
      </div>
    """.format(swatches=swatches, scale=scale))
    m.get_root().html.add_child(legend)  # type: ignore[attr-defined]  # folium untyped

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    m.save(OUT_HTML)
    print(f"wrote {OUT_HTML}  ({OUT_HTML.stat().st_size / 1e6:.1f} MB, self-contained)")


if __name__ == "__main__":
    main()
