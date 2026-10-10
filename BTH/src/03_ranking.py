"""Ranked bar chart: green share within 500 m of every metro station.

One horizontal bar per station, same RdYlGn scale as the map, grouped by line.
Output: BTH/out/station_green_ranking.png
"""
from pathlib import Path

from matplotlib.patches import Polygon
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
STATS = HERE / "out/station_green_cover.csv"
OUT = HERE / "out/station_green_ranking.png"

LINE_COLOR = {"blue": "#1f77b4", "red": "#d62728", "violet": "#7f4fc9"}


def main():
    stats = pd.read_csv(STATS).sort_values("green_share_500m")
    shares = stats["green_share_500m"]

    fig, ax = plt.subplots(figsize=(8, 12), dpi=150)
    ax.barh(stats["name"], shares * 100, height=0.72,
            color=[LINE_COLOR[l] for l in stats["line"]], alpha=0.85)
    ax.axvline(shares.median() * 100, color="#333333", lw=1, ls="--",
               label=f"median {shares.median():.0%}")

    ax.set_xlabel("Green share within 500 m (ESA WorldCover 2021, straight-line)")
    ax.set_title("Tree cover in the 500 m walkshed of each Ahmedabad metro station",
                 fontsize=12, pad=12)
    ax.set_xlim(0, shares.max() * 100 * 1.08)
    ax.grid(axis="x", alpha=0.3)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    handles = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)], closed=True,
                       color=c, alpha=0.85) for c in LINE_COLOR.values()]
    ax.legend(handles + [ax.lines[0]],
              [*[f"{l.capitalize()} line" for l in LINE_COLOR], ax.lines[0].get_label()],
              loc="lower right", fontsize=9, framealpha=0.9)

    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
