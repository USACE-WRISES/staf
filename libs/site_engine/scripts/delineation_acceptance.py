"""G7 delineation acceptance panel: engine watershed area vs HR VAA drainage
area across regions and stream sizes.

Samples HR reaches in regional boxes, runs the catchment-aggregation
delineation at each reach's midpoint, and records the union-vs-VAA area
agreement, tree size, runtime, and budget hits. Writes CSV+JSON to the
gitignored out/ dir.

Examples:
  python scripts/delineation_acceptance.py               # default panel
  python scripts/delineation_acceptance.py --per-box 2
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from site_engine import delineate, hr                      # noqa: E402

BOXES = [
    ("Ohio (Waldo)", 40.30, -83.07),
    ("Mid-Atlantic (PA)", 40.80, -76.50),
    ("South Atlantic (GA)", 33.50, -83.30),
    ("Great Lakes (MI)", 43.50, -84.60),
    ("Ozarks (AR/OK)", 35.50, -94.50),
    ("Mountain West (CO)", 39.00, -107.50),
    ("Pacific Northwest (WA)", 46.90, -122.20),
]
D = 0.012


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-box", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    rows = []
    for label, blat, blon in (BOXES[: args.limit] if args.limit else BOXES):
        print(f"sampling {label} ...", flush=True)
        recs = hr.flowlines_in_bbox(blon - D, blat - D, blon + D, blat + D)
        # spread across sizes: smallest and largest drainage areas in the box
        with_da = sorted((r for r in recs if r.get("totdasqkm")),
                         key=lambda r: r["totdasqkm"])
        picks = (with_da[: max(1, args.per_box // 2)]
                 + with_da[-(args.per_box - args.per_box // 2):])
        for rec in picks:
            t0 = time.time()
            out = delineate.delineate_watershed(rec)
            secs = round(time.time() - t0, 1)
            rows.append({
                "region": label, "nhdplusid": rec["nhdplusid"],
                "vaa_da_sqkm": rec["totdasqkm"], "status": out["status"],
                "union_sqkm": out["areaSqkm"],
                "agreement": out["areaAgreement"],
                "n_reaches": out["nReaches"], "n_hops": out["nHops"],
                "secs": secs, "reason": out["reason"],
                "warnings": "; ".join(out["warnings"]),
            })
            reason = f" | {out['reason']}" if out.get("reason") else ""
            print(f"  {rec['nhdplusid']}: VAA {rec['totdasqkm']:.2f} km2 -> "
                  f"{out['status']} union {out['areaSqkm']} "
                  f"(agr {out['areaAgreement']}) reaches={out['nReaches']} "
                  f"{secs}s{reason}")

    ok = [r for r in rows if r["status"] == "ok" and r["agreement"]]
    summary = {
        "n_sites": len(rows),
        "n_ok": len(ok),
        "n_refused": sum(1 for r in rows if r["status"] == "refused"),
        "n_failed": sum(1 for r in rows if r["status"] == "failed"),
        "agreement_median": (round(statistics.median(
            r["agreement"] for r in ok), 4) if ok else None),
        "agreement_worst": (round(min(
            (r["agreement"] for r in ok),
            key=lambda a: abs(a - 1.0)) if False else
            max(ok, key=lambda r: abs(r["agreement"] - 1.0))["agreement"], 4)
            if ok else None),
        "secs_median": (round(statistics.median(r["secs"] for r in rows), 1)
                        if rows else None),
        "secs_max": max((r["secs"] for r in rows), default=None),
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        with (out_dir / "delineation_acceptance.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (out_dir / "delineation_acceptance.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, default=str),
        encoding="utf-8")
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
