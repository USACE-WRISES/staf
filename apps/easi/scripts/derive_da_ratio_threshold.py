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


# Ratio bins the threshold decision reads. The first run's 15 km walks put
# every pair above ratio 20; a ~4 km walk plus bin-aware collection fills the
# decision zone instead.
RATIO_BINS = ((1.0, 2.0), (2.0, 5.0), (5.0, 10.0), (10.0, 20.0),
              (20.0, float("inf")))


def _bin_of(ratio: float):
    for lo, hi in RATIO_BINS:
        if lo <= ratio < hi:
            return (lo, hi)
    return None


def scoring_pairs(n_pairs: int, walk_km: float = 4.0) -> dict:
    """Score headwater reaches and their downstream mainstem with the batch
    engine; report ECI class agreement as a function of the DA ratio.
    EXPENSIVE (live services): every unique reach is one full metric run.

    Pair collection is bin-aware: the ratio is estimated up front from the
    published drainage areas (the same numbers the routing policy uses), and a
    headwater contributes at most one pair, to the least-filled bin its
    downstream candidates can reach. Every pair gets a ledger row, including
    the ones that fail to score and why.
    """
    import math

    from pynhd import NLDI, WaterData

    from easi import delineation
    from easi.batch import api
    from easi.batch import contracts as C

    per_bin = max(1, math.ceil(n_pairs / len(RATIO_BINS)))
    bin_counts = {b: 0 for b in RATIO_BINS}
    pairs: list[tuple] = []          # (region, up_comid, down_comid, ratio_est)
    for label, blat, blon in SAMPLE_BOXES:
        if len(pairs) >= n_pairs:
            break
        try:
            gdf = WaterData("nhdflowline_network").bybox(
                (blon - D, blat - D, blon + D, blat + D))
        except Exception:  # noqa: BLE001
            continue
        if gdf is None or gdf.empty or "streamorde" not in gdf.columns:
            continue
        heads = gdf[gdf["streamorde"] == 1]
        for _, row in heads.iterrows():
            if len(pairs) >= n_pairs:
                break
            comid = int(row["comid"])
            up_da = row.get("totdasqkm")
            try:
                up_da = float(up_da)
            except (TypeError, ValueError):
                continue
            if not up_da or up_da <= 0:
                continue
            try:
                down = NLDI().navigate_byid(
                    fsource="comid", fid=str(comid),
                    navigation="downstreamMain", source="flowlines",
                    distance=max(1.0, float(walk_km)))
            except Exception:  # noqa: BLE001
                continue
            if down is None or down.empty:
                continue
            down_ids = [int(c) for c in down.get("nhdplus_comid",
                                                 down.get("comid", []))
                        if str(c).isdigit() and int(c) != comid]
            # Rank this headwater's candidates into the least-filled bin they
            # can serve; one pair per headwater keeps pairs independent.
            best = None
            for did in down_ids:
                d_da = delineation.flowline_attrs(did).get("drainage_area_sqkm")
                if not d_da or d_da <= up_da:
                    continue
                ratio = round(d_da / up_da, 2)
                b = _bin_of(ratio)
                if b is None or bin_counts[b] >= per_bin:
                    continue
                if best is None or bin_counts[b] < bin_counts[_bin_of(best[2])]:
                    best = (did, d_da, ratio)
            if best is not None:
                did, _d_da, ratio = best
                bin_counts[_bin_of(ratio)] += 1
                pairs.append((label, comid, did, ratio))
                print(f"  pair {label}: {comid} -> {did} ratio~{ratio}")
    print(f"collected {len(pairs)} pairs "
          f"(bins {[bin_counts[b] for b in RATIO_BINS]}); "
          "scoring with the batch engine ...")

    # One batch over the UNIQUE reaches: shared sites score once.
    unique = sorted({c for _, u, d, _r in pairs for c in (u, d)})
    sites = [C.SiteRequest(f"C-{c}", 0.0, 0.0, comid=c) for c in unique]
    by: dict[str, object] = {}
    for i in range(0, len(sites), 25):       # stay under the engine's cap
        batch = api.run_batch_sync(C.BatchRequest(sites=sites[i:i + 25]))
        by.update({s.site_id: s for s in batch.sites})

    results = []
    ledger = []
    for label, up, down, ratio_est in pairs:
        u, dwn = by.get(f"C-{up}"), by.get(f"C-{down}")
        if not u or u.eci is None:
            ledger.append({"region": label, "up_comid": up, "down_comid": down,
                           "ratio_est": ratio_est, "outcome": "up_unscored",
                           "reason": (u.issues[0].message if u and u.issues
                                      else "no result")})
            continue
        if not dwn or dwn.eci is None:
            ledger.append({"region": label, "up_comid": up, "down_comid": down,
                           "ratio_est": ratio_est, "outcome": "down_unscored",
                           "reason": (dwn.issues[0].message if dwn and dwn.issues
                                      else "no result")})
            continue
        da_u = u.delineation.drainage_area_sqkm
        da_d = dwn.delineation.drainage_area_sqkm
        ratio = round(da_d / da_u, 2) if da_u and da_d else ratio_est
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
        b = _bin_of(r["da_ratio"])
        if b is None:
            continue
        lo, hi = b
        key = f"{lo:g}-{hi:g}" if hi != float("inf") else f">= {lo:g}"
        by_bin.setdefault(key, []).append(r["same_class"])
    return {
        "mode": "class-agreement",
        "walk_km": None,   # filled by main()
        "n_pairs_collected": len(pairs),
        "n_pairs_scored": len(results),
        "agreement_by_ratio_bin": {
            k: {"n": len(v), "same_class_share": round(sum(v) / len(v), 2)}
            for k, v in sorted(by_bin.items())},
        "unscored_ledger": ledger,
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
    ap.add_argument("--walk-km", type=float, default=4.0,
                    help="class-agreement mode: how far downstream to collect "
                         "pair candidates; short walks keep pairs in the "
                         "decision-zone ratio bins")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()

    report = (scoring_pairs(args.pairs, args.walk_km) if args.with_scoring
              else survey_ratios(args.limit, args.per_box))
    if args.with_scoring:
        report["walk_km"] = args.walk_km
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
