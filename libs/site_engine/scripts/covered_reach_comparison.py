"""G7 covered-reach study: engine metrics vs EPA StreamCat at covered reaches.

At points on the covered NHDPlus V2 network, run the engine's full computation
and compare each engine metric against its StreamCat analog for the V2 COMID.
This is the evidence base for any future migration decision (the engine never
feeds scoring on covered streams without this study plus the score-level
equivalence study).

Interpretation caveat recorded per row: the engine watershed is the TRUE HR
watershed at the exact point, while StreamCat describes the V2 reach-outlet
watershed, so the drainage-area ratio between the two frames every metric
comparison. Unit conversions: NID storage acre-ft/km2 -> m3/km2 (x 1233.48)
against StreamCat ``damnrmstorws`` (normal storage) and ``damnidstorws``.

The ``dammed:`` panel points sit a few km below USACE reservoirs so the dam
metrics are compared on watersheds that actually contain dams (the 2026-08
panel had none). They are large basins: expect long runs, and honest budget
refusals count as ledger rows, never as agreement.

Requires the repo checkout (imports EASI's StreamCat client lazily, so the
module itself imports offline for tests). Writes CSV+JSON to the gitignored
out/ dir.

Examples:
  python scripts/covered_reach_comparison.py            # default panel
  python scripts/covered_reach_comparison.py --limit 3
  python scripts/covered_reach_comparison.py --only-dammed
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
EASI_ROOT = REPO / "apps" / "easi"

ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184
DAMMED_PREFIX = "dammed:"

# label, lat, lon: covered-network points across regions and stream sizes.
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
    # Reaches below USACE reservoirs (the dammed subset).
    (f"{DAMMED_PREFIX} Olentangy below Delaware Dam (OH)", 40.2550, -83.0720),
    (f"{DAMMED_PREFIX} Alum Creek below Alum Creek Dam (OH)", 40.1540, -82.9800),
    (f"{DAMMED_PREFIX} Deer Creek below Deer Creek Dam (OH)", 39.6210, -83.2160),
    (f"{DAMMED_PREFIX} Caesar Creek below Caesar Creek Dam (OH)", 39.4870, -84.0640),
]

_SC_NAMES = ["pctimp2019", "pctcrop2019", "pcthay2019", "rddens", "kffact",
             "damnrmstor", "damnidstor", "damdens", "runoff",
             "pctdecid2019", "pctconif2019", "pctmxfst2019",
             "pctwdwet2019", "pcthbwet2019", "pctshrb2019", "pctgrs2019"]


def _same(v: float) -> float:
    return v


def _acre_ft_to_m3(v: float) -> float:
    return v * ACRE_FT_PER_KM2_TO_M3_PER_KM2


# engine metric key -> (StreamCat columns summed, transform(engine value) -> SC units)
_PAIRS: dict[str, tuple[tuple[str, ...], object]] = {
    "imperviousPctWatershed": (("pctimp2019ws",), _same),
    "cropPctWatershed": (("pctcrop2019ws",), _same),
    "hayPasturePctWatershed": (("pcthay2019ws",), _same),
    "woodyWetlandPctWatershed": (("pctwdwet2019ws",), _same),
    "herbWetlandPctWatershed": (("pcthbwet2019ws",), _same),
    "forestPctRiparian": (("pctconif2019wsrp100", "pctdecid2019wsrp100",
                           "pctmxfst2019wsrp100"), _same),
    "shrubPctRiparian": (("pctshrb2019wsrp100",), _same),
    "grasslandPctRiparian": (("pctgrs2019wsrp100",), _same),
    "woodyWetlandPctRiparian": (("pctwdwet2019wsrp100",), _same),
    "herbWetlandPctRiparian": (("pcthbwet2019wsrp100",), _same),
    "roadDensity": (("rddensws",), _same),
    "soilKFactor": (("kffactws",), _same),
    "damStoragePerSqkm": (("damnrmstorws",), _acre_ft_to_m3),
    "damNidStoragePerSqkm": (("damnidstorws",), _acre_ft_to_m3),
    "damDensityPerSqkm": (("damdensws",), _same),
    "runoffDepthMm": (("runoffws",), _same),
}


def sc_column(cols: tuple[str, ...]) -> str:
    """The ledger column name for a StreamCat side (a sum joins with '+')."""
    return "+".join(cols)


def sc_value(sc: dict, cols: tuple[str, ...]):
    """Sum of the StreamCat columns; None when any member is missing (a
    missing class is unknown, never zero)."""
    vals = [sc.get(c) for c in cols]
    if any(v is None for v in vals):
        return None
    try:
        return round(sum(float(v) for v in vals), 3)
    except (TypeError, ValueError):
        return None


def _easi():
    """EASI's clients, imported lazily so this module imports offline."""
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    if str(EASI_ROOT) not in sys.path:
        sys.path.insert(0, str(EASI_ROOT))
    from easi import delineation                       # noqa: E402
    from easi.datasources import flowlines, streamcat  # noqa: E402
    return flowlines, streamcat, delineation


def compare_point(label: str, lat: float, lon: float) -> dict:
    """One panel point -> ALWAYS a ledger row.

    ``outcome`` is "ok" for a usable comparison; every other outcome carries
    the stage that stopped it and a reason, so the full-panel run says exactly
    why each dropped point dropped (the migration evidence base must account
    for every site, not just the survivors).
    """
    flowlines, streamcat, delineation = _easi()
    from site_engine import compute_site

    d = 0.012
    base = {"label": label, "lat": lat, "lon": lon, "outcome": "ok",
            "reason": "", "comid": None, "nhdplusid": None,
            "dammed": label.startswith(DAMMED_PREFIX)}
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
    ws = rec.get("watershed") or {}
    base["n_reaches"], base["n_hops"] = ws.get("nReaches"), ws.get("nHops")
    if rec["status"] != "ok":
        base.update(outcome=f"engine_{rec['status']}",
                    reason=str(rec.get("reason") or ""))
        print(f"  {label}: {base['outcome']} ({base['reason']}) {secs}s")
        return base
    engine_da = (rec.get("site") or {}).get("drainageAreaSqkm")
    # StreamCat's API carries no area column; the V2 drainage area comes from
    # the same NHDPlus attribute read EASI uses.
    sc_da = delineation.flowline_attrs(comid).get("drainage_area_sqkm")
    base.update(
        nhdplusid=(rec.get("site") or {}).get("nhdplusId"),
        engine_da_sqkm=engine_da, streamcat_da_sqkm=sc_da,
        da_ratio=(round(engine_da / sc_da, 3) if engine_da and sc_da else None))
    for key, (cols, transform) in _PAIRS.items():
        ev = (rec["metrics"].get(key) or {}).get("value")
        base[f"engine_{key}"] = (round(transform(float(ev)), 3)
                                 if ev is not None else None)
        base[f"sc_{sc_column(cols)}"] = sc_value(sc, cols)
    print(f"  {label}: COMID {comid}, DA ratio {base['da_ratio']}, {secs}s")
    return base


def summarize(rows: list[dict]) -> dict:
    ok_rows = [r for r in rows if r["outcome"] == "ok"]
    outcome_counts: dict[str, int] = {}
    for r in rows:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1
    summary: dict = {"n_points": len(rows), "n_ok": len(ok_rows),
                     "outcomes": outcome_counts, "metrics": {},
                     "dammed": {"n_points": sum(1 for r in rows if r.get("dammed")),
                                "n_ok": sum(1 for r in ok_rows if r.get("dammed")),
                                "metrics": {}}}
    for subset_key, subset in (("metrics", ok_rows),
                               ("dammed", [r for r in ok_rows if r.get("dammed")])):
        target = summary[subset_key] if subset_key == "metrics" else summary["dammed"]["metrics"]
        for key, (cols, _t) in _PAIRS.items():
            diffs = []
            for r in subset:
                ev, sv = r.get(f"engine_{key}"), r.get(f"sc_{sc_column(cols)}")
                if ev is None or sv is None:
                    continue
                try:
                    diffs.append(float(ev) - float(sv))
                except (TypeError, ValueError):
                    continue
            if diffs:
                target[key] = {"n": len(diffs),
                               "median_diff": round(statistics.median(diffs), 3),
                               "max_abs_diff": round(max(abs(x) for x in diffs), 3)}
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-dammed", action="store_true",
                    help="run only the dammed subset of the panel")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    panel = [p for p in PANEL if p[0].startswith(DAMMED_PREFIX)] if args.only_dammed else PANEL
    rows = []
    for label, lat, lon in (panel[: args.limit] if args.limit else panel):
        print(f"comparing {label} ...", flush=True)
        rows.append(compare_point(label, lat, lon))

    summary = summarize(rows)
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
