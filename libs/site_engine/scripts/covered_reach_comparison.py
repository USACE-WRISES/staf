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
    ("New England stream (ME)", 44.30, -70.50),
    ("Northeast stream (VT)", 44.00, -72.70),
    ("Tennessee stream (TN)", 35.80, -84.20),
    ("Upper Mississippi stream (WI/IA)", 43.20, -91.50),
    ("Souris-Red stream (MN/ND)", 47.80, -96.50),
    ("Missouri stream (KS)", 39.00, -96.50),
    ("Texas stream (TX)", 30.30, -98.00),
    ("Rio Grande stream (NM)", 33.90, -105.50),
    ("Great Basin stream (UT)", 40.50, -111.80),
    ("Lower Colorado stream (AZ)", 34.20, -111.50),
    ("California stream (CA)", 38.80, -121.00),
    ("Black Hills stream (SD)", 44.50, -103.50),
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


def compare_point(label: str, lat: float, lon: float) -> dict:
    """One panel point -> ALWAYS a ledger row.

    ``outcome`` is "ok" for a usable comparison; every other outcome carries
    the stage that stopped it and a reason, so the full-panel run says exactly
    why each dropped point dropped (the migration evidence base must account
    for every site, not just the survivors).
    """
    d = 0.012
    base = {"label": label, "lat": lat, "lon": lon, "outcome": "ok",
            "reason": "", "comid": None, "nhdplusid": None}
    v2_fc = flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d)
    hit = flowlines.nearest_point_on_lines(v2_fc, lat, lon)
    if hit is None or hit[3] is None:
        base.update(outcome="skipped_no_covered_reach",
                    reason="no V2 flowline near the panel point")
        print(f"  {label}: {base['outcome']}")
        return base
    comid = hit[3]
    base["comid"] = comid
    sc = streamcat.metrics_by_comid(comid, _SC_NAMES)
    if not sc:
        base.update(outcome="skipped_streamcat_empty",
                    reason=f"StreamCat returned no row for COMID {comid}")
        print(f"  {label}: {base['outcome']}")
        return base
    t0 = time.time()
    rec = compute_site(hit[0], hit[1], {"includeGeometry": False})
    secs = round(time.time() - t0, 1)
    base["engine_secs"] = secs
    if rec["status"] != "ok":
        base.update(outcome=f"engine_{rec['status']}",
                    reason=str(rec.get("reason") or ""))
        print(f"  {label}: {base['outcome']} ({base['reason']}) {secs}s")
        return base
    engine_da = (rec.get("site") or {}).get("drainageAreaSqkm")
    sc_da = sc.get("wsareasqkm")
    base.update(
        nhdplusid=(rec.get("site") or {}).get("nhdplusId"),
        engine_da_sqkm=engine_da, streamcat_da_sqkm=sc_da,
        da_ratio=(round(engine_da / sc_da, 3) if engine_da and sc_da else None))
    for key, (col, transform) in _PAIRS.items():
        ev = (rec["metrics"].get(key) or {}).get("value")
        sv = sc.get(col)
        base[f"engine_{key}"] = (round(transform(float(ev)), 3)
                                 if ev is not None else None)
        base[f"sc_{col}"] = sv
    print(f"  {label}: COMID {comid}, DA ratio {base['da_ratio']}, {secs}s")
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    rows = []
    for label, lat, lon in (PANEL[: args.limit] if args.limit else PANEL):
        print(f"comparing {label} ...", flush=True)
        rows.append(compare_point(label, lat, lon))

    ok_rows = [r for r in rows if r["outcome"] == "ok"]
    outcome_counts: dict[str, int] = {}
    for r in rows:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1
    summary: dict = {"n_points": len(rows), "n_ok": len(ok_rows),
                     "outcomes": outcome_counts}
    for key, (col, _t) in _PAIRS.items():
        diffs = []
        for r in ok_rows:
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
        fieldnames = sorted({k for r in rows for k in r},
                            key=lambda k: (k not in ("label", "outcome",
                                                     "reason", "comid"), k))
        with (out_dir / "covered_reach_comparison.csv").open(
                "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, restval="")
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
