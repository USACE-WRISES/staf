"""Score-level equivalence study: StreamCat lookup engine vs STAF site engine.

At NRSA sites on the covered NHDPlus V2 network, run EASI once with the
StreamCat lookup engine's watershed inputs (report A) and once with the STAF
site engine's exact-watershed inputs substituted through
``assessment.recompute_watershed_rows`` (report B), then compare the eight
watershed-metric ratings and the condition class. For DEEP, score the same
StreamCat and engine values against the published regional curves with
``deep.curves.interp_curve`` and record the index shift.

Panel (deterministic, no RNG): NRSA stations from the pooled multi-cycle store
in Level III ecoregions 58 (Northeastern Highlands) and 55 (Eastern Corn Belt
Plains), the two pilot libraries. Candidates are enriched with the V2 drainage
area and three StreamCat columns, then filled round robin across six cells
(drainage-area tertiles x urban share) to ``--per-region`` sites, with at
least ``--min-dammed`` dammed basins per region swapped in within the same
size tertile. Candidates above ``--max-da-sqkm`` are excluded up front: the
tiers that consume the engine assess wadeable streams.

Pre-registered rule (decided 2026-09-01, encoded in ``verdict``): the two
engines are interchangeable for scoring if rating agreement over the eight
watershed metrics is at least 0.90, condition-class agreement is at least
0.90, and the median DEEP index shift is under 0.05. The verdict prints
pooled and per region, with and without flow alteration (whose runoff input
diverges by design).

Requires the repo checkout (EASI, DEEP, the StreamCurves NRSA store and the
assessment library). Per-site results are cached under ``out/score_equivalence/``
so a run resumes. Outputs: ``out/score_equivalence_{panel,sites,metrics,deep}.csv``,
``out/score_equivalence_study.json``, and with ``--notes`` a markdown summary
under ``notes/EASI_Report/analysis/``.

Examples:
  python scripts/score_equivalence_study.py --limit 2
  python scripts/score_equivalence_study.py --region 58 --per-region 15
  python scripts/score_equivalence_study.py --notes
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO = ENGINE_ROOT.parents[1]
EASI_ROOT = REPO / "apps" / "easi"
DEEP_ROOT = REPO / "apps" / "deep"
STATIONS = REPO / "apps" / "stream-curves" / "data" / "nrsa" / "stations.parquet"
LIBRARY = REPO / "apps" / "library" / "assessments"

REGIONS = {"58": "northeastern-highlands", "55": "eastern-corn-belt-plains"}
RULE = {"rating_agreement_min": 0.90, "class_agreement_min": 0.90,
        "deep_median_shift_max": 0.05}
URBAN_SPLIT_PCT = 2.0
ENGINE_FAMILIES = ["dams", "landcover", "roads", "runoff", "soils"]

# EASI watershed metric ids (the eight the layer feeds)
FLOW_ALTERATION_ID = "streamflow-regime-flow-alteration-regulation-water-use"

# DEEP bundle metric id -> (StreamCat column, engine metric key, transform)
DEEP_PAIRS = {
    "spring-pctimp2019ws": ("pctimp2019ws", "imperviousPctWatershed", lambda v: v),
    "spring-pctcrop2019ws": ("pctcrop2019ws", "cropPctWatershed", lambda v: v),
    "spring-pctwdwet2019ws": ("pctwdwet2019ws", "woodyWetlandPctWatershed", lambda v: v),
    "spring-pcthbwet2019ws": ("pcthbwet2019ws", "herbWetlandPctWatershed", lambda v: v),
    "spring-rddensws": ("rddensws", "roadDensity", lambda v: v),
    "spring-damdensws": ("damdensws", "damDensityPerSqkm", lambda v: v),
}
_SC_NAMES = ["pctimp2019", "pctcrop2019", "pcthay2019", "pctwdwet2019",
             "pcthbwet2019", "rddens", "damdens", "damnrmstor"]


# --------------------------------------------------------------------------- #
# panel selection (pure; tested offline)
# --------------------------------------------------------------------------- #
def _tertile_bounds(values: list[float]) -> tuple[float, float]:
    xs = sorted(values)
    if not xs:
        return 0.0, 0.0
    return xs[len(xs) // 3], xs[(2 * len(xs)) // 3]


def select_panel(candidates: list[dict], *, per_region: int, min_dammed: int,
                 max_da_sqkm: float | None) -> list[dict]:
    """Deterministic stratified pick per region.

    ``candidates`` carry ``station_key, region, lat, lon, comid, da_sqkm,
    pctimp, damnrmstor``. Sites without a drainage area or impervious value
    are ineligible. Cells = DA tertile (within the region's eligible pool) x
    urban share (impervious under ``URBAN_SPLIT_PCT`` or not); the pick walks
    the cells round robin, each cell sorted by station key, until
    ``per_region`` sites are chosen, then swaps dammed candidates in (same DA
    tertile, undammed swapped out) until ``min_dammed`` are present or none
    remain.
    """
    out: list[dict] = []
    for region in sorted({c["region"] for c in candidates}):
        pool = [c for c in candidates if c["region"] == region
                and c.get("da_sqkm") is not None and c.get("pctimp") is not None
                and (max_da_sqkm is None or float(c["da_sqkm"]) <= max_da_sqkm)]
        if not pool:
            continue
        t1, t2 = _tertile_bounds([float(c["da_sqkm"]) for c in pool])

        def tertile(c):
            da = float(c["da_sqkm"])
            return 0 if da < t1 else (1 if da < t2 else 2)

        cells: dict[tuple[int, int], list[dict]] = {}
        for c in pool:
            key = (tertile(c), 1 if float(c["pctimp"]) >= URBAN_SPLIT_PCT else 0)
            cells.setdefault(key, []).append(c)
        for cell in cells.values():
            cell.sort(key=lambda c: c["station_key"])
        order = sorted(cells)
        chosen: list[dict] = []
        while len(chosen) < per_region and any(cells[k] for k in order):
            for key in order:
                if cells[key] and len(chosen) < per_region:
                    chosen.append(cells[key].pop(0))
        # dammed guarantee: swap within the same DA tertile
        def dammed(c):
            return (c.get("damnrmstor") or 0) > 0
        leftovers = [c for k in order for c in cells[k]]
        for cand in sorted((c for c in leftovers if dammed(c)),
                           key=lambda c: c["station_key"]):
            if sum(1 for c in chosen if dammed(c)) >= min_dammed:
                break
            victim = next((c for c in chosen if not dammed(c)
                           and tertile(c) == tertile(cand)), None)
            if victim is None:
                continue
            chosen[chosen.index(victim)] = cand
        for c in chosen:
            c = dict(c)
            c["da_tertile"] = tertile(c)
            c["urban"] = float(c["pctimp"]) >= URBAN_SPLIT_PCT
            c["dammed"] = dammed(c)
            out.append(c)
    return out


def deep_shift(points: list[dict], sc_value, engine_value) -> dict:
    """Index at the StreamCat value vs the engine value on one DEEP curve."""
    sys.path.insert(0, str(DEEP_ROOT))
    from deep import curves  # noqa: E402

    a = curves.interp_curve(points, float(sc_value)) if sc_value is not None else None
    b = curves.interp_curve(points, float(engine_value)) if engine_value is not None else None
    return {"streamcat_index": a, "engine_index": b,
            "shift": (round(b - a, 4) if a is not None and b is not None else None)}


def verdict(rating_agreement, class_agreement, deep_median_shift) -> dict:
    """The pre-registered rule, inclusive at the boundaries it names."""
    checks = {
        "rating_agreement": (rating_agreement is not None
                             and rating_agreement >= RULE["rating_agreement_min"]),
        "class_agreement": (class_agreement is not None
                            and class_agreement >= RULE["class_agreement_min"]),
        "deep_median_shift": (deep_median_shift is not None
                              and abs(deep_median_shift) < RULE["deep_median_shift_max"]),
    }
    return {"interchangeable": all(checks.values()), "checks": checks,
            "rule": dict(RULE)}


# --------------------------------------------------------------------------- #
# live pieces
# --------------------------------------------------------------------------- #
def _easi():
    if str(EASI_ROOT) not in sys.path:
        sys.path.insert(0, str(EASI_ROOT))
    from easi import assessment, delineation, pipeline, routing, scoring, watershed  # noqa
    from easi.datasources import streamcat  # noqa
    from easi.metrics import registry  # noqa
    return assessment, delineation, pipeline, routing, scoring, watershed, streamcat, registry


def load_candidates(regions: list[str]) -> list[dict]:
    import pandas as pd
    df = pd.read_parquet(STATIONS)
    df = df[df["us_l3code"].astype(str).isin(regions)]
    out = []
    for _, r in df.iterrows():
        comid = r.get("comid")
        try:
            comid = int(comid) if comid == comid and comid is not None else None
        except (TypeError, ValueError):
            comid = None
        out.append({"station_key": str(r["station_key"]),
                    "region": str(r["us_l3code"]), "lat": float(r["lat"]),
                    "lon": float(r["lon"]), "comid": comid})
    return sorted(out, key=lambda c: c["station_key"])


def enrich_candidates(cands: list[dict], cache: Path) -> list[dict]:
    """StreamCat impervious, dams and the V2 drainage area per candidate
    (cached, resumable)."""
    _a, delineation, _p, _r, _s, _w, streamcat, _reg = _easi()
    done = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    for c in cands:
        key = c["station_key"]
        if key in done:
            c.update(done[key])
            continue
        rec = {"da_sqkm": None, "pctimp": None, "damnrmstor": None, "damdens": None}
        if c.get("comid"):
            sc = streamcat.metrics_by_comid(c["comid"], ["pctimp2019", "damnrmstor", "damdens"])
            rec["pctimp"] = sc.get("pctimp2019ws")
            rec["damnrmstor"] = sc.get("damnrmstorws")
            rec["damdens"] = sc.get("damdensws")
            rec["da_sqkm"] = delineation.flowline_attrs(c["comid"]).get("drainage_area_sqkm")
        done[key] = rec
        c.update(rec)
        cache.write_text(json.dumps(done, indent=1, sort_keys=True), encoding="utf-8")
        print(f"  enriched {key}: DA {rec['da_sqkm']} imp {rec['pctimp']} "
              f"dams {rec['damnrmstor']}", flush=True)
    return cands


def _bundle(region: str, which: str) -> tuple[dict | None, dict]:
    folder = LIBRARY / REGIONS[region]
    catalog = json.loads((REPO / "apps" / "library" / "catalog.json").read_text(encoding="utf-8"))
    entry = next((a for a in catalog.get("assessments", [])
                  if a.get("id") == REGIONS[region] or
                  str(a.get("folder") or "").endswith(REGIONS[region])), None)
    version = None
    if entry:
        version = entry.get("latestVersion") if which == "latest" else entry.get("defaultVersion")
    if version is None:
        versions = sorted(int(p.name[1:]) for p in folder.glob("v*") if p.name[1:].isdigit())
        version = versions[-1] if versions else None
    if version is None:
        return None, {"region": region, "version": None}
    path = folder / f"v{version}" / "assessment.deep.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    return bundle, {"region": region, "version": version,
                    "contentDigest": bundle.get("contentDigest"),
                    "predictorSource": bundle.get("predictorSource", "streamcat")}


def _metric_specs(bundle: dict) -> dict:
    return {m["metricId"]: m for fn in bundle.get("metricsByFunction", [])
            for m in fn.get("metrics", [])}


def run_site(c: dict, *, budget: str) -> dict:
    """One site -> the cached record (always a ledger row)."""
    assessment, _d, pipeline, routing, scoring, watershed, streamcat, registry = _easi()
    from site_engine import compute_site
    from site_engine.provenance import DEFAULT_CONFIG, INTERACTIVE_CONFIG

    row = {"station_key": c["station_key"], "region": c["region"], "lat": c["lat"],
           "lon": c["lon"], "comid": c.get("comid"), "da_sqkm": c.get("da_sqkm"),
           "urban": c.get("urban"), "dammed": c.get("dammed"), "outcome": "ok",
           "reason": ""}
    t0 = time.time()
    delin = asyncio.run(pipeline.delineate_only(
        c["lat"], c["lon"], comid=c.get("comid"),
        watershed_engine=routing.POLICY_STREAMCAT_LEGACY))
    if delin.get("status") != "ok":
        row.update(outcome="delineation_failed", reason=delin.get("message", ""))
        return row
    ctx_inputs = delin.pop("ctx_inputs")
    a = asyncio.run(pipeline.assess_only(ctx_inputs, prefetch=False))
    report_a = a["report"]
    sc = streamcat.metrics_by_comid(ctx_inputs["comid"], list(registry.STREAMCAT_NAMES))
    row["secs_easi"] = round(time.time() - t0, 1)

    t1 = time.time()
    cfg = {**(INTERACTIVE_CONFIG if budget == "interactive" else DEFAULT_CONFIG),
           "includeGeometry": False, "metricFamilies": ENGINE_FAMILIES}
    rec = compute_site(ctx_inputs["lat"], ctx_inputs["lon"], cfg)
    row["secs_engine"] = round(time.time() - t1, 1)
    ws = rec.get("watershed") or {}
    row.update(engine_status=rec["status"], engine_reason=rec.get("reason") or "",
               n_reaches=ws.get("nReaches"), n_hops=ws.get("nHops"),
               engine_area_sqkm=ws.get("areaSqkm"), area_agreement=ws.get("areaAgreement"))
    if rec["status"] != "ok":
        row.update(outcome=f"engine_{rec['status']}", reason=rec.get("reason") or "")
        return row

    ctx_b = pipeline._ctx_from_inputs(ctx_inputs)
    ctx_b.extras["streamcat"] = sc
    ctx_b.extras["landcover"] = {}
    ctx_b.extras["watershedEngine"] = {"status": "ok", "record": rec,
                                       "engineVersion": rec.get("engineVersion")}
    ctx_b.extras["watershed"] = watershed.build(ctx_b, sc)
    ctx_b.extras["nrsa"] = None
    report_b = assessment.recompute_watershed_rows(report_a, ctx_b)

    rows_a = {r["metricId"]: r for r in report_a["metricRows"]}
    rows_b = {r["metricId"]: r for r in report_b["metricRows"]}
    metrics = []
    for mid in assessment.WATERSHED_METRIC_IDS:
        ra, rb = rows_a[mid], rows_b[mid]
        metrics.append({"metricId": mid, "streamcat_rating": ra["rating"],
                        "engine_rating": rb["rating"],
                        "streamcat_value": ra.get("valueText"),
                        "engine_value": rb.get("valueText"),
                        "agree": (ra["rating"] == rb["rating"]
                                  if ra["rating"] and rb["rating"] else None)})
    row["metrics"] = metrics
    row["eci_streamcat"] = report_a.get("ecosystemConditionIndex")
    row["eci_engine"] = report_b.get("ecosystemConditionIndex")
    row["class_streamcat"] = scoring.index_band_label(row["eci_streamcat"])
    row["class_engine"] = scoring.index_band_label(row["eci_engine"])
    row["class_agree"] = row["class_streamcat"] == row["class_engine"]
    row["streamcat_row"] = {k: sc.get(k) for k in
                            ("pctimp2019ws", "pctcrop2019ws", "pctwdwet2019ws",
                             "pcthbwet2019ws", "rddensws", "damdensws",
                             "damnrmstorws", "runoffws", "kffactws")}
    row["engine_values"] = {k: (rec["metrics"].get(v) or {}).get("value")
                            for _b, (_sc, v, _t) in DEEP_PAIRS.items()
                            for k in [v]}
    row["engine_values"]["runoffDepthMm"] = (rec["metrics"].get("runoffDepthMm") or {}).get("value")
    row["engine_values"]["soilKFactor"] = (rec["metrics"].get("soilKFactor") or {}).get("value")
    return row


def deep_rows(site: dict, specs: dict, meta: dict) -> list[dict]:
    out = []
    for bundle_id, (sc_col, engine_key, transform) in DEEP_PAIRS.items():
        spec = specs.get(bundle_id)
        if spec is None:
            continue
        from site_engine import provenance  # noqa: F401  (path already set)
        sys.path.insert(0, str(DEEP_ROOT))
        from deep import curves
        pts = curves.active_points(spec)
        sc_v = (site.get("streamcat_row") or {}).get(sc_col)
        ev = (site.get("engine_values") or {}).get(engine_key)
        ev = transform(float(ev)) if ev is not None else None
        shift = deep_shift(pts, sc_v, ev)
        out.append({"station_key": site["station_key"], "region": site["region"],
                    "bundle": f"{meta['region']} v{meta['version']}",
                    "metricId": bundle_id, "streamcat_value": sc_v,
                    "engine_value": ev, **shift})
    return out


def summarize(sites: list[dict], deep: list[dict]) -> dict:
    ok = [s for s in sites if s["outcome"] == "ok"]

    def agreement(rows, exclude_flow: bool):
        pairs = [m for s in rows for m in s.get("metrics", [])
                 if m["agree"] is not None
                 and not (exclude_flow and m["metricId"] == FLOW_ALTERATION_ID)]
        return (round(sum(1 for m in pairs if m["agree"]) / len(pairs), 4)
                if pairs else None, len(pairs))

    def class_agreement(rows):
        pairs = [s for s in rows if s.get("class_agree") is not None]
        return (round(sum(1 for s in pairs if s["class_agree"]) / len(pairs), 4)
                if pairs else None, len(pairs))

    def deep_median(rows):
        shifts = [abs(r["shift"]) for r in rows if r.get("shift") is not None]
        return (round(statistics.median(shifts), 4) if shifts else None, len(shifts))

    def block(rows, deep_subset):
        ra, n_ra = agreement(rows, False)
        ra_x, n_rax = agreement(rows, True)
        ca, n_ca = class_agreement(rows)
        dm, n_dm = deep_median(deep_subset)
        per_metric = {}
        for s in rows:
            for m in s.get("metrics", []):
                if m["agree"] is None:
                    continue
                d = per_metric.setdefault(m["metricId"], {"n": 0, "agree": 0})
                d["n"] += 1
                d["agree"] += int(m["agree"])
        return {"n_sites": len(rows), "rating_agreement": ra, "n_rating_pairs": n_ra,
                "rating_agreement_without_flow_alteration": ra_x,
                "n_rating_pairs_without_flow_alteration": n_rax,
                "class_agreement": ca, "n_class_pairs": n_ca,
                "deep_median_abs_shift": dm, "n_deep_pairs": n_dm,
                "per_metric": {k: {**v, "share": round(v["agree"] / v["n"], 3)}
                               for k, v in per_metric.items()},
                "verdict": verdict(ra, ca, dm),
                "verdict_without_flow_alteration": verdict(ra_x, ca, dm)}

    outcomes: dict[str, int] = {}
    for s in sites:
        outcomes[s["outcome"]] = outcomes.get(s["outcome"], 0) + 1
    summary = {"n_sites": len(sites), "n_ok": len(ok), "outcomes": outcomes,
               "pooled": block(ok, deep), "regions": {}}
    for region in sorted({s["region"] for s in sites}):
        summary["regions"][region] = block(
            [s for s in ok if s["region"] == region],
            [d for d in deep if d["region"] == region])
    return summary


def write_notes(summary: dict, meta: dict, path: Path) -> None:
    p = summary["pooled"]
    lines = [
        "# Score-level equivalence study, StreamCat lookup engine vs STAF site engine",
        "", f"Run {time.strftime('%Y-%m-%d')}. Bundles: "
        + ", ".join(f"{REGIONS[r]} v{m['version']}" for r, m in meta.items() if m.get("version")),
        "", f"Sites: {summary['n_sites']} in the panel, {summary['n_ok']} compared. "
        f"Outcomes: {summary['outcomes']}.", "",
        "| Measure | Pooled | Rule |", "|---|---|---|",
        f"| Watershed-metric rating agreement | {p['rating_agreement']} (n={p['n_rating_pairs']}) | >= {RULE['rating_agreement_min']} |",
        f"| Same, without flow alteration | {p['rating_agreement_without_flow_alteration']} (n={p['n_rating_pairs_without_flow_alteration']}) | |",
        f"| Condition-class agreement | {p['class_agreement']} (n={p['n_class_pairs']}) | >= {RULE['class_agreement_min']} |",
        f"| Median DEEP index shift (absolute) | {p['deep_median_abs_shift']} (n={p['n_deep_pairs']}) | < {RULE['deep_median_shift_max']} |",
        "", f"Verdict (pre-registered rule): interchangeable = {p['verdict']['interchangeable']}; "
        f"without flow alteration = {p['verdict_without_flow_alteration']['interchangeable']}.",
        "", "## Per metric", "", "| Metric | n | Agreement |", "|---|---|---|",
    ]
    for mid, v in sorted(p["per_metric"].items()):
        lines.append(f"| {mid} | {v['n']} | {v['share']} |")
    lines += ["", "## Per region", ""]
    for region, b in summary["regions"].items():
        lines.append(f"- {REGIONS.get(region, region)}: {b['n_sites']} sites, rating agreement "
                     f"{b['rating_agreement']}, class agreement {b['class_agreement']}, "
                     f"median DEEP shift {b['deep_median_abs_shift']}, verdict "
                     f"{b['verdict']['interchangeable']}")
    lines += ["", "Raw outputs: libs/site_engine/scripts/out/score_equivalence_*.csv and "
              "score_equivalence_study.json (gitignored)."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", choices=sorted(REGIONS), action="append")
    ap.add_argument("--per-region", type=int, default=15)
    ap.add_argument("--min-dammed", type=int, default=3)
    ap.add_argument("--max-da-sqkm", type=float, default=300.0)
    ap.add_argument("--budget", choices=("default", "interactive"), default="default")
    ap.add_argument("--bundle", choices=("default", "latest"), default="default")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="ignore per-site caches")
    ap.add_argument("--notes", action="store_true",
                    help="write the markdown summary under notes/")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    out_dir = Path(args.out)
    site_dir = out_dir / "score_equivalence"
    site_dir.mkdir(parents=True, exist_ok=True)
    regions = args.region or sorted(REGIONS)

    print("loading candidates ...", flush=True)
    cands = load_candidates(regions)
    cands = enrich_candidates(cands, out_dir / "score_equivalence_candidates.json")
    panel = select_panel(cands, per_region=args.per_region,
                         min_dammed=args.min_dammed, max_da_sqkm=args.max_da_sqkm)
    if args.limit:
        panel = panel[: args.limit]
    with (out_dir / "score_equivalence_panel.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for p in panel for k in p}), restval="")
        w.writeheader()
        w.writerows(panel)
    print(f"panel: {len(panel)} sites", flush=True)

    sites = []
    for c in panel:
        cache = site_dir / f"{c['station_key']}.json"
        if cache.exists() and not args.force:
            sites.append(json.loads(cache.read_text(encoding="utf-8")))
            continue
        print(f"site {c['station_key']} ({c['region']}) ...", flush=True)
        row = run_site(c, budget=args.budget)
        cache.write_text(json.dumps(row, indent=1, default=str), encoding="utf-8")
        print(f"  -> {row['outcome']} {row.get('reason', '')}", flush=True)
        sites.append(row)

    deep: list[dict] = []
    bundle_meta: dict[str, dict] = {}
    for region in regions:
        bundle, meta = _bundle(region, args.bundle)
        bundle_meta[region] = meta
        if bundle is None:
            continue
        specs = _metric_specs(bundle)
        for s in sites:
            if s["region"] == region and s["outcome"] == "ok":
                deep.extend(deep_rows(s, specs, meta))

    summary = summarize(sites, deep)
    summary["bundles"] = bundle_meta
    summary["budget"] = args.budget
    (out_dir / "score_equivalence_study.json").write_text(
        json.dumps({"summary": summary, "sites": sites, "deep": deep},
                   indent=1, default=str), encoding="utf-8")
    flat_sites = [{k: v for k, v in s.items()
                   if k not in ("metrics", "streamcat_row", "engine_values")} for s in sites]
    for name, rows in (("sites", flat_sites),
                       ("metrics", [{"station_key": s["station_key"], "region": s["region"], **m}
                                    for s in sites for m in s.get("metrics", [])]),
                       ("deep", deep)):
        if not rows:
            continue
        with (out_dir / f"score_equivalence_{name}.csv").open("w", newline="",
                                                             encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}), restval="")
            w.writeheader()
            w.writerows(rows)
    if args.notes:
        write_notes(summary, bundle_meta,
                    REPO / "notes" / "EASI_Report" / "analysis"
                    / f"score_equivalence_study_{time.strftime('%Y-%m')}.md")
    print("\n== summary ==")
    print(json.dumps({k: v for k, v in summary.items() if k != "regions"},
                     indent=2, default=str))
    for region, b in summary["regions"].items():
        print(f"{REGIONS.get(region, region)}: {json.dumps(b['verdict'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
