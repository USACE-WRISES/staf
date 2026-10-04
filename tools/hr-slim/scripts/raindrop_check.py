"""The STAF data bundle's raindrop against NLDI's, on points along pilot HR flowlines.

EASI routes a click on an HR-only stream (and a typed coordinate) to the V2 reach it drains to.
Live, NLDI's hydrolocation raindrop answers (a trace down the NHDPlus V2 flow-direction grid);
with the bundle, the V2 catchment that contains the point answers (``site_engine.bundle.raindrop``).
This check samples flowline midpoints in every lower-48 pilot region, labelled by whether a V2
line runs within 150 ft (``on a V2 line``) or not (``HR-only``), asks both, and classifies each
disagreement by where NLDI's reach sits relative to the bundle's on the V2 network (downstream of
it, upstream of it, or elsewhere) and by drainage area.

The first run (2026-10-02) sampled by the kinds of an HR network walk the bundle no longer ships
(``routed``, ``covered``, ``unrouted``); ``--reuse`` classifies those saved NLDI answers again.

    python tools/hr-slim/scripts/raindrop_check.py --per-region 8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import shapely

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "libs" / "site_engine"))
sys.path.insert(0, str(REPO / "tools" / "hr-slim"))

from hrbuild import sources  # noqa: E402
from site_engine import anchor, bundle  # noqa: E402
from site_engine._hrslim import fmt  # noqa: E402

BUNDLE = REPO / "apps" / "hr-data" / "data"
OUT = Path(r"D:\Data\nhdplus-hr\slim2\accept\raindrop.json")
WALK = 60          # V2 hops searched when placing NLDI's reach relative to the bundle's


def _sample(ds, vpu: str, rng, n: int) -> list[dict]:
    """Midpoints of ``n`` random network flowlines of the region, labelled by a V2 line within
    150 ft."""
    from site_engine.geometry import nearest_point_on_records
    region = ds._regions[vpu]
    pick = np.sort(rng.choice(region.meta["lines"]["rows"], size=n, replace=False))
    t = ds._line_rows(region, pick)
    out = []
    for row, nid, g in zip(pick, t.column("nhdplusid").to_pylist(), fmt.decode_lines(t)):
        p = shapely.line_interpolate_point(shapely.line_merge(g), 0.5, normalized=True)
        lat, lon, half = round(float(p.y), 6), round(float(p.x), 6), 0.003
        v2 = bundle.v2_lines_in_box(lon - half, lat - half, lon + half, lat + half) or []
        hit = nearest_point_on_records(v2, lat, lon)
        kind = "on a V2 line" if hit is not None and hit[2] <= 150.0 else "HR-only"
        out.append({"vpu": vpu, "kind": kind, "nhdplusid": int(nid), "row": int(row), "lat": lat, "lon": lon})
    return out


def _live(point: dict) -> dict:
    """NLDI's answer (hydrolocation, then flowtrace on a transient failure; three tries)."""
    for attempt in range(3):
        got = anchor.hydrolocation_snap(point["lat"], point["lon"])
        if not got.get("error"):
            return got
        time.sleep(15 * (attempt + 1))
    return got


def _chain(comid: int, upstream: bool) -> list[int]:
    """Up to ``WALK`` V2 reaches along the mainstem from ``comid`` (it first), stopping where the
    pilot's regions end."""
    ds2 = bundle.v2_dataset()
    key = "uphydroseq" if upstream else "dnhydroseq"
    out, rec = [], bundle.v2_flowline(comid)
    while rec is not None and len(out) < WALK:
        out.append(int(rec["nhdplusid"]))
        nxt = rec.get(key)
        if not nxt:
            break
        t = ds2.reach(hydroseq=int(nxt))
        recs = bundle._records(t) if t is not None else []
        rec = recs[0] if len(recs) == 1 else None
    return out


def _why_none(point: dict) -> str:
    """Why the bundle left a point to NLDI."""
    ds2 = bundle.v2_dataset()
    if bundle._catchment_at(ds2, point["lat"], point["lon"]) is None:
        return "in no V2 catchment the pilot holds"
    return "the catchment's flowline is missing"


def _relation(local: int, live: int) -> str:
    if local == live:
        return "same"
    if live in _chain(local, upstream=False):
        return "nldi downstream"
    if local in _chain(live, upstream=False):
        return "nldi upstream"
    return "elsewhere"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-region", type=int, default=8, help="flowlines sampled per region")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reuse", action="store_true", help="classify the last run's answers again (no NLDI)")
    args = ap.parse_args(argv)
    bundle.set_source("bundle", BUNDLE)
    ds = bundle.dataset()
    if args.reuse:
        pts = json.loads(OUT.read_text(encoding="utf-8"))
        lives = [p["nldi"] for p in pts]
        for p in pts:
            p["local"] = bundle.raindrop(p["lat"], p["lon"])
    else:
        rng = np.random.default_rng(args.seed)
        pts = []
        for vpu in sources.lower48(ds.vpus):
            pts += _sample(ds, vpu, rng, args.per_region)
        for p in pts:
            p["local"] = bundle.raindrop(p["lat"], p["lon"])
        bundle.set_source("service")                 # NLDI itself, not the bundle's answer
        with ThreadPoolExecutor(args.workers) as pool:
            lives = list(pool.map(_live, pts))
        bundle.set_source("bundle", BUNDLE)
    for p, live in zip(pts, lives):
        p["nldi"] = live
        p.pop("daRatioNldiToLocal", None)
        p.pop("snapApartFt", None)
        p["whyNone"] = _why_none(p) if p["local"] is None else None
        loc = (p["local"] or {}).get("comid")
        nl = live.get("comid")
        p["relation"] = ("no local answer" if loc is None else "nldi error" if live.get("error")
                         else "nldi none" if nl is None else _relation(int(loc), int(nl)))
        if loc is not None and nl is not None:
            a, b = bundle.v2_flowline(int(loc)), bundle.v2_flowline(int(nl))
            if a and b and a.get("totdasqkm") and b.get("totdasqkm"):
                p["daRatioNldiToLocal"] = round(b["totdasqkm"] / a["totdasqkm"], 3)
            if p["local"].get("snap_lat") is not None and live.get("snap_lat") is not None:
                p["snapApartFt"] = anchor.distance_ft(p["local"]["snap_lat"], p["local"]["snap_lon"],
                                                      live["snap_lat"], live["snap_lon"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pts, indent=1), encoding="utf-8", newline="\n")
    summary: dict = {}
    for p in pts:
        summary.setdefault(p["kind"], {}).setdefault(p["relation"], 0)
        summary[p["kind"]][p["relation"]] += 1
    print(json.dumps(summary, indent=1))
    print("| Region | kind | HR line | bundle | NLDI | relation | NLDI/bundle DA | snaps apart ft | why no local |")
    print("|---|---|---|---|---|---|---|---|---|")
    for p in pts:
        if p["relation"] != "same":
            print(f"| {p['vpu']} | {p['kind']} | {p['nhdplusid']} | {(p['local'] or {}).get('comid')} | "
                  f"{p['nldi'].get('comid', p['nldi'].get('error'))} | {p['relation']} | "
                  f"{p.get('daRatioNldiToLocal')} | {p.get('snapApartFt')} | {p.get('whyNone') or ''} |")
    print("written", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
