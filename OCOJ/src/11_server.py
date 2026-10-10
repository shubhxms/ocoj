"""OCOJ 11 — local API server: the McRAPTOR router behind JSON + exposure-aware walking.

stdlib only (http.server). The UI (OCOJ/ui/index.html) is native-component brutalist
and talks to:

  GET /                     -> OCOJ/ui/index.html
  GET /api/stops            -> [{id, name, mode, lat, lon, aqi, uv, tree, exp}, ...]
  GET /api/plan?o=..&d=..&at=HH:MM&radius=800&shade=0|1
      -> {query:{...}, journeys:[{arr, dep, dur_min, fare, transfers, walk_km,
          exposure, legs:[{kind, mode, route, from, to, from_name, to_name,
                           t0, t1, fare, km, exp}]}]}

shade=1 ("shade-seeking"): footpath walking seconds are scaled by
1 + (exp_a + exp_b) / 2   (exp in 0..1, see 10_exposure.py), so the router
penalises walking through hot/unshady stops — walks in the worst-exposure
places count up to 2x their clock time. Ride legs are untouched (vehicle
interiors; walking is where exposure acts). A separate Router instance is
built per (radius, shade) and cached.

Run:  .venv/bin/python OCOJ/src/11_server.py [--port 8787]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "OCOJ" / "src"
UI = ROOT / "OCOJ" / "ui" / "index.html"

_spec = importlib.util.spec_from_file_location("ocoj_router", SRC / "04_router.py")
assert _spec is not None and _spec.loader is not None
router_mod = importlib.util.module_from_spec(_spec)
sys.modules["ocoj_router"] = router_mod
_spec.loader.exec_module(router_mod)
RouterT = router_mod.Router
stops = pd.read_csv(ROOT / "OCOJ/data/stops_unified.csv", dtype={"stop_id": str}, encoding="utf-8-sig")
expo = pd.read_csv(ROOT / "OCOJ/data/stop_exposure_joined.csv", dtype={"stop_id": str}, encoding="utf-8-sig")
EXPO = dict(zip(expo["stop_id"], expo["exp"]))
NAME = dict(zip(stops["stop_id"], stops["name"]))

_routers: dict[tuple[float, bool], RouterT] = {}
_lock = threading.Lock()


def get_router(radius: float, shade: bool) -> RouterT:
    key = (radius, shade)
    if key not in _routers:
        r = router_mod.Router(walk_radius_m=radius)
        if shade:  # scale every footpath by endpoint exposure (see docstring)
            idx = {sid: i for i, sid in enumerate(r.stop_ids)}
            exp_idx = {idx[sid]: e for sid, e in EXPO.items() if sid in idx}
            scaled = {}
            for a, nbrs in r.foot.items():
                ea = exp_idx.get(a, 0.4)
                scaled[a] = [
                    (b, int(wsec * (1.0 + (ea + exp_idx.get(b, 0.4)) / 2))) for b, wsec in nbrs
                ]
            r.foot = scaled
        if len(_routers) > 11:
            _routers.clear()
        _routers[key] = r
    return _routers[key]


def leg_dict(leg) -> dict:
    d = {
        "kind": leg.kind,
        "mode": leg.mode,
        "route": leg.route_id or None,
        "from": leg.from_stop,
        "to": leg.to_stop,
        "from_name": NAME.get(leg.from_stop, leg.from_stop),
        "to_name": NAME.get(leg.to_stop, leg.to_stop),
        "t0": router_mod.sec_to_hhmm(leg.from_time),
        "t1": router_mod.sec_to_hhmm(leg.to_time),
        "fare": None if leg.fare != leg.fare else round(leg.fare),
        "km": round(leg.dist_km, 2),
    }
    if leg.kind == "walk":
        d["exp"] = round(leg.dist_km * 100 * (EXPO.get(leg.from_stop, 0.4) + EXPO.get(leg.to_stop, 0.4)) / 2, 1)
    else:
        d["exp"] = 0.0
    return d


def journeys_json(o: str, d: str, at: str, radius: float, shade: bool) -> dict:
    js = get_router(radius, shade).plan(o, d, at)
    out = []
    for j in js:
        legs = [leg_dict(l) for l in j.legs]
        out.append(
            {
                "dep": router_mod.sec_to_hhmm(j.dep_sec),
                "arr": router_mod.sec_to_hhmm(j.arr_sec),
                "dur_min": j.dur_min,
                "fare": None if j.fare != j.fare else round(j.fare),
                "transfers": j.n_transfers,
                "walk_km": j.walk_km,
                "exposure": round(sum(l["exp"] for l in legs if l["kind"] == "walk"), 1),
                "legs": legs,
            }
        )
    return {
        "query": {"o": o, "d": d, "o_name": NAME.get(o, o), "d_name": NAME.get(d, d),
                  "at": at, "radius_m": radius, "shade": shade},
        "n": len(out),
        "journeys": out,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:  # quieter console
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _send(self, code: int, payload: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        u = urlparse(self.path)
        if u.path.startswith("/ui/") or u.path.startswith("/vendor/"):
            rel = u.path.lstrip("/")
            base = ROOT / "OCOJ"
            cand = (base / rel).resolve()
            if cand.is_file() and str(cand).startswith(str(base)):  # no traversal
                ctype = "text/css" if cand.suffix == ".css" else (
                    "application/javascript" if cand.suffix == ".js" else "application/octet-stream"
                )
                self._send(200, cand.read_bytes(), ctype)
            else:
                self._json(404, {"error": f"no static {u.path}"})
            return
        if u.path == "/" or u.path == "/index.html":
            if UI.exists():
                self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"UI not built yet", "text/plain")
            return
        if u.path == "/api/stops":
            merged = stops.merge(expo[["stop_id", "aqi", "uv_midday", "tree_share", "exp"]], on="stop_id")
            merged = stops.merge(expo[["stop_id", "aqi", "uv_midday", "tree_share", "exp"]], on="stop_id")
            payload = [
                {"id": r["stop_id"], "name": r["name"], "mode": r["mode"],
                 "lat": round(float(r["lat"]), 5), "lon": round(float(r["lon"]), 5),
                 "aqi": round(float(r["aqi"])), "uv": round(float(r["uv_midday"]), 1),
                 "tree": round(float(r["tree_share"]), 3), "exp": round(float(r["exp"]), 3)}
                for r in merged.to_dict("records")
            ]
            self._json(200, payload)
            return
        if u.path == "/api/plan":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                o, d, at = q["o"], q["d"], q.get("at", "09:00")
                radius = float(q.get("radius", 800))
                shade = q.get("shade", "0") in ("1", "true")
            except (KeyError, ValueError) as e:
                self._json(400, {"error": f"bad params: {e}"})
                return
            with _lock:
                try:
                    self._json(200, journeys_json(o, d, at, radius, shade))
                except (KeyError, ValueError) as e:
                    self._json(400, {"error": str(e)})
            return
        self._json(404, {"error": f"no route {u.path}"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"OCOJ server on http://127.0.0.1:{args.port}  (UI: {UI})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
