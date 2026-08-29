"""Validation study 1: derive the published DA-ratio refusal threshold (gate G2).

The routing policy ships with a provisional ``DA_RATIO_MAX`` of 10. This
harness produces the empirical evidence to set the final published value. Two
modes:

* Default (cheap, routing-only): sample HR-only reaches across regions, route
  each with the production policy, and report the drainage-area-ratio
  distribution plus the refusal rate at candidate thresholds. This shows how
  often each candidate bound would decline a real headwater click.

* ``--with-scoring`` (expensive, live metric runs): for sampled covered-network
  headwater reaches, score the reach AND its successive downstream mainstem
  reaches with the production batch engine, then report ECI condition-class
  agreement as a function of the DA ratio between them. The ratio where
  agreement degrades is the defensible threshold. Each pair costs two full
  20-metric runs against live services; budget minutes per pair.

Outputs to the gitignored out/ dir. Diagnostic harness — changes no production
code path. The published constant lives at ``easi.routing.DA_RATIO_MAX``; cite
this script's output when changing it.

Examples:
  python scripts/derive_da_ratio_threshold.py                    # ratio survey
  python scripts/derive_da_ratio_threshold.py --limit 3
  python scripts/derive_da_ratio_threshold.py --with-scoring --pairs 6
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from easi import routing                                  # noqa: E402
from easi.datasources import flowlines, nhd_hr            # noqa: E402

CANDIDATE_THRESHOLDS = (2, 5, 10, 20, 50)
D = 0.012

# Sample boxes across regions (reused from the probe's panel, trimmed).
SAMPLE_BOXES = [
    ("Ohio (Worthington)", 40.0962, -83.0203),
    ("Ohio (Waldo)", 40.30, -83.07),
    ("Mid-Atlantic (PA)", 40.80, -76.50),
    ("South Atlantic (GA)", 33.50, -83.30),
    ("Great Lakes (MI)", 43.50, -84.60),
    ("Ozarks (AR/OK)", 35.50, -94.50),
    ("Mountain West (CO)", 39.00, -107.50),
    ("Pacific Northwest (WA)", 46.90, -122.20),
]


def hr_only_points(blat: float, blon: float, max_per_box: int) -> list[tuple]:
    """Midpoints of HR flowlines with no V2 line within the snap tolerance."""
    v2 = flowlines.flowlines_in_bbox(blon - D, blat - D, blon + D, blat + D)
    hr = nhd_hr.hr_flowlines_in_bbox(blon - D, blat - D, blon + D, blat + D)
    out = []
    for f in (hr or {}).get("features") or []:
        coords = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "MultiLineString":
            coords = coords[0]
        mid = coords[len(coords) // 2]
        lon, lat = float(mid[0]), float(mid[1])
        v2_hit = flowlines.nearest_point_on_lines(v2, lat, lon)
        if v2_hit is None or v2_hit[2] > routing.HR_SNAP_TOL_FT:
            out.append((lat, lon))
            if len(out) >= max_per_box:
                break
    return out


def survey_ratios(limit_boxes: int | None, per_box: int) -> dict:
    ratios: list[float] = []
    outcomes: dict[str, int] = {}
    rows = []
    boxes = SAMPLE_BOXES[:limit_boxes] if limit_boxes else SAMPLE_BOXES
    for label, blat, blon in boxes:
        print(f"sampling {label} ...", flush=True)
        for lat, lon in hr_only_points(blat, blon, per_box):
            res = routing.resolve_anchor(lat, lon)
            anchor = res.get("anchor") or {}
            r = (anchor.get("routing") or {})
            ratio = r.get("daRatio")
            kind = ("refused:" + res.get("code", "") if res.get("refused")
                    else res.get("error") or anchor.get("anchorKind") or "?")
            outcomes[kind] = outcomes.get(kind, 0) + 1
            if ratio is not None:
                ratios.append(float(ratio))
            rows.append({"region": label, "lat": round(lat, 5),
                         "lon": round(lon, 5), "outcome": kind,
                         "da_ratio": ratio,
                         "routed_ft": r.get("routedDistanceFt")})
            print(f"  ({lat:.4f},{lon:.4f}) -> {kind} ratio={ratio}")
    refusal_table = {
        str(t): round(sum(1 for x in ratios if x > t) / len(ratios), 3)
        for t in CANDIDATE_THRESHOLDS} if ratios else {}
    return {
        "mode": "ratio-survey",
        "n_points": len(rows),
        "n_with_ratio": len(ratios),
        "ratio_median": round(statistics.median(ratios), 2) if ratios else None,
        "ratio_p90": round(sorted(ratios)[max(0, int(len(ratios) * 0.9) - 1)], 2)
        if ratios else None,
        "share_refused_at_threshold": refusal_table,
        "outcomes": outcomes,
        "rows": rows,
    }


def scoring_pairs(n_pairs: int) -> dict:
    """Score headwater reaches and their downstream mainstem with the batch
    engine; report class agreement vs DA ratio. EXPENSIVE (live services)."""
    from pynhd import NLDI, WaterData

    from easi.batch import api
    from easi.batch import contracts as C

    pairs = []
    for label, blat, blon in SAMPLE_BOXES:
        if len(pairs) >= n_pairs:
            break
        try:
            gdf = WaterData("nhdflowline_network").bybox(
                (blon - D, blat - D, blon + D, blat + D))
        except Exception:
            continue
        if gdf is None or gdf.empty or "streamorde" not in gdf.columns:
            continue
        heads = gdf[gdf["streamorde"] == 1]
        for _, row in heads.iterrows():
            if len(pairs) >= n_pairs:
                break
            comid = int(row["comid"])
            try:
                down = NLDI().navigate_byid(
                    fsource="comid", fid=str(comid),
                    navigation="downstreamMain", source="flowlines", distance=15)
            except Exception:
                continue
            if down is None or down.empty:
                continue
            down_ids = [int(c) for c in down.get("nhdplus_comid", down.get("comid", []))
                        if str(c).isdigit() and int(c) != comid]
            if down_ids:
                pairs.append((label, comid, down_ids[-1]))
    print(f"collected {len(pairs)} pairs; scoring with the batch engine ...")

    results = []
    for label, up, down in pairs:
        sites = [C.SiteRequest(f"UP-{up}", 0.0, 0.0, comid=up),
                 C.SiteRequest(f"DN-{down}", 0.0, 0.0, comid=down)]
        # lat/lon are unused when a comid is supplied; keep placeholders.
        batch = api.run_batch_sync(C.BatchRequest(sites=sites))
        by = {s.site_id: s for s in batch.sites}
        u, dwn = by.get(f"UP-{up}"), by.get(f"DN-{down}")
        if not u or not dwn or u.eci is None or dwn.eci is None:
            continue
        da_u = u.delineation.drainage_area_sqkm
        da_d = dwn.delineation.drainage_area_sqkm
        ratio = round(da_d / da_u, 2) if da_u and da_d else None
        from easi.scoring import index_band_label
        results.append({"region": label, "up_comid": up, "down_comid": down,
                        "da_ratio": ratio, "up_eci": u.eci, "down_eci": dwn.eci,
                        "same_class": index_band_label(u.eci)
                        == index_band_label(dwn.eci)})
        print(f"  {label}: ratio {ratio}, ECI {u.eci} vs {dwn.eci}, "
              f"same class: {results[-1]['same_class']}")
    by_bin: dict[str, list[bool]] = {}
    for r in results:
        if r["da_ratio"] is None:
            continue
        for t in CANDIDATE_THRESHOLDS:
            if r["da_ratio"] <= t:
                by_bin.setdefault(f"<= {t}", []).append(r["same_class"])
                break
        else:
            by_bin.setdefault(f"> {CANDIDATE_THRESHOLDS[-1]}", []).append(
                r["same_class"])
    return {
        "mode": "class-agreement",
        "n_pairs": len(results),
        "agreement_by_ratio_bin": {
            k: {"n": len(v), "same_class_share": round(sum(v) / len(v), 2)}
            for k, v in sorted(by_bin.items())},
        "rows": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=None,
                    help="ratio survey: only the first N sample boxes")
    ap.add_argument("--per-box", type=int, default=4)
    ap.add_argument("--with-scoring", action="store_true",
                    help="run the expensive class-agreement study")
    ap.add_argument("--pairs", type=int, default=8)
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    report = (scoring_pairs(args.pairs) if args.with_scoring
              else survey_ratios(args.limit, args.per_box))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = ("derive_da_ratio_scoring.json" if args.with_scoring
            else "derive_da_ratio_survey.json")
    (out_dir / name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k != "rows"}
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    print(f"report: {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
