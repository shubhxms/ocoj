"""OCOJ fare engine: unify the three fare systems of the router.

  AMTS   distance stage table  OCOJ/data/amts_fares_stage_2023-07-01.csv
  BRTS   GTFS Fares v2 areas   metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted/
                               (areas.txt, stop_areas.txt, fare_leg_rules.txt,
                               fare_products.txt; ADULT products only)
  METRO  official GMRC OD      metro-hacks-data/01_transit/metro/fares_stations/
                               gmrc_fare_od_matrix_official_api.csv

Public API (all return whole INR rupees unless noted; all fail loudly):
  amts_fare(dist_km) -> int
  brts_fare(stop_id_a, stop_id_b) -> int          # GTFS stop ids, e.g. AMC_318
  metro_fare(a, b) -> dict                        # id / "GMRC_05" / station name
  fare(mode, a, b, dist_km=None) -> int           # dispatcher

Run as __main__ for the self-check battery -> OCOJ/out/fare_checks.csv.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMTS_STAGES_CSV = ROOT / "OCOJ/data/amts_fares_stage_2023-07-01.csv"
STOPS_UNIFIED_CSV = ROOT / "OCOJ/data/stops_unified.csv"
GTFS_DIR = ROOT / "metro-hacks-data/01_transit/gtfs_secondary/gtfs_extracted"
METRO_OD_CSV = ROOT / "metro-hacks-data/01_transit/metro/fares_stations/gmrc_fare_od_matrix_official_api.csv"
CHECKS_CSV = ROOT / "OCOJ/out/fare_checks.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a GTFS-style CSV, stripping the BOM. Raises if the file is missing."""
    if not path.is_file():
        raise FileNotFoundError(f"fare data file missing: {path}")
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError as exc:
        raise RuntimeError(f"cannot read fare data file {path}: {exc}") from exc


# --------------------------------------------------------------------- AMTS --

@cache
def _amts_stages() -> list[tuple[float, float, int]]:
    """[(distance_min_km, distance_max_km, fare_inr)] sorted by min distance."""
    rows = _read_csv(AMTS_STAGES_CSV)
    return sorted((float(r["distance_min_km"]), float(r["distance_max_km"]), int(r["fare_inr"])) for r in rows)


def amts_fare(dist_km: float) -> int:
    """AMTS stage fare (2023-07-01 table) for a route leg of `dist_km` km.

    Stage lookup is lower-inclusive: stage k covers [min, max); the LAST stage
    is max-inclusive ([41, 44]). Ranges are contiguous, so every 0 < d <= 44
    falls in exactly one stage.

    Deliberate choices outside the published table (revisit if data changes):
      dist_km <= 0  -> 5   minimum stage fare. Zero/negative distance is caller
                            data, floored to the minimum fare rather than
                            crashed on (per engine spec).
      dist_km > 44  -> 30  flat top fare. The table stops at 44 km and AMTS
                            publishes no rule beyond it; we extend the top
                            stage flat instead of raising.
    """
    if dist_km <= 0:
        return 5
    stages = _amts_stages()
    if dist_km > stages[-1][1]:
        return stages[-1][2]
    for lo, hi, fare_inr in stages:
        if lo <= dist_km < hi:
            return fare_inr
    # reachable only at the top edge (dist_km == 44): last stage max-inclusive
    return stages[-1][2]


# --------------------------------------------------------------------- BRTS --

@cache
def _brts_stop_area() -> dict[str, str]:
    """stop_id -> area_id (191 stops, one area each in this feed)."""
    return {r["stop_id"]: r["area_id"] for r in _read_csv(GTFS_DIR / "stop_areas.txt")}


@cache
def _brts_rules() -> dict[tuple[str, str], int]:
    """(from_area_id, to_area_id) -> adult amount INR."""
    amounts = {
        r["fare_product_id"]: int(r["amount"])
        for r in _read_csv(GTFS_DIR / "fare_products.txt")
        if r["rider_category_id"] == "BRTS_ADULT"
    }
    return {
        (r["from_area_id"], r["to_area_id"]): amounts[r["fare_product_id"]]
        for r in _read_csv(GTFS_DIR / "fare_leg_rules.txt")
        if r["fare_product_id"] in amounts
    }


def brts_fare(stop_id_a: str, stop_id_b: str) -> int:
    """BRTS (AJL) adult area fare between two GTFS stop ids, e.g. AMC_318.

    Stops are mapped to fare areas via stop_areas.txt; the rule table is tried
    as (A, B) then (B, A) so either travel direction works. Raises ValueError
    if a stop belongs to no fare area, or if no adult rule exists for the area
    pair (including same-area pairs — the feed has none).
    """
    stop_area = _brts_stop_area()
    for stop_id in (stop_id_a, stop_id_b):
        if stop_id not in stop_area:
            raise ValueError(
                f"BRTS stop {stop_id!r} belongs to no fare area "
                f"({len(stop_area)} of the GTFS stops are in areas)"
            )
    area_a, area_b = stop_area[stop_id_a], stop_area[stop_id_b]
    rules = _brts_rules()
    amount = rules.get((area_a, area_b))
    if amount is None:
        amount = rules.get((area_b, area_a))
    if amount is None:
        raise ValueError(
            f"no adult fare rule for BRTS areas {area_a} <-> {area_b} "
            f"(stops {stop_id_a!r}, {stop_id_b!r})"
        )
    return amount


# -------------------------------------------------------------------- METRO --

_GMRC_ID = re.compile(r"GMRC_(\d+)$", re.IGNORECASE)


@cache
def _metro_matrix() -> tuple[dict, dict, dict]:
    """(pair_index, station_id->name, name.lower()->station_id).

    The official matrix stores one direction per unordered pair (1,378 rows =
    53*52/2); the reverse is resolved at lookup time.
    """
    pairs: dict[tuple[int, int], dict] = {}
    stations: dict[int, str] = {}
    for r in _read_csv(METRO_OD_CSV):
        from_id, to_id = int(r["from_id"]), int(r["to_id"])
        stations[from_id] = r["from_station"].strip()
        stations[to_id] = r["to_station"].strip()
        pairs[(from_id, to_id)] = {
            "fare_inr": int(r["fare_inr_token"]),
            "distance_km": float(r["distance_km"]),
            "travel_time_min": int(r["travel_time_min"]),  # "04" -> 4 minutes
            "interchange": r["interchange"].strip(),  # "0" or interchange station
            "stations_travelled": int(r["stations_travelled"]),
        }
    names = {name.lower(): sid for sid, name in stations.items()}
    return pairs, stations, names


def _metro_station_id(x: int | str, role: str) -> int:
    """Resolve a metro station given as int id, 'GMRC_05'-style id, or name."""
    _, stations, names = _metro_matrix()
    if isinstance(x, int) and not isinstance(x, bool):
        if x in stations:
            return x
        raise KeyError(
            f"unknown metro station id {x!r} ({role}): matrix has "
            f"{len(stations)} stations, ids 1-54 with gaps"
        )
    text = str(x).strip()
    match = _GMRC_ID.match(text)
    if match:
        sid = int(match.group(1))
        if sid in stations:
            return sid
        raise KeyError(f"unknown metro station id {x!r} ({role})")
    key = text.lower()
    if key in names:
        return names[key]
    raise KeyError(f"unknown metro station name {x!r} ({role})")


def metro_fare(a: int | str, b: int | str) -> dict:
    """GMRC metro fare between two stations; either direction of the OD matrix.

    Each argument is a GMRC station id (int), a 'GMRC_05'-style id string, or a
    station name (case-insensitive). Returns
    {fare_inr, distance_km, travel_time_min, interchange, stations_travelled}.
    Unknown stations or pairs raise KeyError naming the input.
    """
    pairs, _, _ = _metro_matrix()
    id_a = _metro_station_id(a, "origin")
    id_b = _metro_station_id(b, "destination")
    record = pairs.get((id_a, id_b))
    if record is None:
        record = pairs.get((id_b, id_a))
    if record is None:
        raise KeyError(
            f"no GMRC OD pair for {a!r} -> {b!r} (ids {id_a} -> {id_b})"
        )
    return dict(record)


# ---------------------------------------------------------------- dispatcher --

def fare(mode: str, a, b, dist_km: float | None = None) -> int:
    """Fare in whole INR rupees for any mode.

    mode 'amts'  is distance-based: a/b ignored, dist_km required.
    mode 'brts'  takes GTFS stop ids in a/b.
    mode 'metro' takes ids / 'GMRC_05' strings / names in a/b.
    """
    m = str(mode).strip().lower()
    if m == "amts":
        if dist_km is None:
            raise ValueError("mode 'amts' is distance-based: pass dist_km")
        return amts_fare(dist_km)
    if m == "brts":
        return brts_fare(a, b)
    if m == "metro":
        return metro_fare(a, b)["fare_inr"]
    raise ValueError(f"unknown mode {mode!r}: expected 'amts', 'brts' or 'metro'")


# ------------------------------------------------------------------- checks --

def _run_check(rows: list[dict], check: str, inp: str, expected, fn) -> None:
    """Run fn() once; classify ok | expected_error | FAIL into `rows`."""
    wants_error = isinstance(expected, type) and issubclass(expected, Exception)
    try:
        got = fn()
    except Exception as exc:  # noqa: BLE001 - the battery itself triages
        if wants_error and isinstance(exc, expected):
            rows.append(dict(check=check, input=inp,
                             expected=f"raises {expected.__name__}",
                             got=f"{type(exc).__name__}: {exc}",
                             status="expected_error"))
        else:
            rows.append(dict(check=check, input=inp,
                             expected=_fmt(expected),
                             got=f"{type(exc).__name__}: {exc}", status="FAIL"))
        return
    if wants_error:
        rows.append(dict(check=check, input=inp,
                         expected=f"raises {expected.__name__}",
                         got=_fmt(got), status="FAIL"))
        return
    rows.append(dict(check=check, input=inp, expected=_fmt(expected),
                     got=_fmt(got), status="ok" if got == expected else "FAIL"))


def _fmt(v) -> str:
    return json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)


def _raw_metro_expected(from_id: int, to_id: int) -> dict:
    """Independently derive the expected metro record from the raw CSV (pandas)."""
    import pandas as pd

    od = pd.read_csv(METRO_OD_CSV)
    row = od[(od.from_id == from_id) & (od.to_id == to_id)]
    if row.empty:  # matrix stores one direction per pair
        row = od[(od.from_id == to_id) & (od.to_id == from_id)]
    assert not row.empty, f"raw row {from_id}<->{to_id} missing"
    row = row.iloc[0]
    return {
        "fare_inr": int(row.fare_inr_token),
        "distance_km": float(row.distance_km),
        "travel_time_min": int(row.travel_time_min),
        "interchange": str(row.interchange),
        "stations_travelled": int(row.stations_travelled),
    }


def _coverage() -> None:
    """Report which unified stops the BRTS area system actually covers."""
    import pandas as pd

    in_area = set(_brts_stop_area())
    stops = pd.read_csv(STOPS_UNIFIED_CSV, dtype=str)
    stops["in_area"] = stops.stop_id.isin(in_area)
    for label, sel in (("gtsl_bus", stops["mode"] == "gtsl_bus"),
                       ("brts/multi", stops["mode"].isin(["brts", "multi"])),
                       ("all", stops["stop_id"].notna())):
        sub = stops[sel]
        pct = 100.0 * sub.in_area.mean()
        print(f"area coverage {label:>10}: {sub.in_area.sum()}/{len(sub)} ({pct:.1f}%)")


def main() -> int:
    rows: list[dict] = []

    # AMTS: stage boundaries from the 2023-07-01 table (lower-inclusive, last max-inclusive)
    for dist, fare_inr in [(0, 5), (2.99, 5), (3.0, 10), (5.0, 15), (10.9, 20),
                           (11.0, 20), (44.0, 30), (50.0, 30), (-1, 5)]:
        _run_check(rows, "amts boundary", f"amts_fare({dist})", fare_inr,
                   lambda d=dist: amts_fare(d))

    # BRTS: expected amounts derived independently from the raw GTFS files
    import pandas as pd

    sa = pd.read_csv(GTFS_DIR / "stop_areas.txt", dtype=str, encoding="utf-8-sig")
    fl = pd.read_csv(GTFS_DIR / "fare_leg_rules.txt", dtype=str, encoding="utf-8-sig")
    fp = pd.read_csv(GTFS_DIR / "fare_products.txt", dtype=str, encoding="utf-8-sig")
    adult = fp[fp.rider_category_id == "BRTS_ADULT"]
    adult_amounts = pd.DataFrame(
        {"fare_product_id": adult.fare_product_id, "amount": adult.amount.astype(int)}
    )
    raw_brts = (fl.merge(adult_amounts, on="fare_product_id")
                 .merge(sa.rename(columns={"stop_id": "from_stop", "area_id": "from_area_id"}),
                        on="from_area_id")
                 .merge(sa.rename(columns={"stop_id": "to_stop", "area_id": "to_area_id"}),
                        on="to_area_id"))

    def raw_brts_amount(a: str, b: str) -> int:
        hit = raw_brts[((raw_brts.from_stop == a) & (raw_brts.to_stop == b))
                       | ((raw_brts.from_stop == b) & (raw_brts.to_stop == a))]
        assert not hit.empty, f"raw BRTS rule {a}<->{b} missing"
        return int(hit.amount.iloc[0])

    pairs = [("AMC_318", "AMC_3"), ("AMC_318", "AMC_1001")]
    for a, b in pairs:
        _run_check(rows, "brts area pair", f"brts_fare({a!r}, {b!r})",
                   raw_brts_amount(a, b), lambda a=a, b=b: brts_fare(a, b))
    _run_check(rows, "brts same-area pair", "brts_fare('AMC_318', 'AMC_318')",
               ValueError, lambda: brts_fare("AMC_318", "AMC_318"))
    _run_check(rows, "brts stop outside areas", "brts_fare('AMC_315', 'AMC_318')",
               ValueError, lambda: brts_fare("AMC_315", "AMC_318"))
    _run_check(rows, "brts AMTS-only stop", "brts_fare('AMC_6161', 'AMC_318')",
               ValueError, lambda: brts_fare("AMC_6161", "AMC_318"))

    # METRO: expected record derived independently from the raw OD matrix (pandas)
    exp_1_6 = _raw_metro_expected(1, 6)  # Vastral Gam <-> Apparel Park
    _run_check(rows, "metro id pair", "metro_fare(1, 6)", exp_1_6,
               lambda: metro_fare(1, 6))
    _run_check(rows, "metro reversed pair", "metro_fare(6, 1)", exp_1_6,
               lambda: metro_fare(6, 1))
    _run_check(rows, "metro GMRC-string ids", "metro_fare('GMRC_01', 'GMRC_06')",
               exp_1_6, lambda: metro_fare("GMRC_01", "GMRC_06"))
    _run_check(rows, "metro name lookup", "metro_fare('apparel park', 'vastral gam')",
               exp_1_6, lambda: metro_fare("apparel park", "vastral gam"))
    _run_check(rows, "metro unknown name", "metro_fare('Not A Station', 1)",
               KeyError, lambda: metro_fare("Not A Station", 1))

    # dispatcher
    _run_check(rows, "dispatcher metro", "fare('metro', 1, 6)", exp_1_6["fare_inr"],
               lambda: fare("metro", 1, 6))
    _run_check(rows, "dispatcher brts", "fare('brts', 'AMC_318', 'AMC_3')",
               raw_brts_amount("AMC_318", "AMC_3"),
               lambda: fare("brts", "AMC_318", "AMC_3"))
    _run_check(rows, "dispatcher amts", "fare('amts', None, None, dist_km=3.0)", 10,
               lambda: fare("amts", None, None, dist_km=3.0))
    _run_check(rows, "dispatcher amts missing dist", "fare('amts', None, None)",
               ValueError, lambda: fare("amts", None, None))
    _run_check(rows, "dispatcher unknown mode", "fare('tram', 1, 2)",
               ValueError, lambda: fare("tram", 1, 2))

    CHECKS_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["check", "input", "expected", "got", "status"]).to_csv(
        CHECKS_CSV, index=False)

    n_ok = sum(r["status"] == "ok" for r in rows)
    n_err = sum(r["status"] == "expected_error" for r in rows)
    n_fail = sum(r["status"] == "FAIL" for r in rows)
    print(f"\nfare checks: {n_ok} ok / {n_err} expected_error / {n_fail} FAIL -> {CHECKS_CSV}")
    for r in rows:
        if r["status"] == "FAIL":
            print(f"  FAIL {r['input']}: expected {r['expected']}, got {r['got']}")
    _coverage()
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
