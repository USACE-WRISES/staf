"""SFARI's NWIS gage statistics: the bundled table against SFARI's own live functions given time.

SFARI's live path gives up after 12 s (gage search) and 18 s (daily values); the service now takes 25
to 75 s for a gage search and answers 503 at times, so the app's answer is usually none. This check
runs the same functions with long timeouts and three tries, to compare the data rather than the
outages, at flowlines with gage-sized drainage areas (50 to 2,000 km2) in every lower-48 pilot region.

    python tools/hr-slim/scripts/nwis_check.py --per-region 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "apps" / "hr-data"))
sys.path.insert(0, str(REPO / "apps" / "sfari"))
sys.path.insert(0, str(REPO / "tools" / "hr-slim"))

from hrbuild import sources  # noqa: E402
from hrslim import Dataset2, points  # noqa: E402
from sfari.datasources import nwis as sfari_nwis  # noqa: E402

V2 = Path(r"D:\Data\nhdplus-hr\slim2")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-region", type=int, default=2)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args(argv)
    daily, nearby = sfari_nwis._daily_flow, sfari_nwis._nearby_gages

    def retried(call, empty, *a):
        """The service answers 503 at times; three tries, so the check compares data, not outages."""
        for attempt in range(3):
            got = call(*a)
            if got:
                return got
            time.sleep(10 * (attempt + 1))
        return empty

    sfari_nwis._daily_flow = lambda site, start="2014-01-01", timeout=18.0: retried(daily, None, site, start, 300.0)
    sfari_nwis._nearby_gages = lambda lat, lon, deg=0.25, timeout=12.0: retried(nearby, [], lat, lon, deg, 180.0)
    tables = points.PointTables(V2 / "tables")
    ds = Dataset2(V2 / "data")
    manifest = json.loads((V2 / "data" / "manifest.json").read_text(encoding="utf-8"))
    rng = np.random.default_rng(args.seed)
    same = n = 0
    rows = []
    print("| Region | DA km2 | point | bundle | live (long timeouts) |")
    print("|---|---|---|---|---|")
    for vpu in sources.lower48(sorted(manifest["vpus"])):
        topo = ds._regions[vpu].topo()
        da = np.nan_to_num(np.asarray(topo["da"], float))
        cand = np.nonzero((da > 50) & (da < 2000))[0]
        for row in rng.choice(cand, min(args.per_region, len(cand)), replace=False):
            t = ds.reach(nhdplusid=int(topo["ids"][row]))
            lon = (t.column("xmin")[0].as_py() + t.column("xmax")[0].as_py()) / 2e5
            lat = (t.column("ymin")[0].as_py() + t.column("ymax")[0].as_py()) / 2e5
            live = sfari_nwis.flow_stats(lat, lon, float(da[row]))
            got = points.nwis_flow_stats(tables, lat, lon, float(da[row]))
            ok = (live or {}).get("site") == (got or {}).get("site") and all(
                abs(((live or {}).get(k) or 0) - ((got or {}).get(k) or 0))
                <= 0.02 * max(1.0, abs((live or {}).get(k) or 0)) for k in ("q10", "q50", "q90"))
            same += int(ok)
            n += 1

            def show(r):
                return "none" if r is None else f"{r['site']} n={r['n_days']} Q10={r['q10']} Q50={r['q50']} Q90={r['q90']}"
            print(f"| {vpu} | {da[row]:.0f} | {lat:.4f}, {lon:.4f} | {show(got)} | {show(live)} |", flush=True)
            rows.append({"vpu": vpu, "lat": lat, "lon": lon, "da_sqkm": float(da[row]), "bundle": got, "live": live})
    print(f"\n{same} of {n} agree (same gage, quantiles within 2%)")
    out = V2 / "accept" / "nwis_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"agree": same, "points": n, "rows": rows}, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
