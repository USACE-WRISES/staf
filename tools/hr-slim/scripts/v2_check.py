"""The STAF data bundle's V2 reads against the live services EASI asks.

Two comparisons at V2 reaches the raindrop checks found (``slim2\\accept\\raindrop_run*.json``):

- ``attrs``: the fabric API's flowline properties EASI reads (name, reach code, drainage area,
  slope, fcode, stream order, length, the 13 EROM flows) against ``bundle.v2_feature``.
- ``nav``: NLDI's ``upstreamMain`` and ``downstreamMain`` navigations within 8.05 km (EASI's NRSA
  connectivity, 5 miles) against ``bundle.v2_mainstem``.

    python tools/hr-slim/scripts/v2_check.py attrs --n 30
    python tools/hr-slim/scripts/v2_check.py nav --n 10
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "libs" / "site_engine"))
sys.path.insert(0, str(REPO / "apps" / "easi"))

from site_engine import bundle  # noqa: E402

BUNDLE = REPO / "apps" / "hr-data" / "data"
ACCEPT = Path(r"D:\Data\nhdplus-hr\slim2\accept")
FIELDS = ("gnis_name", "reachcode", "totdasqkm", "slope", "fcode", "streamorde", "lengthkm")


def _comids(n: int) -> list[int]:
    seen = []
    for name in ("raindrop_run1.json", "raindrop_run2.json"):
        path = ACCEPT / name
        if not path.exists():
            continue
        for p in json.loads(path.read_text(encoding="utf-8")):
            c = (p.get("nldi") or {}).get("comid")
            if c is not None and int(c) not in seen:
                seen.append(int(c))
    return seen[:n]


def _same(a, b) -> bool:
    a = None if isinstance(a, str) and not a.strip() else a      # a blank name is no name (EASI strips it)
    b = None if isinstance(b, str) and not b.strip() else b
    if a is None or b is None:
        return a is None and b is None
    try:
        fa, fb = float(a), float(b)
        return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12) or (fa < 0 and fb is None)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def attrs(n: int) -> int:
    from easi.datasources import fabric
    bundle.set_source("service")
    live = dict((c, fabric.feature_by_comid(c)) for c in _comids(n))
    bundle.set_source("bundle", BUNDLE)
    fabric.clear_feature_memo()
    same = 0
    for c, feat in live.items():
        loc = bundle.v2_feature(c)
        if not feat or loc is None:
            print(c, "live" if feat else "no live answer", "bundle" if loc else "no bundle answer")
            continue
        lp, bp = feat["properties"], loc["properties"]
        keys = FIELDS + tuple(k for k in lp if k.startswith("qe_"))
        diff = [(k, lp.get(k), bp.get(k)) for k in keys
                if not _same(lp.get(k), bp.get(k)) and not (k == "slope" and float(lp.get(k) or 0) < 0
                                                              and bp.get(k) is None)]
        if diff:
            print(c, diff)
        else:
            same += 1
    print(f"attributes identical at {same} of {len(live)} COMIDs")
    return 0


def nav(n: int, km: float = 8.05) -> int:
    from pynhd import NLDI
    bundle.set_source("bundle", BUNDLE)
    same = total = 0
    for c in _comids(n):
        for direction, up in (("upstreamMain", True), ("downstreamMain", False)):
            try:
                frame = NLDI().navigate_byid(fsource="comid", fid=str(c), navigation=direction,
                                             source="flowlines", distance=km)
                col = "nhdplus_comid" if "nhdplus_comid" in frame.columns else "comid"
                live = set(int(v) for v in frame[col].dropna())
            except Exception as exc:  # noqa: BLE001
                print(c, direction, "NLDI failed:", exc)
                continue
            recs = bundle.v2_mainstem(c, km, upstream=up)
            if recs is None:
                print(c, direction, "the bundle cannot answer (the walk leaves the pilot)")
                continue
            loc = set(int(r["nhdplusid"]) for r in recs)
            total += 1
            if loc == live:
                same += 1
            else:
                print(c, direction, "NLDI only:", sorted(live - loc), "bundle only:", sorted(loc - live))
    print(f"navigation identical in {same} of {total} walks")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["attrs", "nav"])
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args(argv)
    return attrs(args.n) if args.what == "attrs" else nav(args.n)


if __name__ == "__main__":
    raise SystemExit(main())
