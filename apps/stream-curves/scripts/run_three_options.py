"""Three options for a missing reference curve, judged on one protocol.

Option 1A  a selected percentile of all relevant observations in the region
Option 1B  a stressor-response model predicted at a realistic low-disturbance setting
Option 2   a relaxed but explicit regional pressure screen
Option 3   a national pool of ecologically comparable reference streams

Every option is scored the same way, on the same evaluation cells, against the
criteria fixed in PREREGISTRATION.md before this was run:

  C1  both recovered anchors inside the 90 percent bootstrap interval of the true
      anchors, in at least two thirds of a metric's evaluation regions
  C2  net optimism at most 0.05, where net optimism is the share of the region's
      sites the candidate curve places in a BETTER condition class minus the share
      it places in a worse one
  C3  for a predicting method, an extrapolation share of at least 0.25

Leakage control. One row per station. Resampling and splitting by HUC12. Regions
split into development and evaluation by seed before any measurement. For Option 3
the target region's own reference streams are removed from the donor pool, because
a low overlap is not the same as an independent test.

    py -3.12 scripts/run_three_options.py --out <folder>
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br          # noqa: E402
from streamcurves import basis_validation as bv        # noqa: E402
from streamcurves import pressure_evidence as pe       # noqa: E402
from streamcurves import reference_pool as rp          # noqa: E402

NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
FLOOR_EXPLORATORY = 10          # DATA-05
#: Screens Option 2 may use, loosest last. "strict" is the published screen and is
#: the thing being replaced, so it is not a candidate.
SCREEN_LADDER = ["relaxed", "point_source_strict", "agriculture_50", "agriculture_75"]


def low_disturbance_target(national: pd.DataFrame) -> dict:
    """A realistic low-disturbance pressure vector, not a vector of zeros.

    Setting every pressure to zero asks the model for a combination that may never
    occur. The median pressure vector of the nationally screened reference
    population is an observed combination and is what the published screen already
    calls least disturbed, so it is the target the prediction is made at.
    """
    ref = national[national["pass_strict"].astype(bool)]
    out = {}
    for c in br.PRESSURE_COLUMNS:
        if c in ref.columns:
            out[c] = float(pd.to_numeric(ref[c], errors="coerce").median())
    return out


def predict_at(model: dict, rows: pd.DataFrame, target: dict):
    """Predicted metric for each site with pressures held at ``target``."""
    if not model:
        return None
    X = br._design(rows, model["natural"]).copy()
    for c in model["pressure"]:
        X[c] = float(np.log1p(max(0.0, target.get(c, 0.0))))
    X = X.replace([np.inf, -np.inf], np.nan)
    keep = X.dropna().index
    if not len(keep):
        return None
    try:
        pred = model["fit"].predict(X.loc[keep])
    except Exception:
        return None
    return pd.Series(np.asarray(pred, dtype=float), index=keep)


def option2_pool(reg: pd.DataFrame, values: pd.Series) -> tuple:
    """The first screen in the ladder that reaches the exploratory floor, drawn from
    stations that do NOT pass the strict screen.

    The exclusion is what makes this a test. Measured across the evaluation regions,
    a relaxed pool that keeps the strict-pass stations is 98 percent strict reference
    at the median and 100 percent in four of nine regions: it would be scored against
    itself and would appear to recover the anchors perfectly. A region like the
    Eastern Corn Belt Plains has no strict-pass station to keep, so removing them here
    reproduces the situation the option is being proposed for.
    """
    eligible = reg[~reg["pass_strict"].astype(bool)]
    for name in SCREEN_LADDER:
        mask = br.apply_screen(eligible, name)
        got = values.reindex(eligible.index)[mask].dropna()
        if len(got) >= FLOOR_EXPLORATORY:
            return name, got, br.screen_residual(eligible, mask)
    return None, None, None


def option3_pool(metric: str, reg: pd.DataFrame, national: pd.DataFrame,
                 values: pd.Series) -> tuple:
    """Comparable national reference donors, with the target region's own reference
    streams removed so the test is independent rather than merely low-overlap."""
    profile = rp.family_profile(metric)
    if not profile:
        return None, None
    envelope = rp.envelope_for(reg, profile.get("covariates") or [])
    liths = rp.target_lith_groups(reg)
    pool = national[national["pass_strict"].astype(bool)]
    pool = pool[~pool.index.isin(reg.index)]          # the exclusion that matters
    ok, _ = rp.comparable_mask(pool, envelope, liths,
                               use_lithology=bool(profile.get("lithology")))
    donors = pool[ok]
    got = values.reindex(donors.index).dropna()
    return (donors, got) if len(got) >= 5 else (donors, None)


def evaluate_cell(metric: str, cfg: dict, reg: pd.DataFrame, national: pd.DataFrame,
                  values: pd.Series, rule: dict, spec: dict, dev_codes: list[str],
                  target: dict, *, n_boot: int, seed: int) -> list[dict]:
    ref = values.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
    blind = values.reindex(reg.index)[~reg["pass_strict"].astype(bool)].dropna()
    evald = values.reindex(reg.index).dropna()
    truth = br.anchors_of(ref)
    if truth is None or len(evald) < 10:
        return []
    interval = br.anchor_interval(ref, clusters=reg.loc[ref.index, "huc12"],
                                  n_boot=300, seed=seed)
    ref_pts, _ = bv.curve_for(ref, cfg)
    base = {"metric": metric, "l3": str(reg["l3"].iloc[0]),
            "region": str(reg["l3_name"].iloc[0]), "units": cfg.get("units", ""),
            "n_reference_hidden": int(len(ref)), "n_blind": int(len(blind)),
            "true_q25": round(truth[0], 4), "true_q75": round(truth[1], 4),
            "true_q25_lo": None if not interval["q25"] else round(interval["q25"][0], 4),
            "true_q25_hi": None if not interval["q25"] else round(interval["q25"][1], 4)}

    out = []

    def _curve_from_values(anchors, cfg_):
        """A curve whose q25 and q75 are the recovered anchors, built by the shipping
        engine from a synthetic sample carrying exactly those quartiles, so the shape
        and the tails come from the same code a published curve uses. One estimated
        anchor pair does not by itself make a curve: the rest is the engine's seed
        geometry, unchanged."""
        if not anchors:
            return None
        lo, hi = anchors
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return None
        span = hi - lo
        sample = np.concatenate([np.linspace(lo - span, lo, 25),
                                 np.linspace(lo, hi, 50),
                                 np.linspace(hi, hi + span, 25)])
        pts, _ = bv.curve_for(pd.Series(sample), cfg_)
        return pts

    def record(option, detail, anchors, extra=None):
        pts = _curve_from_values(anchors, cfg)
        err = br.recovery_error(anchors, truth)
        cont = br.contains(anchors, interval)
        sc = br.score_outcomes(ref_pts, pts, evald)
        rec = {**base, "option": option, "detail": detail,
               "pred_q25": None if not anchors else round(anchors[0], 4),
               "pred_q75": None if not anchors else round(anchors[1], 4),
               "err_native_q25": None if not anchors else round(anchors[0] - truth[0], 4),
               "err_native_q75": None if not anchors else round(anchors[1] - truth[1], 4),
               **err, **cont, **sc}
        rec.update(extra or {})
        out.append(rec)

    # ---- option 1A: selected percentile of the region's own observations -----
    if rule.get("p_low") is not None and len(blind) >= 5:
        record("1A_percentile", f"p{rule['p_low']:.2f}-p{rule['p_high']:.2f}",
               br.percentile_anchors(blind, rule["p_low"], rule["p_high"]),
               {"n_fit": int(len(blind)), "target_estimated": "best available in region"})

    # ---- option 1B: stressor-response at a realistic low-disturbance setting --
    if spec.get("spec"):
        train_ix = national.index[national["l3"].astype(str).isin(dev_codes)]
        train = national.loc[train_ix].copy()
        train["__group"] = train["l3"].astype(str)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = br.fit_stressor_response(train, values.reindex(train_ix), NATURAL,
                                             spec=spec["spec"])
        pred = predict_at(model, reg, target) if model else None
        share = br.extrapolation_share(train, reg, NATURAL)
        record("1B_stressor_response", spec["spec"], br.anchors_of(pred),
               {"n_fit": int(model["n_train"]) if model else 0,
                "extrapolation_ok": share,
                "target_estimated": "modelled least disturbed"})

    # ---- option 2: a relaxed but explicit regional screen ---------------------
    name, pool, resid = option2_pool(reg, values)
    if pool is not None:
        record("2_relaxed_screen", name, br.anchors_of(pool),
               {"n_fit": int(len(pool)),
                "screen_ag_median": (resid or {}).get("agriculture_ws_median"),
                "target_estimated": "least disturbed available under stated thresholds"})
    else:
        out.append({**base, "option": "2_relaxed_screen", "detail": "no screen reaches the floor",
                    "n_fit": 0, "target_estimated": "n/a"})

    # ---- option 3: comparable national donors, target region excluded --------
    donors, dvals = option3_pool(metric, reg, national, values)
    if dvals is not None:
        record("3_national_pool", f"{len(dvals)} donors",
               br.anchors_of(dvals),
               {"n_fit": int(len(dvals)),
                "n_donor_huc12": int(donors.loc[dvals.index, "huc12"].nunique()),
                "target_estimated": "least disturbed, transferred"})
    else:
        out.append({**base, "option": "3_national_pool", "detail": "no comparable donors",
                    "n_fit": 0 if dvals is None else len(dvals), "target_estimated": "n/a"})
    return out


def run(out_dir: Path | None, *, n_boot: int, seed: int, min_reference: int,
        only_metrics: list[str] | None, progress=None) -> dict:
    if progress is None:
        def progress(msg):
            print(msg, flush=True)
    inp = pe.national_inputs()
    frame, values_tbl, mc = inp["frame"], inp["values"], inp["metric_config"]
    wide = values_tbl.set_index(values_tbl["site_id"].astype(str))
    keys = frame["station_key"].astype(str)
    metrics = [m for m in mc if (only_metrics is None or m in only_metrics)
               and m in wide.columns]
    testable = [c for c, g in frame.groupby(frame["l3"].astype(str))
                if int(g["pass_strict"].sum()) >= min_reference and len(g) >= 25]
    dev_codes, ev_codes = br.split_regions(testable, seed=seed)
    target = low_disturbance_target(frame)
    progress(f"[three] {len(metrics)} metrics, {len(dev_codes)} development regions, "
             f"{len(ev_codes)} evaluation regions")
    progress(f"[three] low-disturbance target (median of the screened national pool): "
             + ", ".join(f"{k}={v:g}" for k, v in target.items()))

    records, frozen = [], {}
    for i, metric in enumerate(metrics, start=1):
        series = pd.Series(keys.map(pd.to_numeric(wide[metric], errors="coerce")).to_numpy(),
                           index=frame.index)
        dev_cells = []
        for code in dev_codes:
            reg = frame[frame["l3"].astype(str) == code]
            r = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
            b = series.reindex(reg.index)[~reg["pass_strict"].astype(bool)].dropna()
            t = br.anchors_of(r)
            if t and len(b) >= 10:
                dev_cells.append({"blind": b, "truth": t, "rows": reg})
        if len(dev_cells) < 3:
            progress(f"[three] {i}/{len(metrics)} {metric}: too few development cells")
            continue
        rule = br.calibrate_percentiles(dev_cells)
        from run_recovery_test import choose_spec
        dev_map = {c: {"rows": frame[frame["l3"].astype(str) == c]} for c in dev_codes}
        spec = choose_spec(metric, frame, series,
                           {c: {"rows": v["rows"],
                                "truth": br.anchors_of(
                                    series.reindex(v["rows"].index)[
                                        v["rows"]["pass_strict"].astype(bool)].dropna())}
                            for c, v in dev_map.items()
                            if br.anchors_of(series.reindex(v["rows"].index)[
                                v["rows"]["pass_strict"].astype(bool)].dropna())})
        frozen[metric] = {"percentile": rule, "model": spec}
        progress(f"[three] {i}/{len(metrics)} {metric}: p{rule.get('p_low')}-"
                 f"{rule.get('p_high')}, spec {spec.get('spec')}")
        for code in ev_codes:
            reg = frame[frame["l3"].astype(str) == code]
            records.extend(evaluate_cell(metric, mc[metric], reg, frame, series,
                                         rule, spec, dev_codes, target,
                                         n_boot=n_boot, seed=seed))
        if out_dir and records:
            out_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(records).to_csv(out_dir / "three_options.partial.csv", index=False)

    table = pd.DataFrame(records)
    summary = {"generated": date.today().isoformat(), "seed": seed,
               "development_regions": dev_codes, "evaluation_regions": ev_codes,
               "low_disturbance_target": target, "n_records": len(records),
               "frozen_rules": frozen}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "three_options.csv", index=False)
        (out_dir / "three_options.json").write_text(
            json.dumps(summary, indent=1, default=str), encoding="utf-8")
        progress(f"[three] wrote {out_dir / 'three_options.csv'}")
    return {"summary": summary, "table": table}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-boot", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--metric", action="append", default=None)
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, n_boot=a.n_boot, seed=a.seed,
              min_reference=a.min_reference, only_metrics=a.metric)
    print(json.dumps({k: v for k, v in got["summary"].items() if k != "frozen_rules"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
