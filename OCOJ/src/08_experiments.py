"""OCOJ 08 — experiments: push mock rides through the router, measure the trade-off.

For every ride: the full Pareto front is computed; the FASTEST (min arrival) and
CHEAPEST (min fare) journeys are compared. The headline metric is how often the
cheapest journey is not the fastest, and what the trade costs in minutes / rupees.

Checkpointed: results append to OCOJ/out/results_<tag>.csv after every ride and
already-processed ride_ids are skipped on rerun — runs are resumable and killable.

Usage:
  .venv/bin/python OCOJ/src/08_experiments.py --radius 800 --tag r800 [--limit N]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "OCOJ" / "src"
OUT = ROOT / "OCOJ" / "out"
RIDES = ROOT / "OCOJ" / "data" / "rides_mock.csv"

_spec = importlib.util.spec_from_file_location("ocoj_router", SRC / "04_router.py")
assert _spec is not None, "cannot locate 04_router.py"
router_mod = importlib.util.module_from_spec(_spec)
sys.modules["ocoj_router"] = router_mod
_spec.loader.exec_module(router_mod)

COLS = [
    "ride_id", "source", "peak_label", "straight_km", "dep_time", "radius",
    "n_journeys", "fast_min", "fast_fare", "fast_transfers", "fast_walk_km", "fast_modes",
    "cheap_fare", "cheap_min", "cheap_transfers", "cheap_walk_km", "cheap_modes",
    "split", "extra_min_if_cheapest", "fare_saved_if_cheapest",
]


def modes_of(j) -> str:
    return "+".join(sorted({leg.mode for leg in j.legs if leg.kind == "ride"})) or "walk-only"


def row_for(ride: dict, journeys, radius) -> dict:
    if not journeys:
        return {
            "ride_id": ride["ride_id"], "source": ride["source"], "peak_label": ride["peak_label"],
            "straight_km": ride["straight_km"], "dep_time": ride["dep_time"], "radius": radius,
            "n_journeys": 0,
        }
    fast = min(journeys, key=lambda j: (j.arr_sec, j.fare))
    cheap = min(journeys, key=lambda j: (j.fare, j.arr_sec))
    same = fast is cheap
    return {
        "ride_id": ride["ride_id"], "source": ride["source"], "peak_label": ride["peak_label"],
        "straight_km": ride["straight_km"], "dep_time": ride["dep_time"], "radius": radius,
        "n_journeys": len(journeys),
        "fast_min": fast.dur_min, "fast_fare": fast.fare,
        "fast_transfers": fast.n_transfers, "fast_walk_km": fast.walk_km,
        "fast_modes": modes_of(fast),
        "cheap_fare": cheap.fare, "cheap_min": cheap.dur_min,
        "cheap_transfers": cheap.n_transfers, "cheap_walk_km": cheap.walk_km,
        "cheap_modes": modes_of(cheap),
        "split": "same" if same else "tradeoff",
        "extra_min_if_cheapest": 0 if same else cheap.dur_min - fast.dur_min,
        "fare_saved_if_cheapest": 0.0 if same else fast.fare - cheap.fare,
    }


def summarize(df: pd.DataFrame, radius: float) -> pd.DataFrame:
    done = df[df.n_journeys > 0]
    rows = []
    for name, g in [("ALL", done), *[(f"src={k}", v) for k, v in done.groupby("source")],
                    *[(f"peak={k}", v) for k, v in done.groupby("peak_label")]]:
        n = len(g)
        tr = g[g.split == "tradeoff"]
        rows.append({
            "group": name, "radius_m": radius, "n": n,
            "unreachable": int((df.n_journeys == 0).sum()) if name == "ALL" else 0,
            "share_tradeoff": round(len(tr) / n, 3) if n else None,
            "median_fast_min": g.fast_min.median() if n else None,
            "median_cheap_fare": g.cheap_fare.median() if n else None,
            "median_extra_min": tr.extra_min_if_cheapest.median() if len(tr) else 0,
            "median_fare_saved": tr.fare_saved_if_cheapest.median() if len(tr) else 0,
            "metro_in_fast_share": round((g.fast_modes.str.contains("metro")).mean(), 3) if n else None,
            "brts_in_fast_share": round((g.fast_modes.str.contains("brts")).mean(), 3) if n else None,
            "median_fast_transfers": g.fast_transfers.median() if n else None,
        })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=float, default=800.0)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--limit", type=int, default=None, help="first N rides (deterministic)")
    ap.add_argument("--rides", type=Path, default=RIDES)
    args = ap.parse_args()
    tag = args.tag or f"r{int(args.radius)}"
    res_path = OUT / f"results_{tag}.csv"
    OUT.mkdir(exist_ok=True)

    rides = pd.read_csv(args.rides, dtype={"o_stop_id": str, "d_stop_id": str})
    if args.limit:
        rides = rides.head(args.limit)

    done_ids: set[int] = set()
    if res_path.exists():
        done_ids = set(pd.read_csv(res_path)["ride_id"])
        print(f"resuming: {len(done_ids)} rides already done")


    r = router_mod.Router(walk_radius_m=args.radius)
    rides = rides[~rides.ride_id.isin(done_ids)]
    print(f"{len(rides)} rides to run at radius {args.radius:.0f} m -> {res_path}")
    t_start = time.time()
    try:
        f = res_path.open("a", newline="")
    except OSError as e:
        raise SystemExit(f"cannot write {res_path}: {e}") from e
    with f:
        for i, ride in enumerate(rides.to_dict("records"), 1):
            t0 = time.time()
            try:
                js = r.plan(ride["o_stop_id"], ride["d_stop_id"], ride["dep_time"])
            except (KeyError, ValueError) as e:  # unreachable / bad stop: record as 0
                print(f"  ride {ride['ride_id']}: {e}", flush=True)
                js = []
            pd.DataFrame([row_for(ride, js, args.radius)])[COLS].to_csv(f, header=not f.tell(), index=False)
            f.flush()
            if i % 25 == 0:
                el = time.time() - t_start
                print(f"  {i}/{len(rides)} rides | last {time.time()-t0:.1f}s | elapsed {el/60:.0f}m | eta {el/i*(len(rides)-i)/60:.0f}m", flush=True)

    df = pd.read_csv(res_path)
    summ = summarize(df, args.radius)
    summ.to_csv(OUT / f"summary_{tag}.csv", index=False)
    print(f"\n=== summary (radius {args.radius:.0f} m, tag {tag}) ===")
    print(summ.to_string(index=False))


if __name__ == "__main__":
    main()
