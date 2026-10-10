"""OCOJ 07 — validation battery for the 04_router McRAPTOR router.

Hard checks (any failure -> exit 1):
  1 basic          >=1 journey for 10 diverse OD pairs (3 fixed + 7 sampled rides)
  2 integrity      leg chaining, chronology (incl. 120 s transfer penalty), finite fields
  3 fares          journey fare = sum of ride-leg fares; per-mode fares match 03_fares
  4 pareto         no returned journey dominated; output sorted by arrival
  5 determinism    identical query -> identical (arr, fare) tuples
  6 edge           KeyError unknown stops; int dep_after == str form; ValueError origin==dest
  7 rounds         max_rounds=k journeys use <= k ride legs (round = boards)

Soft checks (reported, not gating):
  8  radius        journey count weakly monotone in walk radius 300 -> 1200 (warn only)
  9  perf          30 rides (seed 1): ms/query min/median/max, queries >30 s flagged
  10 plausibility  cheapest fare vs naive single-AMTS stage fare; AMC_83->AMC_27 diagnosis
  11 timing        journey duration >= straight_km / 40 km/h

Run:  .venv/bin/python OCOJ/src/07_validate.py
Writes OCOJ/out/validation_report.txt; exit 0 iff all hard checks pass.
"""
from __future__ import annotations

import importlib.util
import math
import random
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "OCOJ" / "src"
DATA = ROOT / "OCOJ" / "data"
OUT = ROOT / "OCOJ" / "out" / "validation_report.txt"

sys.path.insert(0, str(SRC))

FIXED_PAIRS = [
    ("AMC_318", "GMRC_20", "09:00"),  # BRTS stop -> metro (cross-mode, cross-city)
    ("AMC_318", "GMRC_01", "09:00"),  # BRTS stop -> metro east end
    ("GMRC_53", "AMC_27", "09:00"),   # metro far north -> BRTS stop
]
SAMPLE_SEED = 42   # check-1 OD sampling
PERF_SEED = 1      # checks 9-11 ride sampling (spec)
N_PERF = 30
PENALTY = 120      # must mirror 04_router TRANSFER_PENALTY_SEC
SPEED_CAP_KMH = 40.0


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


r04 = load_module("r04_router", SRC / "04_router.py")
f03 = load_module("f03_fares", SRC / "03_fares.py")


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.counts: dict[str, Counter] = {}

    def _bump(self, cat: str, status: str) -> None:
        self.counts.setdefault(cat, Counter())[status] += 1

    def rec(self, cat: str, status: str, msg: str) -> None:
        self._bump(cat, status)
        self.lines.append(f"  [{status.upper():4s}] {msg}")

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def section(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(title)

    @property
    def hard_fail(self) -> int:
        hard = {"basic", "integrity", "fares", "pareto", "determinism", "edge", "rounds"}
        return sum(c["fail"] for cat, c in self.counts.items() if cat in hard)


def hhmm(t: int) -> str:
    return f"{(t // 3600) % 24:02d}:{(t % 3600) // 60:02d}"


def frontier_tuples(js) -> list[tuple[int, float]]:
    return [(j.arr_sec, round(j.fare, 6)) for j in js]


# ---------------------------------------------------------------- inputs

def sample_od_pairs(mode_of: dict[str, str]) -> list[tuple[str, str, str, float]]:
    """7 rides (seed 42), greedily covering short / cross-city / metro-metro / brts."""
    all_rides = pd.read_csv(DATA / "rides_mock.csv", dtype={"o_stop_id": str, "d_stop_id": str})
    rides = [row for row in all_rides.to_dict("records")
             if mode_of.get(row["o_stop_id"]) != "gtsl_bus" and mode_of.get(row["d_stop_id"]) != "gtsl_bus"]
    want = {"short", "cross-city", "metro-metro", "brts"}

    def tags(row) -> set[str]:
        t = set()
        km = float(row["straight_km"])
        if km < 3:
            t.add("short")
        if km > 15:
            t.add("cross-city")
        if str(row["o_stop_id"]).startswith("GMRC") and str(row["d_stop_id"]).startswith("GMRC"):
            t.add("metro-metro")
        if mode_of.get(row["o_stop_id"]) in ("brts", "multi") or mode_of.get(row["d_stop_id"]) in ("brts", "multi"):
            t.add("brts")
        return t

    rng = random.Random(SAMPLE_SEED)
    pool = list(rides)
    rng.shuffle(pool)
    picked, unmet = [], set(want)
    for row in pool:  # pass 1: category coverage
        if len(picked) >= 7:
            break
        t = tags(row)
        if t & unmet:
            picked.append(row)
            unmet -= t
    for row in pool:  # pass 2: fill to 7
        if len(picked) >= 7:
            break
        if row not in picked:
            picked.append(row)
    return [(r["o_stop_id"], r["d_stop_id"], str(r["dep_time"]), float(r["straight_km"])) for r in picked]


# ---------------------------------------------------------------- hard checks

def check_basic(rep: Report, r) -> dict[tuple[str, str, str], list]:
    rep.section("[1] basic: >=1 journey for 10 diverse OD pairs")
    rep.say("  note: rides with gtsl_bus-mode endpoints are excluded from sampling — the router")
    rep.say("  excludes GTSL by design (no fare source; see [12] anomalies for the include_gtsl defect)")
    stops = pd.read_csv(DATA / "stops_unified.csv", dtype={"stop_id": str})
    mode_of = dict(zip(stops["stop_id"], stops["mode"]))
    pairs = [(o, d, t) for o, d, t in FIXED_PAIRS] + [(o, d, t) for o, d, t, _ in sample_od_pairs(mode_of)]
    results: dict[tuple[str, str, str], list] = {}
    for o, d, t in pairs:
        try:
            js = r.plan(o, d, t)
        except Exception as exc:
            rep.rec("basic", "fail", f"{o} -> {d} @{t}: raised {type(exc).__name__}: {exc}")
            continue
        results[(o, d, t)] = js
        rep.rec("basic", "pass" if js else "fail",
                f"{o} -> {d} @{t}: {len(js)} journeys" + ("" if js else " (none)"))
    return results


def check_integrity(rep: Report, r, results) -> None:
    rep.section("[2] journey integrity (every journey from check 1)")
    n = 0
    for (o, d, t), js in results.items():
        t0 = r04.hhmm_to_sec(t)
        for k, j in enumerate(js):
            n += 1
            errs = []

            def bad(msg):
                errs.append(msg)

            prev_to = t0  # nothing may depart before the query time / prior arrival
            for i, leg in enumerate(j.legs):
                # transfer penalty binds only a RIDE boarding directly after a ride leg;
                # walk legs after alighting have zero minimum (walk labels reset pat=None)
                min_dep = prev_to + (PENALTY if i and leg.kind == "ride" and j.legs[i - 1].kind == "ride" else 0)
                if leg.from_time < min_dep:
                    bad(f"leg{i} {leg.kind} departs {hhmm(leg.from_time)} before min {hhmm(min_dep)}")
                if i and j.legs[i - 1].to_stop != leg.from_stop:
                    bad(f"leg{i} from_stop {leg.from_stop} != prev to_stop {j.legs[i-1].to_stop}")
                if leg.kind == "ride" and leg.to_time <= leg.from_time:
                    bad(f"leg{i} ride non-positive duration")
                if not (math.isfinite(leg.fare) and leg.fare >= 0):
                    bad(f"leg{i} fare not finite/non-negative: {leg.fare}")
                if not (math.isfinite(leg.dist_km) and leg.dist_km >= 0):
                    bad(f"leg{i} dist_km not finite/non-negative: {leg.dist_km}")
                prev_to = leg.to_time
            if not j.legs:
                bad("journey has no legs")
            if j.legs and j.legs[-1].to_time != j.arr_sec:
                bad(f"arr_sec {j.arr_sec} != last leg arrival {j.legs[-1].to_time}")
            if j.dep_sec != t0:
                bad(f"dep_sec {j.dep_sec} != query time {t0}")
            if not (math.isfinite(j.fare) and 0 <= j.fare <= r04.FARE_CAP):
                bad(f"journey fare out of range: {j.fare}")
            status = "fail" if errs else "pass"
            rep.rec("integrity", status, f"{o} -> {d} @{t} #{k}: " + ("ok" if not errs else "; ".join(errs[:4])))
    rep.say(f"  journeys checked: {n}")


def check_fares(rep: Report, r, results) -> None:
    rep.section("[3] fare arithmetic vs 03_fares")
    n_j = n_leg = 0
    for (o, d, t), js in results.items():
        for k, j in enumerate(js):
            n_j += 1
            errs = []
            ride_sum = sum(l.fare for l in j.legs if l.kind == "ride")
            if abs(j.fare - ride_sum) > 1e-6:
                errs.append(f"journey fare {j.fare} != sum ride legs {ride_sum}")
            for i, leg in enumerate(j.legs):
                if leg.kind != "ride":
                    if leg.fare != 0.0:
                        errs.append(f"leg{i} walk fare {leg.fare} != 0")
                    continue
                n_leg += 1
                if leg.mode == "amts":
                    exp = f03.amts_fare(leg.dist_km * r04.CIRCUITY)
                    if abs(leg.fare - exp) > 0.5:
                        errs.append(f"leg{i} amts Rs{leg.fare} != stage fare Rs{exp} for {leg.dist_km:.2f}km*{r04.CIRCUITY}")
                elif leg.mode == "brts":
                    try:
                        exp = f03.brts_fare(leg.from_stop, leg.to_stop)
                        if abs(leg.fare - exp) > 1e-6:
                            errs.append(f"leg{i} brts Rs{leg.fare} != brts_fare Rs{exp}")
                    except ValueError as exc:
                        if math.isfinite(leg.fare):
                            errs.append(f"leg{i} brts finite Rs{leg.fare} but unpriceable: {exc}")
                        else:
                            errs.append(f"leg{i} brts fare=inf survived FARE_CAP pruning")
                elif leg.mode == "metro":
                    exp = f03.metro_fare(leg.from_stop, leg.to_stop)["fare_inr"]
                    if abs(leg.fare - exp) > 1e-6:
                        errs.append(f"leg{i} metro Rs{leg.fare} != metro_fare Rs{exp}")
                else:
                    errs.append(f"leg{i} unexpected mode {leg.mode} with finite fare")
                if leg.fare > r04.FARE_CAP:
                    errs.append(f"leg{i} fare Rs{leg.fare} exceeds FARE_CAP {r04.FARE_CAP}")
            rep.rec("fares", "fail" if errs else "pass",
                    f"{o} -> {d} #{k} Rs{j.fare:.0f}: " + ("ok" if not errs else "; ".join(errs[:3])))
    rep.say(f"  journeys: {n_j}, ride legs: {n_leg}")


def check_pareto(rep: Report, results) -> None:
    rep.section("[4] Pareto validity (arr_sec, fare)")
    for (o, d, t), js in results.items():
        errs = []
        tups = frontier_tuples(js)
        if tups != sorted(tups, key=lambda x: (x[0], x[1])):
            errs.append("output not sorted by arrival")
        if len(set(tups)) != len(tups):
            errs.append(f"duplicate (arr, fare) tuples: {tups}")
        for i in range(len(js)):
            for k in range(len(js)):
                if i == k:
                    continue
                a, b = js[i], js[k]
                if (a.arr_sec <= b.arr_sec and a.fare <= b.fare) and (a.arr_sec, a.fare) != (b.arr_sec, b.fare):
                    errs.append(f"journey #{k} (arr {b.arr_sec}, Rs {b.fare}) dominated by #{i} (arr {a.arr_sec}, Rs {a.fare})")
        rep.rec("pareto", "fail" if errs else "pass", f"{o} -> {d} @{t}: {len(js)} journeys, " + ("ok" if not errs else "; ".join(errs[:3])))


def check_determinism(rep: Report, r, results) -> None:
    rep.section("[5] determinism: same query twice -> identical (arr, fare)")
    keys = list(results)[:5]
    for key in keys:
        o, d, t = key
        t1, t2 = frontier_tuples(r.plan(o, d, t)), frontier_tuples(r.plan(o, d, t))
        rep.rec("determinism", "pass" if t1 == t2 else "fail",
                f"{o} -> {d} @{t}: {'identical' if t1 == t2 else f'{t1} != {t2}'} ({len(t1)} journeys)")


def check_edge(rep: Report, r) -> None:
    rep.section("[6] edge cases")
    try:
        r.plan("AMC_99999", "AMC_318", "09:00")
        rep.rec("edge", "fail", "unknown origin did not raise")
    except KeyError:
        rep.rec("edge", "pass", "unknown origin raises KeyError")
    except Exception as exc:
        rep.rec("edge", "fail", f"unknown origin raised {type(exc).__name__} not KeyError")
    try:
        r.plan("AMC_318", "GMRC_99", "09:00")
        rep.rec("edge", "fail", "unknown dest did not raise")
    except KeyError:
        rep.rec("edge", "pass", "unknown dest raises KeyError")
    except Exception as exc:
        rep.rec("edge", "fail", f"unknown dest raised {type(exc).__name__} not KeyError")
    try:
        r.plan("AMC_318", "AMC_318", "09:00")
        rep.rec("edge", "fail", "origin == dest did not raise")
    except ValueError:
        rep.rec("edge", "pass", "origin == dest raises ValueError")
    except Exception as exc:
        rep.rec("edge", "fail", f"origin == dest raised {type(exc).__name__} not ValueError")
    try:
        t_str = frontier_tuples(r.plan("AMC_318", "GMRC_20", "09:00"))
        t_int = frontier_tuples(r.plan("AMC_318", "GMRC_20", 9 * 3600))
        rep.rec("edge", "pass" if t_str == t_int else "fail",
                f"int dep_after == str form: {'yes' if t_str == t_int else f'{t_str} != {t_int}'}")
    except Exception as exc:
        rep.rec("edge", "fail", f"int/str dep_after comparison raised {type(exc).__name__}: {exc}")


def check_rounds(rep: Report, results) -> None:
    rep.section("[7] round semantics: max_rounds=k -> <=k ride legs")
    pairs = [(o, d, t) for (o, d, t), js in results.items() if js][:4]
    for k in (1, 2):
        rk = r04.Router(max_rounds=k)
        for o, d, t in pairs:
            try:
                js = rk.plan(o, d, t)
            except Exception as exc:
                rep.rec("rounds", "fail", f"max_rounds={k} {o} -> {d}: raised {type(exc).__name__}: {exc}")
                continue
            over = [(j.n_transfers, [l.mode for l in j.legs if l.kind == "ride"]) for j in js
                    if sum(1 for l in j.legs if l.kind == "ride") > k]
            rep.rec("rounds", "fail" if over else "pass",
                    f"max_rounds={k} {o} -> {d}: {len(js)} journeys, max ride legs "
                    f"{max((sum(1 for l in j.legs if l.kind == 'ride') for j in js), default=0)}" +
                    (f" VIOLATIONS {over}" if over else ""))


# ---------------------------------------------------------------- soft checks

def check_radius(rep: Report, results) -> None:
    rep.section("[8] radius monotonicity (soft): count(r=1200) >= count(r=300)")
    pairs = [(o, d, t) for (o, d, t), js in results.items() if js][:5]
    r300 = r04.Router(walk_radius_m=300.0)
    r1200 = r04.Router(walk_radius_m=1200.0)
    for o, d, _ in pairs:
        t = "09:00"
        n300, n1200 = len(r300.plan(o, d, t)), len(r1200.plan(o, d, t))
        if n1200 >= n300:
            rep.rec("radius", "pass", f"{o} -> {d}: r300={n300} <= r1200={n1200}")
        else:
            rep.rec("radius", "warn", f"{o} -> {d}: r300={n300} > r1200={n1200} (weak violation, not gating)")


def perf_and_plausibility(rep: Report, r) -> None:
    rep.section("[9] perf (soft): 30 rides, seed 1")
    rides = pd.read_csv(DATA / "rides_mock.csv", dtype={"o_stop_id": str, "d_stop_id": str})
    sample = random.Random(PERF_SEED).sample(rides.to_dict("records"), N_PERF)

    ms, gaps, nojour, timing_bad = [], [], [], []
    rows = []
    for row in sample:
        o, d, t = row["o_stop_id"], row["d_stop_id"], str(row["dep_time"])
        t0 = time.perf_counter()
        try:
            js = r.plan(o, d, t)
        except ValueError as exc:  # origin == dest ride in the mock
            rep.say(f"  skipped {o} -> {d} @{t}: {exc}")
            continue
        dt = (time.perf_counter() - t0) * 1000.0
        ms.append(dt)
        naive = f03.amts_fare(float(row["straight_km"]) * r04.CIRCUITY)
        if js:
            cheapest = min(j.fare for j in js)
            gaps.append(cheapest - naive)
            rows.append((o, d, t, float(row["straight_km"]), naive, cheapest, cheapest - naive))
        else:
            nojour.append(f"{o} -> {d} @{t} ({row['straight_km']:.1f} km)")
        for j in js:  # [11] timing plausibility
            min_sec = float(row["straight_km"]) / SPEED_CAP_KMH * 3600.0
            if j.arr_sec - j.dep_sec < min_sec - 1.0:
                timing_bad.append(f"{o} -> {d} @{t}: {j.dur_min} min for {row['straight_km']:.1f} km straight")

    rep.say(f"  queries: {len(ms)}  min {min(ms):.0f} ms | median {statistics.median(ms):.0f} ms | max {max(ms):.0f} ms")
    rep.say(f"  queries over 30 s: {sum(1 for x in ms if x > 30000)}")
    rep.rec("perf", "pass" if max(ms) <= 30000 else "warn",
            f"max {max(ms)/1000:.1f} s/query" + ("" if max(ms) <= 30000 else " (>30 s)"))
    if nojour:
        rep.rec("perf", "warn", "no-journey rides: " + "; ".join(nojour))

    rep.section("[10] fare plausibility (soft): cheapest vs naive single-AMTS stage fare")
    rep.say("  naive = amts_fare(straight_km * 1.3); gap = cheapest router fare - naive")
    if gaps:
        qs = statistics.quantiles(gaps, n=4)
        med = statistics.median(gaps)
        rep.say(f"  gap min {min(gaps):.0f} | p25 {qs[0]:.0f} | median {med:.0f} | p75 {qs[2]:.0f} | max {max(gaps):.0f} Rs")
        rep.say(f"  rides with gap > 15 Rs: {sum(1 for g in gaps if g > 15)}/{len(gaps)}")
        worst = sorted(rows, key=lambda x: -x[6])[:5]
        rep.say("  worst cases (o -> d @t, km, naive, cheapest, gap):")
        for o, d, t, km, nv, ch, g in worst:
            rep.say(f"    {o} -> {d} @{t}  {km:.1f} km  naive Rs {nv:.0f}  cheapest Rs {ch:.0f}  gap Rs {g:.0f}")
        rep.rec("plausibility", "warn" if med > 15 else "pass",
                f"median gap Rs {med:.0f}" + (" (>15: systematically expensive)" if med > 15 else " (<=15)"))

    rep.section("[11] timing plausibility (soft): dur >= straight_km / 40 km/h")
    if timing_bad:
        rep.rec("timing", "warn", f"{len(timing_bad)} violations:")
        for s in timing_bad[:10]:
            rep.say(f"    {s}")
    else:
        rep.rec("timing", "pass", "no journey beats 40 km/h door-to-door")


def diagnose_amc83(rep: Report, r) -> None:
    rep.section("[10b] diagnosis: AMC_83 (Ghodasar) -> AMC_27 (Shivranjani) @ 09:00")
    for t in ("09:00", "16:32"):  # spec time + ride 1's mock departure
        js = r.plan("AMC_83", "AMC_27", t)
        rep.say(f"  front @{t}: " + ", ".join(f"arr {hhmm(j.arr_sec)} Rs {j.fare:.0f} ({j.n_transfers}x)" for j in js))
        cheapest = min(js, key=lambda j: (j.fare, j.arr_sec))
        rep.say(f"  cheapest @{t}: Rs {cheapest.fare:.0f}, {cheapest.dur_min} min, {cheapest.n_transfers} transfers, walk {cheapest.walk_km} km")
        if t == "09:00":
            for leg in cheapest.legs:
                if leg.kind == "ride":
                    rep.say(f"    {hhmm(leg.from_time)}-{hhmm(leg.to_time)} {leg.mode:5s} {leg.route_id} "
                            f"{leg.from_stop} -> {leg.to_stop}  Rs {leg.fare:.0f}, {leg.dist_km:.2f} km")
                else:
                    rep.say(f"    {hhmm(leg.from_time)}-{hhmm(leg.to_time)} walk  {leg.from_stop} -> {leg.to_stop}  {leg.dist_km:.2f} km")
    km = 9.54
    naive = f03.amts_fare(km * r04.CIRCUITY)
    brts = f03.brts_fare("AMC_83", "AMC_27")
    oi, di = r.idx_of["AMC_83"], r.idx_of["AMC_27"]
    direct = Counter(p.agency for p in r.patterns if oi in p.stops and di in p.stops)
    rep.say(f"  naive single-AMTS stage fare ({km} km * 1.3): Rs {naive}")
    rep.say(f"  brts_fare(AMC_83, AMC_27): Rs {brts}")
    rep.say(f"  direct patterns serving BOTH stops: {dict(direct)} (AMTS direct: {direct.get('AMTS', 0)})")
    rep.say("  conclusion: cheapest journey IS the Rs-20 direct BRTS ride (AMC_2957), equal to both the")
    rep.say("  naive AMTS stage fare and the BRTS area fare; the premise 'all journeys cost 40-50' holds")
    rep.say("  only for the fast end of the front (33-39 min). Those are expensive because fares are")
    rep.say("  charged per leg with no integration: each extra board re-pays a base stage/area fare")
    rep.say("  (e.g. Rs 50 = BRTS 5 + AMTS 25 + BRTS 5 + AMTS 15). No direct AMTS pattern exists in the")
    rep.say("  timetable for this OD, so BRTS is not crowding out a cheaper AMTS bus; fare model, not bug.")


def report_anomalies(rep: Report) -> None:
    rep.section("[12] anomalies (observed, not fixed)")
    rep.say("  - include_gtsl=True is defeated by the FARE_CAP prune: GTSL legs price at inf, and the")
    rep.say("    route phase drops every candidate with fare_j > FARE_CAP (120), so no GTSL ride journey")
    rep.say("    can ever be produced. Verified: GTSL_43 -> AMC_1258 @16:06 returns 0 journeys even with")
    rep.say("    include_gtsl=True (982 patterns admitted vs 970). The docstring's 'include_gtsl=True")
    rep.say("    admits them with fare=inf (time-only)' does not hold. Fix would need inf to bypass the")
    rep.say("    cap (semantics change -> out of scope for this battery).")
    rep.say("  - AMC_83 -> AMC_27: 'all journeys cost Rs 40-50' premise holds only for the fast end of")
    rep.say("    the front; the cheapest journey is Rs 20 direct BRTS (see [10b]).")


# ---------------------------------------------------------------- main

def main() -> int:
    t_start = time.time()
    print("loading router (default r=800) ...", flush=True)
    r = r04.Router()
    print(f"  {len(r.stop_ids)} stops, {len(r.patterns)} patterns, "
          f"{sum(len(v) for v in r.foot.values())} foot edges", flush=True)

    rep = Report()
    rep.say("OCOJ 07 — router validation report")
    rep.say(f"generated: {datetime.now().isoformat(timespec='seconds')}  wall: {time.time()-t_start:.0f}s at header")
    rep.say(f"python {sys.version.split()[0]}  pandas {pd.__version__}  router: 04_router.py")

    print("check 1-5 ...", flush=True)
    results = check_basic(rep, r)
    check_integrity(rep, r, results)
    check_fares(rep, r, results)
    check_pareto(rep, results)
    check_determinism(rep, r, results)

    print("check 6 edge ...", flush=True)
    check_edge(rep, r)

    print("check 7 rounds ...", flush=True)
    check_rounds(rep, results)

    print("check 8 radius ...", flush=True)
    check_radius(rep, results)

    print("checks 9-11 perf/plausibility/timing ...", flush=True)
    perf_and_plausibility(rep, r)
    diagnose_amc83(rep, r)
    report_anomalies(rep)

    rep.section("SUMMARY")
    hard = ["basic", "integrity", "fares", "pareto", "determinism", "edge", "rounds"]
    total = Counter()
    rep.say(f"  {'category':14s} {'pass':>5s} {'fail':>5s} {'warn':>5s}")
    for cat in hard + [c for c in rep.counts if c not in hard]:
        if cat not in rep.counts:
            continue
        c = rep.counts[cat]
        total.update(c)
        rep.say(f"  {cat:14s} {c['pass']:5d} {c['fail']:5d} {c['warn']:5d}")
    rep.say(f"  {'TOTAL':14s} {total['pass']:5d} {total['fail']:5d} {total['warn']:5d}")
    hard_fail = rep.hard_fail
    rep.say(f"  hard checks: {'ALL PASS' if hard_fail == 0 else f'{hard_fail} FAILURES'}")
    rep.say(f"  exit: {0 if hard_fail == 0 else 1}")
    rep.say(f"  wall clock: {time.time()-t_start:.0f}s")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rep.lines) + "\n", encoding="utf-8")
    print(f"\nreport -> {OUT}")
    print(f"hard checks: {'ALL PASS' if hard_fail == 0 else f'{hard_fail} FAILURES'}; "
          f"pass {total['pass']} / fail {total['fail']} / warn {total['warn']}")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    sys.exit(main())
