"""Can a method put back a reference level that was hidden from it? (methodology 0.13)

The direct test. In a region that HAS least-disturbed stations, hide them, let a
method predict the reference anchors from what is left, and score the prediction
against the anchors that were hidden. That is the Eastern Corn Belt Plains
situation reproduced where the answer is known.

Two methods are tested beside the pooling bases already measured:

* **selected percentile** of the region's remaining, disturbed population. The
  pair of percentiles is calibrated on development regions and then frozen.
* **stressor-response**, metric modelled on natural setting and disturbance across
  the development regions and predicted at zero disturbance for the evaluation
  region. The specification is chosen on development data from ``SPECS`` and then
  frozen.

Rules are fixed before the evaluation runs:

* Regions are split into development and evaluation once, by seed, before any
  measurement (``basis_recovery.split_regions``).
* Percentiles and specifications are selected on development regions only.
* An evaluation region's own reference stations are never visible to the method
  that predicts them, and never enter the model's training data.
* Every prediction carries a bootstrap interval and an extrapolation share, and a
  recovery error is reported in units of the true reference interquartile range.

    py -3.12 scripts/run_recovery_test.py --out <folder>
    py -3.12 scripts/run_recovery_test.py --out <folder> --metric bent_EPT_NTAX
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

from streamcurves import basis_recovery as br     # noqa: E402
from streamcurves import pressure_evidence as pe  # noqa: E402

NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]


def cells_for(metric: str, frame: pd.DataFrame, series: pd.Series,
              codes: list[str], *, min_reference: int, min_blind: int) -> dict:
    """{region: {blind, truth, rows}} for one metric.

    ``blind`` is the region's non-reference values, which is all a method in a
    region like the Eastern Corn Belt Plains would ever see. ``truth`` is the
    anchors of the reference stations that were hidden.
    """
    out = {}
    for code in codes:
        reg = frame[frame["l3"].astype(str) == code]
        v = series.reindex(reg.index)
        ref = v[reg["pass_strict"].astype(bool)].dropna()
        blind = v[~reg["pass_strict"].astype(bool)].dropna()
        if len(ref) < min_reference or len(blind) < min_blind:
            continue
        truth = br.anchors_of(ref)
        if truth is None:
            continue
        out[code] = {"blind": blind, "truth": truth, "rows": reg,
                     "ref": ref, "n_ref": int(len(ref)), "n_blind": int(len(blind))}
    return out


def choose_spec(metric: str, frame: pd.DataFrame, series: pd.Series,
                dev: dict) -> dict:
    """Pick the specification on development regions, leaving each one out in turn.

    Scored by the same median absolute recovery error the evaluation reports, so
    the choice is made on the quantity that matters rather than on fit.
    """
    best = {"spec": None, "dev_err": None, "n_dev_cells": 0}
    for spec in br.SPECS:
        errs = []
        for code, cell in dev.items():
            others = [c for c in dev if c != code]
            if not others:
                continue
            train_ix = frame.index[frame["l3"].astype(str).isin(others)]
            train = frame.loc[train_ix].copy()
            train["__group"] = train["l3"].astype(str)
            y = series.reindex(train_ix)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = br.fit_stressor_response(train, y, NATURAL, spec=spec)
            if not model:
                continue
            pred = br.predict_reference(model, cell["rows"])
            got = br.recovery_error(br.anchors_of(pred), cell["truth"])
            if got["err_abs_mean"] is not None:
                errs.append(got["err_abs_mean"])
        if len(errs) >= 3:
            med = float(np.median(errs))
            if best["dev_err"] is None or med < best["dev_err"]:
                best = {"spec": spec, "dev_err": round(med, 4), "n_dev_cells": len(errs)}
    return best


def evaluate(metric: str, frame: pd.DataFrame, series: pd.Series, dev: dict, ev: dict,
             rule: dict, spec: dict, *, n_boot: int, seed: int) -> list[dict]:
    """Score both frozen methods on the held-out regions."""
    recs = []
    rng = np.random.default_rng(int(seed))
    dev_codes = list(dev)
    for code, cell in ev.items():
        base = {"metric": metric, "l3": code,
                "region": str(cell["rows"]["l3_name"].iloc[0]),
                "n_reference_hidden": cell["n_ref"], "n_blind": cell["n_blind"],
                "reference_share": round(float(cell["rows"]["pass_strict"].mean()), 4)}

        if rule.get("p_low") is not None:
            pred = br.percentile_anchors(cell["blind"], rule["p_low"], rule["p_high"])
            got = br.recovery_error(pred, cell["truth"])
            draws = []
            for _ in range(n_boot):
                s = cell["blind"].sample(len(cell["blind"]), replace=True,
                                         random_state=int(rng.integers(1 << 31)))
                e = br.recovery_error(
                    br.percentile_anchors(s, rule["p_low"], rule["p_high"]), cell["truth"])
                if e["err_abs_mean"] is not None:
                    draws.append(e["err_abs_mean"])
            recs.append({**base, "method": "selected_percentile",
                         "rule": f"p{rule['p_low']:.2f}-p{rule['p_high']:.2f}",
                         **got,
                         "err_lo": round(float(np.quantile(draws, 0.05)), 4) if draws else None,
                         "err_hi": round(float(np.quantile(draws, 0.95)), 4) if draws else None,
                         "extrapolation_ok": None})

        if spec.get("spec"):
            train_ix = frame.index[frame["l3"].astype(str).isin(dev_codes)]
            train = frame.loc[train_ix].copy()
            train["__group"] = train["l3"].astype(str)
            y = series.reindex(train_ix)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = br.fit_stressor_response(train, y, NATURAL, spec=spec["spec"])
            pred = br.predict_reference(model, cell["rows"]) if model else None
            got = br.recovery_error(br.anchors_of(pred), cell["truth"])
            share = br.extrapolation_share(train, cell["rows"], NATURAL)
            draws = []
            if model:
                for _ in range(max(1, n_boot // 4)):   # a refit per draw: costly
                    ix = train_ix.to_numpy()
                    take = rng.choice(len(ix), size=len(ix), replace=True)
                    tr = frame.loc[ix[take]].copy()
                    tr["__group"] = tr["l3"].astype(str)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        mb = br.fit_stressor_response(tr, series.reindex(tr.index),
                                                      NATURAL, spec=spec["spec"])
                    if not mb:
                        continue
                    e = br.recovery_error(
                        br.anchors_of(br.predict_reference(mb, cell["rows"])), cell["truth"])
                    if e["err_abs_mean"] is not None:
                        draws.append(e["err_abs_mean"])
            recs.append({**base, "method": "stressor_response",
                         "rule": spec["spec"], **got,
                         "err_lo": round(float(np.quantile(draws, 0.05)), 4) if draws else None,
                         "err_hi": round(float(np.quantile(draws, 0.95)), 4) if draws else None,
                         "extrapolation_ok": share})
    return recs


def run(out_dir: Path | None, *, n_boot: int, seed: int, min_reference: int,
        min_blind: int, only_metrics: list[str] | None, progress=None) -> dict:
    if progress is None:
        def progress(msg):
            print(msg, flush=True)

    inp = pe.national_inputs()
    frame, values, mc = inp["frame"], inp["values"], inp["metric_config"]
    wide = values.set_index(values["site_id"].astype(str))
    keys = frame["station_key"].astype(str)
    metrics = [m for m in mc if (only_metrics is None or m in only_metrics)
               and m in wide.columns]

    testable = [c for c, g in frame.groupby(frame["l3"].astype(str))
                if int(g["pass_strict"].sum()) >= min_reference and len(g) >= min_blind]
    dev_codes, ev_codes = br.split_regions(testable, seed=seed)
    progress(f"[recovery] {len(metrics)} metrics; {len(dev_codes)} development regions "
             f"{dev_codes}; {len(ev_codes)} evaluation regions {ev_codes}")

    records, rules = [], {}
    for i, metric in enumerate(metrics, start=1):
        series = pd.Series(keys.map(pd.to_numeric(wide[metric], errors="coerce")).to_numpy(),
                           index=frame.index)
        dev = cells_for(metric, frame, series, dev_codes,
                        min_reference=min_reference, min_blind=min_blind)
        ev = cells_for(metric, frame, series, ev_codes,
                       min_reference=min_reference, min_blind=min_blind)
        if len(dev) < 3 or not ev:
            progress(f"[recovery] {i}/{len(metrics)} {metric}: too few cells "
                     f"(dev {len(dev)}, eval {len(ev)})")
            continue
        rule = br.calibrate_percentiles(list(dev.values()))
        spec = choose_spec(metric, frame, series, dev)
        rules[metric] = {"percentile": rule, "model": spec,
                         "n_dev": len(dev), "n_eval": len(ev)}
        progress(f"[recovery] {i}/{len(metrics)} {metric}: percentiles "
                 f"{rule.get('p_low')}-{rule.get('p_high')} (dev err {rule.get('dev_err')}), "
                 f"spec {spec.get('spec')} (dev err {spec.get('dev_err')})")
        got = evaluate(metric, frame, series, dev, ev, rule, spec,
                       n_boot=n_boot, seed=seed)
        records.extend(got)
        if out_dir and records:
            out_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(records).to_csv(out_dir / "recovery.partial.csv", index=False)

    table = pd.DataFrame(records)
    summary = {"generated": date.today().isoformat(),
               "development_regions": dev_codes, "evaluation_regions": ev_codes,
               "seed": seed, "n_boot": n_boot, "n_records": len(records),
               "frozen_rules": rules}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "recovery.csv", index=False)
        (out_dir / "recovery.json").write_text(
            json.dumps(summary, indent=1, default=str), encoding="utf-8")
        progress(f"[recovery] wrote {out_dir / 'recovery.csv'}")
    return {"summary": summary, "table": table}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-boot", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--min-blind", type=int, default=20)
    ap.add_argument("--metric", action="append", default=None)
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, n_boot=a.n_boot, seed=a.seed,
              min_reference=a.min_reference, min_blind=a.min_blind,
              only_metrics=a.metric)
    print(json.dumps({k: v for k, v in got["summary"].items()
                      if k != "frozen_rules"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
