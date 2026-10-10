"""OCOJ 04 — McRAPTOR router: Pareto-optimal journeys over (arrival time, fare).

Algorithm: multi-criteria round-based public transit routing (RAPTOR, Delling/Pajor/
Werneck 2012, with the McRAPTOR multi-criteria extension). Round k = journeys using
<= k trip boards (i.e. <= k-1 transfers). Labels are (arrival_sec, fare_inr) Pareto
bags per stop; fare is charged at ALIGHTING, per leg — AMTS distance-stage along the
boarded route, BRTS area pair, metro OD pair — never per edge (fares are not
edge-additive; see README). Footpaths (walk/transfer edges) relax within rounds.

Inputs (all produced upstream):
  OCOJ/data/stops_unified.csv   stop_id,name,lat,lon,agency,mode,n_routes
  OCOJ/data/timetable.parquet   trip_id,route_id,agency,is_synth,n_stops,first_dep_sec,stop_ids_str,dep_secs_str
  OCOJ/data/walk_edges.parquet  stop_a,stop_b,walk_km
  OCOJ/src/03_fares.py          amts_fare / brts_fare / metro_fare

Design choices (documented, not silent):
  - GTSL legs excluded by default: no fare data exists for Gandhinagar buses
    (0% area coverage); NOTE: include_gtsl=True is currently a no-op in practice —
    GTSL legs price at inf and FARE_CAP prunes them, so they can never appear in
    a journey. Kept for a future time-only mode (validation anomaly #1).
  - AMTS stage distance = haversine along the route's stop sequence * CIRCUITY
    (1.3) — GTFS has no shapes.txt; straight-line underestimates road distance.
  - Zero/negative inter-stop deltas (GTFS minute granularity) are normalized to a
    MIN_SEG (30 s) minimum segment at load time; the parquet keeps raw values.
  - Transfer penalty 120 s applied when boarding after alighting a trip (not after
    walking). Walking speed 4.5 km/h. Bag cap per stop bounds Pareto blowup.
"""

from __future__ import annotations

import math
import sys
from bisect import bisect_left
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OCOJ" / "src"))

WALK_KMH = 4.5
TRANSFER_PENALTY_SEC = 120
MIN_SEG_SEC = 30
CIRCUITY = 1.3
DEFAULT_ROUNDS = 4  # up to 3 transfers
BAG_CAP = 8
FARE_CAP = 120.0  # no plausible Ahmedabad journey costs more; prunes label explosion

_R_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _R_KM * math.asin(math.sqrt(a))


def hhmm_to_sec(t: str) -> int:
    h, m = t.strip().split(":")[:2]
    return int(h) * 3600 + int(m) * 60


def sec_to_hhmm(s: int) -> str:
    return f"{(s // 3600) % 24:02d}:{(s % 3600) // 60:02d}"


# ---------------------------------------------------------------- data model


@dataclass
class Pattern:
    """One RAPTOR 'route': a unique stop sequence + its trips (dep times per stop)."""

    pid: int
    route_id: str
    agency: str
    stops: list[int]  # stop indices in visit order
    trips: list[list[int]]  # per trip: dep sec at each stop, sorted by first dep
    dep_cols: list[list[int]] = field(default_factory=list)  # per-position dep times (for bisect)


@dataclass
class Label:
    arr: int  # arrival second at stop_idx
    fare: float  # cumulative fare so far (inf = unpriceable)
    stop_idx: int  # the stop this label sits at
    pat: Pattern | None = None  # pattern boarded to get here (None = walk/origin)
    trip_idx: int = -1  # index into pat.trips
    board_pos: int = -1  # position where boarded
    alight_pos: int = -1  # position of stop_idx in pat.stops
    prev: "Label | None" = None  # label this one extended
    walked_km: float = 0.0  # for walk labels: distance walked from prev

    def dominates(self, other: "Label") -> bool:
        return self.arr <= other.arr and self.fare <= other.fare


@dataclass
class Leg:
    kind: str  # "ride" | "walk"
    mode: str  # amts/brts/gtsl_bus/metro or "walk"
    route_id: str
    from_stop: str
    to_stop: str
    from_time: int
    to_time: int
    fare: float
    dist_km: float


@dataclass
class Journey:
    dep_sec: int
    arr_sec: int
    fare: float
    legs: list[Leg] = field(default_factory=list)

    @property
    def n_transfers(self) -> int:
        return max(0, sum(1 for l in self.legs if l.kind == "ride") - 1)

    @property
    def dur_min(self) -> int:
        return (self.arr_sec - self.dep_sec) // 60

    @property
    def walk_km(self) -> float:
        return round(sum(l.dist_km for l in self.legs if l.kind == "walk"), 2)


# ---------------------------------------------------------------- router


class Router:
    def __init__(
        self,
        *,
        walk_radius_m: float = 800.0,
        include_gtsl: bool = False,
        transfer_penalty_sec: int = TRANSFER_PENALTY_SEC,
        max_rounds: int = DEFAULT_ROUNDS,
        bag_cap: int = BAG_CAP,
        stops_csv: Path | None = None,
        timetable_parquet: Path | None = None,
        walk_parquet: Path | None = None,
    ) -> None:
        d = ROOT / "OCOJ/data"
        self.penalty = transfer_penalty_sec
        self.max_rounds = max_rounds
        self.bag_cap = bag_cap
        self.include_gtsl = include_gtsl
        self._fcache: dict[tuple[int, int, int], float] = {}  # (pattern, board, alight) -> fare

        stops = pd.read_csv(stops_csv or d / "stops_unified.csv", dtype={"stop_id": str}, encoding="utf-8-sig")
        self.stop_ids: list[str] = stops["stop_id"].tolist()
        self.stop_names: dict[str, str] = dict(zip(stops["stop_id"], stops["name"]))
        self.lat = stops["lat"].to_numpy()
        self.lon = stops["lon"].to_numpy()
        self.idx_of: dict[str, int] = {s: i for i, s in enumerate(self.stop_ids)}
        self._dist: dict[tuple[int, int], float] = {}  # haversine cache

        self._load_patterns(timetable_parquet or d / "timetable.parquet")
        self._load_footpaths(walk_parquet or d / "walk_edges.parquet", walk_radius_m)

        self.fares = import_module("03_fares")

    # -- loading -----------------------------------------------------------

    def km(self, i: int, j: int) -> float:
        k = (i, j)
        if k not in self._dist:
            self._dist[k] = haversine_km(self.lat[i], self.lon[i], self.lat[j], self.lon[j])
        return self._dist[k]

    def _load_patterns(self, path: Path) -> None:
        tt = pd.read_parquet(path)
        self.patterns: list[Pattern] = []
        by_seq: dict[tuple[str, str], Pattern] = {}
        rows = tt.to_dict("records")
        for row in rows:
            if row["agency"] == "GTSL" and not self.include_gtsl:
                continue
            ids: list[str] = row["stop_ids_str"].split("|")
            times = [int(x) for x in row["dep_secs_str"].split("|")]
            for p in range(1, len(times)):  # normalize minute granularity
                if times[p] < times[p - 1] + MIN_SEG_SEC:
                    times[p] = times[p - 1] + MIN_SEG_SEC
            key = (row["route_id"], row["stop_ids_str"])
            pat = by_seq.get(key)
            if pat is None:
                pat = Pattern(
                    pid=len(self.patterns),
                    route_id=row["route_id"],
                    agency=row["agency"],
                    stops=[self.idx_of[s] for s in ids],
                    trips=[],
                )
                by_seq[key] = pat
                self.patterns.append(pat)
            pat.trips.append(times)
        for pat in self.patterns:
            pat.trips.sort(key=lambda t: t[0])
            pat.dep_cols = [list(col) for col in zip(*pat.trips)] if pat.trips else []
        self.at_stop: list[list[tuple[Pattern, int]]] = [[] for _ in self.stop_ids]
        for pat in self.patterns:
            for pos, s in enumerate(pat.stops):
                self.at_stop[s].append((pat, pos))

    def _load_footpaths(self, path: Path, radius_m: float) -> None:
        we = pd.read_parquet(path)
        we = we[we["walk_km"] <= radius_m / 1000.0]
        sec_per_km = 3600.0 / WALK_KMH
        self.foot: dict[int, list[tuple[int, int]]] = {}
        pairs = list(zip(we["stop_a"], we["stop_b"], we["walk_km"]))
        for stop_a, stop_b, walk_km in pairs:
            a = self.idx_of[stop_a]
            b = self.idx_of[stop_b]
            self.foot.setdefault(a, []).append((b, int(walk_km * sec_per_km)))

    # -- fare (charged at alighting, per leg) ------------------------------

    def _leg_fare(self, pat: Pattern, board_pos: int, alight_pos: int) -> float:
        key = (pat.pid, board_pos, alight_pos)
        hit = self._fcache.get(key)
        if hit is not None:
            return hit
        if pat.agency == "AMTS":
            km = sum(self.km(pat.stops[p], pat.stops[p + 1]) for p in range(board_pos, alight_pos))
            fare = float(self.fares.amts_fare(km * CIRCUITY))
        elif pat.agency == "AJL":
            try:
                a = self.stop_ids[pat.stops[board_pos]]
                b = self.stop_ids[pat.stops[alight_pos]]
                fare = float(self.fares.brts_fare(a, b))
            except ValueError:
                fare = math.inf  # stop outside the 191-stop area system: unpriceable
        elif pat.agency == "metro":
            a = int(self.stop_ids[pat.stops[board_pos]].split("_")[1])
            b = int(self.stop_ids[pat.stops[alight_pos]].split("_")[1])
            fare = float(self.fares.metro_fare(a, b)["fare_inr"])
        else:
            fare = math.inf  # GTSL or unknown: no fare source
        self._fcache[key] = fare
        return fare

    # -- Pareto bags --------------------------------------------------------

    @staticmethod
    def _pareto_add(bag: list[Label], cand: Label) -> bool:
        for lbl in bag:
            if lbl.dominates(cand) and (lbl.arr, lbl.fare) != (cand.arr, cand.fare):
                return False
        bag[:] = [lbl for lbl in bag if not cand.dominates(lbl) or (lbl.arr, lbl.fare) == (cand.arr, cand.fare)]
        bag.append(cand)
        return True

    def _cap(self, bag: list[Label]) -> None:
        if len(bag) > self.bag_cap:
            bag.sort(key=lambda l: (l.arr, l.fare))
            del bag[self.bag_cap :]

    # -- search --------------------------------------------------------------

    def plan(self, origin: str, dest: str, dep_after: str | int) -> list[Journey]:
        if origin not in self.idx_of:
            raise KeyError(f"unknown origin stop {origin!r}")
        if dest not in self.idx_of:
            raise KeyError(f"unknown destination stop {dest!r}")
        if origin == dest:
            raise ValueError(f"origin and destination are the same stop {origin!r}")
        oi, di = self.idx_of[origin], self.idx_of[dest]
        t0 = hhmm_to_sec(dep_after) if isinstance(dep_after, str) else int(dep_after)

        bags: list[list[Label]] = [[] for _ in self.stop_ids]
        marked: set[int] = set()

        start = Label(arr=t0, fare=0.0, stop_idx=oi)
        bags[oi].append(start)
        marked.add(oi)
        self._relax_walks(bags, marked, oi, from_label=start, t0=t0)  # round 0 walk

        for _round in range(1, self.max_rounds + 1):
            improved: set[int] = set()
            for s in sorted(marked):  # route phase: board patterns at marked stops
                for pat, pos in self.at_stop[s]:
                    for lbl in list(bags[s]):
                        ready = lbl.arr + (self.penalty if lbl.pat is not None else 0)
                        trip_idx = self._earliest_trip(pat, pos, ready)
                        if trip_idx is None:
                            continue
                        times = pat.trips[trip_idx]
                        for j in range(pos + 1, len(pat.stops)):
                            arr_j = times[j]
                            fare_j = lbl.fare + self._leg_fare(pat, pos, j)
                            if fare_j > FARE_CAP:
                                continue  # absurd fare: prune (BRTS fares are non-monotone in j)
                            bag = bags[pat.stops[j]]
                            if any(b.arr <= arr_j and b.fare <= fare_j for b in bag):
                                continue  # dominated before we even allocate a Label
                            cand = Label(
                                arr=arr_j,
                                fare=fare_j,
                                stop_idx=pat.stops[j],
                                pat=pat,
                                trip_idx=trip_idx,
                                board_pos=pos,
                                alight_pos=j,
                                prev=lbl,
                            )
                            if self._pareto_add(bag, cand):
                                improved.add(pat.stops[j])
            for s in sorted(improved):  # footpath phase
                self._relax_walks(bags, improved, s, from_label=None)
            for s in improved:
                self._cap(bags[s])
            marked = improved
            if not marked:
                break

        # collect: labels at dest + one final footpath hop into dest, then
        # Pareto-filter the MERGED set (a later arrival with >= fare adds nothing)
        cands: list[Label] = list(bags[di])
        for nb, wsec in self.foot.get(di, []):
            for lbl in bags[nb]:
                cands.append(
                    Label(
                        arr=lbl.arr + wsec,
                        fare=lbl.fare,
                        stop_idx=di,
                        prev=lbl,
                        walked_km=self.km(nb, di),
                    )
                )
        cands.sort(key=lambda l: (l.arr, l.fare))
        front: list[Label] = []
        best_fare = math.inf
        for lbl in cands:
            if lbl.fare < best_fare - 1e-9:
                front.append(lbl)
                best_fare = lbl.fare
        return sorted((self._journey(t0, lbl) for lbl in front), key=lambda j: (j.arr_sec, j.fare))

    def _relax_walks(
        self,
        bags: list[list[Label]],
        improved: set[int],
        s: int,
        from_label: Label | None = None,
        t0: int = 0,
    ) -> None:
        """Add walk labels from stop s (origin seed or round-improved stop).

        Walk labels never seed further walks: RAPTOR footpaths attach to trip
        arrivals (and to the origin once, at round 0); chained walking would
        produce out-and-back noise journeys.
        """
        if from_label is not None:
            sources: list[Label] = [from_label]
        else:
            sources = [l for l in bags[s] if l.pat is not None]
        for nb, wsec in self.foot.get(s, []):
            for lbl in sources:
                arr = t0 + wsec if from_label is not None else lbl.arr + wsec
                cand = Label(
                    arr=arr,
                    fare=lbl.fare,
                    stop_idx=nb,
                    prev=lbl,
                    walked_km=self.km(s, nb),
                )
                if self._pareto_add(bags[nb], cand):
                    improved.add(nb)

    def _earliest_trip(self, pat: Pattern, pos: int, not_before: int) -> int | None:
        times = pat.dep_cols[pos]
        i = bisect_left(times, not_before)
        return i if i < len(times) else None

    def _journey(self, t0: int, lbl: Label) -> Journey:
        legs: list[Leg] = []
        node: Label | None = lbl
        while node is not None and node.prev is not None:
            prev = node.prev
            if node.pat is not None:
                times = node.pat.trips[node.trip_idx]
                stops = node.pat.stops
                bp, ap = node.board_pos, node.alight_pos
                legs.append(
                    Leg(
                        kind="ride",
                        mode={"AMTS": "amts", "AJL": "brts", "GTSL": "gtsl_bus", "metro": "metro"}[node.pat.agency],
                        route_id=node.pat.route_id,
                        from_stop=self.stop_ids[stops[bp]],
                        to_stop=self.stop_ids[stops[ap]],
                        from_time=times[bp],
                        to_time=times[ap],
                        fare=node.fare - prev.fare,
                        dist_km=sum(self.km(stops[p], stops[p + 1]) for p in range(bp, ap)),
                    )
                )
            else:
                legs.append(
                    Leg(
                        kind="walk",
                        mode="walk",
                        route_id="",
                        from_stop=self.stop_ids[prev.stop_idx],
                        to_stop=self.stop_ids[node.stop_idx],
                        from_time=prev.arr,
                        to_time=node.arr,
                        fare=0.0,
                        dist_km=node.walked_km,
                    )
                )
            node = prev
        legs.reverse()
        return Journey(dep_sec=t0, arr_sec=lbl.arr, fare=lbl.fare, legs=legs)


# ---------------------------------------------------------------- demo


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="OCOJ McRAPTOR demo query")
    ap.add_argument("origin")
    ap.add_argument("dest")
    ap.add_argument("--at", default="09:00")
    ap.add_argument("--radius", type=float, default=800.0)
    ap.add_argument("--gtsl", action="store_true")
    args = ap.parse_args()

    r = Router(walk_radius_m=args.radius, include_gtsl=args.gtsl)
    js = r.plan(args.origin, args.dest, args.at)
    print(f"{len(js)} Pareto journeys {args.origin} -> {args.dest} departing {args.at}")
    for j in js:
        print(
            f"\narrive {sec_to_hhmm(j.arr_sec)} | {j.dur_min} min | Rs {j.fare:.0f} | "
            f"{j.n_transfers} transfers | walk {j.walk_km} km"
        )
        for l in j.legs:
            t = f"{sec_to_hhmm(l.from_time)}->{sec_to_hhmm(l.to_time)}"
            if l.kind == "ride":
                print(
                    f"  {t}  {l.mode:6s} {l.route_id:18s} {l.from_stop} -> {l.to_stop}"
                    f"  (Rs {l.fare:.0f}, {l.dist_km:.1f} km)"
                )
            else:
                print(f"  {t}  walk   {'':18s} {l.from_stop} -> {l.to_stop}  ({l.dist_km:.2f} km)")


if __name__ == "__main__":
    main()
