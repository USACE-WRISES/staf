"""Measure every candidate scoring basis against reference, per metric, per region.

Methodology 0.13's evidence pass. Methodology 0.12 fits a curve on least-disturbed
stations of the region or a comparable parent, and withholds the metric where no
level supports it. Fifteen of the 75 Level III ecoregions with a usable panel hold
no least-disturbed station at all and 37 are under 15 percent, so the question is
what else may anchor a curve there, and the answer has to be decided per metric on
measured agreement rather than per region on preference.

Every basis is judged the same way (``streamcurves.basis_validation``): how often
its curve changes a site's condition class against the reference-anchored curve,
and how wide an interval it can state on its own index. A basis that cannot state
one is refused however well it agrees.

Only regions that HAVE a reference pool can be measured, which is the point: the
admission thresholds are calibrated where a yardstick exists and then applied where
one does not. Nothing here changes a published assessment.

    py -3.12 scripts/run_basis_validation.py --out <evidence folder>
    py -3.12 scripts/run_basis_validation.py --out <folder> --metric chem_COND
    py -3.12 scripts/run_basis_validation.py --out <folder> --min-reference 15

Thresholds are set on the command line and recorded in the output, because an
admission rule chosen after seeing the table is not an admission rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_validation as bv          # noqa: E402
from streamcurves import pressure_evidence as pe         # noqa: E402
from streamcurves import reference_pool as rp            # noqa: E402

#: The naive comparator, kept because EASI's own rework kept it: scheme C_pop,
#: "a foil". Reading it beside the others is what makes the others meaningful.
ALL_SITES = "all_sites"
#: The region's own lowest-pressure fraction, which is REF-07's local comparison
#: promoted from a labelled comparison to a candidate basis.
REGIONAL_SCREEN = "regional_screen"
#: Comparable least-disturbed stations nationally, one rung past Level I.
NATIONAL_POOL = "national_pool"
#: The two within-region bases rebuilt with the reference stations removed.
#:
#: Without this the test is a tautology. A basis drawn from the target region
#: overlaps that region's reference set precisely where the reference set exists
#: (median 0.80 across the measurable regions, 1.00 in the cleanest), so it
#: agrees with the yardstick because it largely is the yardstick, and says
#: nothing about a region that has none. Removing the reference stations first
#: reproduces the Eastern Corn Belt Plains situation inside a region where the
#: answer is known.
REGIONAL_SCREEN_HOLDOUT = "regional_screen_holdout"
ALL_SITES_HOLDOUT = "all_sites_holdout"


def bases_for(metric: str, region: pd.DataFrame, national: pd.DataFrame,
              values: pd.Series, cfg: dict) -> tuple[dict, dict]:
    """(bases, clusters) for one metric in one region.

    ``values`` is indexed like ``national``. Each basis is the population its
    curve would be fitted on, so the comparison is like for like and the engine
    is untouched.
    """
    in_region = values.reindex(region.index).dropna()
    strict = region["pass_strict"].astype(bool)
    bases: dict[str, pd.Series] = {}
    clusters: dict[str, pd.Series] = {}

    ref = in_region[strict.reindex(in_region.index).fillna(False)]
    if len(ref) >= 5:
        bases[bv.REFERENCE_BASIS] = ref

    bases[ALL_SITES] = in_region

    picked, _how = rp.local_comparison_stations(region)
    screened = in_region[in_region.index.isin(picked.index)]
    if len(screened) >= 5:
        bases[REGIONAL_SCREEN] = screened

    # The same two bases, built as they would have to be if this region held no
    # least-disturbed station: the reference stations are dropped before the
    # screen runs, not after it picks.
    blind = region[~region["pass_strict"].astype(bool)]
    blind_vals = in_region[in_region.index.isin(blind.index)]
    if len(blind_vals) >= 5:
        bases[ALL_SITES_HOLDOUT] = blind_vals
        picked_blind, _how2 = rp.local_comparison_stations(blind)
        scr_blind = blind_vals[blind_vals.index.isin(picked_blind.index)]
        if len(scr_blind) >= 5:
            bases[REGIONAL_SCREEN_HOLDOUT] = scr_blind

    profile = rp.family_profile(metric)
    if profile:
        envelope = rp.envelope_for(region, profile.get("covariates") or [])
        liths = rp.target_lith_groups(region)
        pool = national[national["pass_strict"].astype(bool)]
        ok, _why = rp.comparable_mask(pool, envelope, liths,
                                      use_lithology=bool(profile.get("lithology")))
        nat = values.reindex(pool[ok].index).dropna()
        if len(nat) >= 5:
            bases[NATIONAL_POOL] = nat

    for name, series in bases.items():
        src = national if name == NATIONAL_POOL else region
        clusters[name] = src["huc12"].reindex(series.index)
    return bases, clusters


def run(out_dir: Path | None, *, n_boot: int, seed: int, accept: float,
        exploratory: float, min_reference: int, min_eval: int,
        only_metrics: list[str] | None, progress=None) -> dict:
    # Flushed, because a redirected stdout buffers and this run takes hours: the
    # first version told an observer nothing at all until it finished.
    if progress is None:
        def progress(msg):
            print(msg, flush=True)
    inputs = pe.national_inputs()
    frame, values_tbl, metric_config = (inputs["frame"], inputs["values"],
                                        inputs["metric_config"])
    wide = values_tbl.set_index(values_tbl["site_id"].astype(str))
    keys = frame["station_key"].astype(str)

    metrics = [m for m in metric_config
               if (only_metrics is None or m in only_metrics) and m in wide.columns]
    regions = [c for c, g in frame.groupby(frame["l3"].astype(str))
               if len(g) >= min_eval and int(g["pass_strict"].sum()) >= min_reference]
    total = len(metrics) * len(regions)
    progress(f"[basis] {len(metrics)} metrics x {len(regions)} regions with a yardstick "
             f"(>= {min_reference} reference stations, >= {min_eval} in frame), "
             f"{total} cells, about {total * 6 * n_boot:,} curve fits")

    records: list[dict] = []
    for i, l3 in enumerate(sorted(regions), start=1):
        region = frame[frame["l3"].astype(str) == l3]
        name = str(region["l3_name"].iloc[0])
        progress(f"[basis] region {i}/{len(regions)}  L3 {l3} {name} "
                 f"({int(region['pass_strict'].sum())} of {len(region)} least disturbed)")
        for metric in metrics:
            series = pd.Series(
                keys.map(pd.to_numeric(wide[metric], errors="coerce")).to_numpy(),
                index=frame.index)
            bases, clusters = bases_for(metric, region, frame, series, metric_config[metric])
            if bv.REFERENCE_BASIS not in bases:
                continue
            evald = series.reindex(region.index).dropna()
            if len(evald) < min_eval:
                continue
            got = bv.compare_bases(bases, metric_config[metric], evald,
                                   clusters=clusters, n_boot=n_boot, seed=seed)
            for rec in got:
                rec["region"] = name
                rec["l3"] = l3
                rec["metric"] = metric
                rec["reference_share"] = round(
                    float(region["pass_strict"].mean()), 4)
                rec["verdict"] = bv.verdict(rec, accept=accept, exploratory=exploratory)
                rec.pop("calls", None)
            records.extend(got)
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            bv.summarize(records).to_csv(out_dir / "basis_validation.partial.csv",
                                         index=False)
            progress(f"[basis]   {len(records)} records so far")

    table = bv.summarize(records)
    thresholds = {"accept": accept, "exploratory": exploratory, "n_boot": n_boot,
                  "seed": seed, "min_reference": min_reference, "min_eval": min_eval}
    summary = {"generated": date.today().isoformat(), "thresholds": thresholds,
               "n_records": len(records), "n_regions": len(regions),
               "n_metrics": len(metrics),
               "verdicts_by_basis": (
                   table.groupby(["basis", "verdict"]).size().unstack(fill_value=0)
                   .to_dict(orient="index") if len(table) else {})}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "basis_validation.csv", index=False)
        (out_dir / "basis_validation.json").write_text(
            json.dumps({**summary, "records": records}, indent=1, default=str),
            encoding="utf-8")
        progress(f"[basis] wrote {out_dir / 'basis_validation.csv'}")
    return {"summary": summary, "table": table, "records": records}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="evidence folder (table and summary)")
    ap.add_argument("--n-boot", type=int, default=200,
                    help="resampling depth for the uncertainty interval")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--accept", type=float, default=0.045,
                    help="band-flip rate at or below which a basis is admissible; the "
                         "scale analysis uses 0.045 for a class split")
    ap.add_argument("--exploratory", type=float, default=0.15,
                    help="band-flip rate at or below which a basis is exploratory only")
    ap.add_argument("--min-reference", type=int, default=12,
                    help="reference stations a region needs to serve as a yardstick")
    ap.add_argument("--min-eval", type=int, default=25,
                    help="in-frame stations a region needs to be worth measuring on")
    ap.add_argument("--metric", action="append", default=None,
                    help="restrict to one metric (repeatable)")
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, n_boot=a.n_boot, seed=a.seed,
              accept=a.accept, exploratory=a.exploratory,
              min_reference=a.min_reference, min_eval=a.min_eval,
              only_metrics=a.metric)
    print(json.dumps(got["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
