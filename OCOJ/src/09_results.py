"""OCOJ 09 — aggregate experiment results and draw the summary figure.

Outputs:
  OCOJ/out/results_summary.csv   headline + per-radius tables
  OCOJ/out/results.png           fast-vs-cheap scatter + radius comparison
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OUT = Path(__file__).resolve().parents[2] / "OCOJ" / "out"


def load(tag: str) -> pd.DataFrame:
    p = OUT / f"results_{tag}.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def stats(df: pd.DataFrame) -> dict:
    done = df[df["n_journeys"] > 0]
    tr = done[done["split"] == "tradeoff"]
    return {
        "n": len(df),
        "unreachable": int((df["n_journeys"] == 0).sum()),
        "share_tradeoff": round(len(tr) / len(done), 3) if len(done) else None,
        "median_fast_min": done["fast_min"].median(),
        "median_fast_fare": done["fast_fare"].median(),
        "median_cheap_fare": done["cheap_fare"].median(),
        "median_cheap_min": done["cheap_min"].median(),
        "median_extra_min": tr["extra_min_if_cheapest"].median(),
        "median_fare_saved": tr["fare_saved_if_cheapest"].median(),
    }


def main() -> None:
    r800 = load("r800")
    rows = [("r800 (full sample)", stats(r800))]
    # fair radius comparison on the rides present in EVERY radius file
    tags = {f"r{r}": load(f"r{r}") for r in (300, 500, 800, 1200)}
    common: set | None = None
    for df in tags.values():
        ids = set(df["ride_id"])
        common = ids if common is None else (common & ids)
    first100 = {k: df[df["ride_id"].isin(common or set())] for k, df in tags.items()}
    for tag, df in first100.items():
        if len(df):
            rows.append((f"{tag} (common n={len(df)})", stats(df)))
            rows.append((f"{tag} (first-100)", stats(df)))
    summary = pd.DataFrame([{"cell": k, **v} for k, v in rows])
    summary.to_csv(OUT / "results_summary.csv", index=False)
    print(summary.to_string(index=False))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    d = r800[r800.n_journeys > 0]
    ax1.scatter(d.fast_min, d.fast_fare, s=14, alpha=0.55, label="fastest journey")
    ax1.scatter(d.cheap_min, d.cheap_fare, s=14, alpha=0.55, label="cheapest journey")
    ax1.set_xlabel("journey duration (min)")
    ax1.set_ylabel("fare (Rs)")
    ax1.set_title(f"Fastest vs cheapest per ride (r800, n={len(d)})")
    ax1.legend()

    rad = {k: v for k, v in first100.items() if len(v)}
    xs = sorted(int(k[1:]) for k in rad)
    trade = []
    unreach = []
    for x in xs:
        v = first100[f"r{x}"]
        done = v[v["n_journeys"] > 0]
        trade.append(float((done["split"] == "tradeoff").mean()) if len(done) else 0)
        unreach.append(float((v["n_journeys"] == 0).mean()))
    ax2.plot(xs, trade, "o-", label="share cheapest != fastest")
    ax2.plot(xs, unreach, "s--", label="share unreachable")
    ax2.set_xlabel("walk radius (m)")
    ax2.set_ylabel("share of rides")
    ax2.set_title("Radius sweep (first-100 rides)")
    ax2.legend()

    fig.tight_layout()
    fig.savefig(OUT / "results.png", dpi=140)
    print(f"\nwrote {OUT / 'results.png'}")


if __name__ == "__main__":
    main()
