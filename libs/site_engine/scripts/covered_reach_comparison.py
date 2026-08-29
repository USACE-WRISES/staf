"""G7 covered-reach study: engine metrics vs EPA StreamCat at covered reaches.

At points on the covered NHDPlus V2 network, run the engine's full computation
and compare each engine metric against its StreamCat analog for the V2 COMID.
This is the evidence base for any future migration decision (the engine never
feeds EASI scoring without this study plus a curve refit).

Interpretation caveat recorded per row: the engine watershed is the TRUE HR
watershed at the exact point, while StreamCat describes the V2 reach-outlet
watershed, so the drainage-area ratio between the two frames every metric
comparison. Unit conversions: NID storage acre-ft/km2 -> m3/km2 (x 1233.48)
against StreamCat ``damnrmstorws``.

Requires the repo checkout (imports EASI's StreamCat client). Writes CSV+JSON
to the gitignored out/ dir.

Examples:
  python scripts/covered_reach_comparison.py            # default panel
  python scripts/covered_reach_comparison.py --limit 3
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
REPO = ENGINE_ROOT.parents[1]
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(REPO / "apps" / "easi"))

from easi.datasources import flowlines, streamcat          # noqa: E402
from site_engine import compute_site                       # noqa: E402

ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184

# label, lat, lon — covered-network points across regions and stream sizes.
PANEL = [
    ("Sugar Run near Waldo OH", 40.3101, -83.0563),
    ("Headwater trib near Worthington OH", 40.0962, -83.0203),
    ("Mid-Atlantic stream (PA)", 40.80, -76.50),
    ("South Atlantic stream (GA)", 33.50, -83.30),
    ("Great Lakes stream (MI)", 43.50, -84.60),
    ("Ozark stream (AR/OK)", 35.50, -94.50),
    ("Mountain stream (CO)", 39.00, -107.50),
    ("Pacific Northwest stream (WA)", 46.90, -122.20),
]

_SC_NAMES = ["pctimp2019", "pctcrop2019", "pcthay2019", "rddens", "kffact",
             "damnrmstor", "runoff", "pctdecid2019", "pctconif2019",
             "pctmxfst2019", "pctwdwet2019", "pcthbwet2019"]

# engine metric key -> (streamcat column, transform(engine value) -> SC units)
_PAIRS = {
    "imperviousPctWatershed": ("pctimp2019ws", lambda v: v),
    "cropPctWatershed": ("pctcrop2019ws", lambda v: v),
    "hayPasturePctWatershed": ("pcthay2019ws", lambda v: v),
    "roadDensity": ("rddensws", lambda v: v),
    "soilKFactor": ("kffactws", lambda v: v),
    "damStoragePerSqkm": ("damnrmstorws",
                          lambda v: v * ACRE_FT_PER_KM2_TO_M3_PER_KM2),
    "runoffDepthMm": ("runoffws", lambda v: v),
}


def compare_point(label: str, lat: float, lon: float) -> dict | None:
    d = 0.012
    v2_fc = flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d)
    hit = flowlines.nearest_point_on_lines(v2_fc, lat, lon)
    if hit is None or hit[3] is None:
        print(f"  {label}: no covered reach nearby, skipped")
        return None
    comid = hit[3]
    sc = streamcat.metrics_by_comid(comid, _SC_NAMES)
    if not sc:
        print(f"  {label}: StreamCat empty for COMID {comid}, skipped")
        return None
    t0 = time.time()
    rec = compute_site(hit[0], hit[1], {"includeGeometry": False})
    secs = round(time.time() - t0, 1)
    if rec["status"] != "ok":
        print(f"  {label}: engine {rec['status']} ({rec['reason']})")
        return None
    engine_da = (rec.get("site") or {}).get("drainageAreaSqkm")
    sc_da = sc.get("wsareasqkm")
    row = {"label": label, "comid": comid,
           "nhdplusid": (rec.get("site") or {}).get("nhdplusId"),
           "engine_da_sqkm": engine_da, "streamcat_da_sqkm": sc_da,
           "da_ratio": (round(engine_da / sc_da, 3)
                        if engine_da and sc_da else None),
           "engine_secs": secs}
    for key, (col, transform) in _PAIRS.items():
        ev = (rec["metrics"].get(key) or {}).get("value")
        sv = sc.get(col)
        row[f"engine_{key}"] = (round(transform(float(ev)), 3)
                                if ev is not None else None)
        row[f"sc_{col}"] = sv
    print(f"  {label}: COMID {comid}, DA ratio {row['da_ratio']}, {secs}s")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    rows = []
    for label, lat, lon in (PANEL[: args.limit] if args.limit else PANEL):
        print(f"comparing {label} ...", flush=True)
        row = compare_point(label, lat, lon)
        if row:
            rows.append(row)

    summary: dict = {"n_points": len(rows)}
    for key, (col, _t) in _PAIRS.items():
        diffs = []
        for r in rows:
            ev, sv = r.get(f"engine_{key}"), r.get(f"sc_{col}")
            if ev is None or sv is None:
                continue
            try:
                diffs.append(float(ev) - float(sv))
            except (TypeError, ValueError):
                continue
        if diffs:
            summary[key] = {"n": len(diffs),
                            "median_diff": round(statistics.median(diffs), 3),
                            "max_abs_diff": round(max(abs(x) for x in diffs), 3)}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        with (out_dir / "covered_reach_comparison.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    (out_dir / "covered_reach_comparison.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, default=str),
        encoding="utf-8")
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
