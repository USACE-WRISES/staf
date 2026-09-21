"""Option 1 improved, on Pre-registration II: a selected percentile and
stressor-response models, in the two regimes the targets actually pose.

  1A_percentile  the percentile pair of the non-reference population that best
                 reproduced reference on the development regions
  1B_<spec>      metric on natural setting and disturbance, predicted at the
                 national reference pressure vector

Round one's four model defects are corrected here. The response is transformed by
the rule fixed in Pre-registration II; the specification is chosen at the same
prediction point it is scored at; a mixed fit's region intercept is estimated from
the region's own remaining stations and added at prediction; and every candidate
states a resampling interval by refitting.

Two regimes, because the two targets fail differently:

  I  the region's reference stations are withheld. Interior Plateau's case, where
     low-disturbance streams exist and are merely too few. The specification is
     selected on the development regions and scored on the evaluation regions.
  E  the reference stations are withheld AND every local station below the Eastern
     Corn Belt Plains minimum of agricultural cover. ECBP's case, where a
     prediction at reference disturbance is an extrapolation of about 30 points of
     cropland. No selection: two specifications are fixed in advance, so every
     testable region that stays evaluable can be used. Measured beforehand, that is
     four regions, which is the minimum a verdict may rest on.

    py -3.12 scripts/run_model_test.py --out <folder> --prereg <file> --jobs 10

Protocol IV (``--protocol iv``, Pre-registration IV, methodology 0.14) tests the
one specification the model registry runs, ``mixed_local``, exactly as a
production build fits it (``modeled_reference.training_frame``: every other
ecoregion, plus the target's own non-reference stations). With nothing selected
there is no development split, so every testable region is an evaluation cell in
Regime I, and Regime E is kept as Pre-registration II defined it. The metrics are
every metric a build can borrow for, reserve candidates included.

    py -3.12 scripts/run_model_test.py --protocol iv --out <folder> --prereg <file> --jobs 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamcurves import basis_recovery as br          # noqa: E402
from streamcurves import pressure_evidence as pe       # noqa: E402
from streamcurves import reference_pool as rp          # noqa: E402

NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
METRICS = ["bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND", "bent_TOTLNTAX",
           "fish_NAT_TOTLNTAX", "fish_NAT_NTOLNTAX", "chem_CHLA", "chem_COND",
           "chem_NTL", "chem_PH", "chem_PTL", "chem_TURB"]
TARGETS = {"55": METRICS, "71": ["bent_TOLRPIND", "bent_TOTLNTAX", "chem_NTL"]}
#: Regime E's fixed specifications, chosen for what they are rather than by a
#: result: the one that carries a local level, and its contrast without one.
REGIME_E_SPECS = ("mixed_local", "linear")
#: The region whose disturbance floor defines Regime E.
REGIME_E_TARGET = "55"
MIN_LOCAL_E = 10                       # DATA-05
N_BOOT_TRUTH, N_BOOT_POP, N_BOOT_MODEL, N_DRAWS, N_RESID = 300, 60, 20, 100, 200

_STATE: dict = {}


def _init(protocol: str = "ii") -> None:
    warnings.simplefilter("ignore")
    if protocol == "iv":
        # Protocol IV reads the production frame (the governed wadeable frame)
        from run_hierarchy_test import production_inputs
        inp = production_inputs()
    else:
        inp = pe.national_inputs()
    frame, values = inp["frame"], inp["values"]
    _STATE.update({"frame": frame, "mc": inp["metric_config"],
                   "wide": values.set_index(values["site_id"].astype(str)),
                   "keys": frame["station_key"].astype(str),
                   "l3": frame["l3"].astype(str),
                   "target_vector": br.reference_pressure_vector(frame)})
    ecbp = frame[frame["l3"].astype(str) == REGIME_E_TARGET]
    _STATE["threshold"] = float(pd.to_numeric(ecbp["agriculture_ws"], errors="coerce").min())


def _series(metric: str) -> pd.Series:
    s = _STATE
    return pd.Series(s["keys"].map(pd.to_numeric(s["wide"][metric], errors="coerce")).to_numpy(),
                     index=s["frame"].index)


def _rng(*parts, seed):
    return np.random.default_rng(br.cell_seed(*parts, seed=seed))


def retained(reg: pd.DataFrame, regime: str) -> pd.DataFrame:
    """The region's own stations a regime leaves in the training data: its
    non-reference stations, and in Regime E only those as agricultural as the
    Eastern Corn Belt Plains' cleanest stream."""
    nonref = reg[~reg["pass_strict"].astype(bool)]
    if regime != "E":
        return nonref
    ag = pd.to_numeric(nonref["agriculture_ws"], errors="coerce")
    return nonref[ag >= _STATE["threshold"]]


def training_rows(code: str, regime: str, ev: list[str]) -> pd.DataFrame:
    """Every in-frame station outside the evaluation regions and outside this
    region, plus what the regime leaves of the region's own. The target is never
    trained on its own reference stations."""
    frame, l3 = _STATE["frame"], _STATE["l3"]
    outside = frame[~l3.isin(ev) & (l3 != str(code))]
    return pd.concat([outside, retained(frame[l3 == str(code)], regime)])


def fit_for(code: str, regime: str, ev: list[str], series: pd.Series, spec: str, *,
            kind: str, offset: float, rows: pd.DataFrame | None = None):
    train = rows if rows is not None else training_rows(code, regime, ev)
    y = series.reindex(train.index)
    keep = y.notna().to_numpy()
    train = train[keep]
    model = br.fit_model(train, y[keep].to_numpy(), NATURAL, spec=spec, kind=kind,
                         offset=offset, groups=train["l3"].astype(str))
    return model, train


def run_metric(metric: str, testable: list[str], dev: list[str], ev: list[str],
               regime_e: list[str], seed: int) -> tuple[list[dict], list[dict], dict, float]:
    t0 = time.time()
    frame, l3 = _STATE["frame"], _STATE["l3"]
    cfg = _STATE["mc"][metric]
    tvec = _STATE["target_vector"]
    series = _series(metric)
    kind = br.transform_for(metric)
    offset = br.log_offset(series)
    records: list[dict] = []

    def cell(code: str, regime: str):
        reg = frame[l3 == str(code)]
        ref = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
        evald = series.reindex(reg.index).dropna()
        truth = br.cell_truth(ref, evald, cfg, clusters=reg["huc12"], n_boot=N_BOOT_TRUTH,
                              seed=br.cell_seed(metric, code, "truth", seed=seed))
        return reg, truth

    # ---- 1A: the percentile rule, calibrated on the development regions -------
    dev_cells = []
    for code in dev:
        reg = frame[l3 == code]
        r = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
        b = series.reindex(reg.index)[~reg["pass_strict"].astype(bool)].dropna()
        t = br.anchors_of(r)
        if t and len(b) >= 10:
            dev_cells.append({"blind": b, "truth": t})
    rule = br.calibrate_percentiles(dev_cells) if len(dev_cells) >= 3 else {"p_low": None}

    # ---- 1B: the specification, chosen on the development regions ------------
    def dev_error(spec: str, code: str):
        reg = frame[l3 == code]
        t = br.anchors_of(series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna())
        if not t:
            return None
        model, _ = fit_for(code, "I", ev, series, spec, kind=kind, offset=offset)
        got = br.model_anchors(model, reg, tvec, group=code, n_resid=N_RESID,
                               rng=_rng(metric, code, spec, "select", seed=seed))
        return br.recovery_error(got["anchors"], t)["err_abs_mean"]

    chosen = br.select_by_leave_one_out(br.MODEL_SPECS, dev, dev_error)
    spec_i = chosen["best"]

    # ---- evaluation ----------------------------------------------------------
    plan = [(code, "I", ["1A", spec_i]) for code in ev]
    plan += [(code, "E", list(REGIME_E_SPECS)) for code in regime_e]
    for code, regime, what in plan:
        reg, truth = cell(code, regime)
        if truth is None:
            continue
        # the gap over the region as it stands, and over what the regime actually
        # left the model to learn from, which in Regime E is the ECBP-like reach
        kept = {f"kept_{k}": v for k, v in br.disturbance_gap(retained(reg, regime), tvec).items()}
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "role": "eval" if code in ev else "dev", "regime": regime,
                "units": cfg.get("units", ""), "transform": kind,
                **br.disturbance_gap(reg, tvec), **kept}
        for item in what:
            if item is None:
                continue
            if item == "1A":
                blind = series.reindex(reg.index)[~reg["pass_strict"].astype(bool)].dropna()
                if rule.get("p_low") is None or len(blind) < 5:
                    records.append({**base, "basis": "1A_percentile", "kind": "population",
                                    "n_fit": int(len(blind)), "detail": "no rule or too few"})
                    continue
                anchors = br.percentile_anchors(blind, rule["p_low"], rule["p_high"])
                iv = br.cluster_bootstrap(
                    lambda pos: br.percentile_anchors(blind.iloc[pos], rule["p_low"],
                                                      rule["p_high"]),
                    reg.loc[blind.index, "huc12"], n_boot=N_BOOT_POP,
                    rng=_rng(metric, code, "1A", regime, seed=seed))
                records.append({**base, "basis": "1A_percentile", "kind": "population",
                                "n_fit": int(len(blind)),
                                "detail": f"p{rule['p_low']:.2f}-p{rule['p_high']:.2f}",
                                **br.candidate_record(truth, anchors=anchors,
                                                      n_fit=len(blind), interval=iv,
                                                      n_draws=N_DRAWS)})
                continue
            model, train = fit_for(code, regime, ev, series, item, kind=kind, offset=offset)
            got = br.model_anchors(model, reg, tvec, group=code, n_resid=N_RESID,
                                   rng=_rng(metric, code, item, regime, seed=seed))
            fitted = br.model_anchors(model, reg, tvec, group=code,
                                      fitted_only=True)["anchors"] if model else None
            cov = br.coverage_by_covariate(train, reg, NATURAL)
            boot_rng = _rng(metric, code, item, regime, "draws", seed=seed)

            def refit(pos, _item=item, _train=train, _reg=reg, _code=code):
                m, _ = fit_for(_code, regime, ev, series, _item, kind=kind, offset=offset,
                               rows=_train.iloc[pos])
                return br.model_anchors(m, _reg, tvec, group=_code, rng=boot_rng,
                                        n_resid=N_RESID)["anchors"]

            iv = br.cluster_bootstrap(refit, train["huc12"], n_boot=N_BOOT_MODEL,
                                      rng=_rng(metric, code, item, regime, "iv", seed=seed)) \
                if got["anchors"] else None
            fields = {"basis": f"1B_{item}", "kind": "model", "spec": item,
                      "n_fit": int(model["n_train"]) if model else 0, "n_pred": got["n_pred"],
                      "n_local_train": int(len(retained(reg, regime))),
                      "blup": got["blup"],
                      "extrapolation_ok": br.extrapolation_share(train, reg, NATURAL),
                      "coverage_no_climate": cov.get("joint_no_climate"),
                      "fitted_q25": None if not fitted else round(fitted[0], 4),
                      "fitted_q75": None if not fitted else round(fitted[1], 4),
                      "selected_on_dev": item == spec_i}
            if got["anchors"] is None:
                records.append({**base, **fields, "detail": "no anchors"})
                continue
            records.append({**base, **fields,
                            **br.candidate_record(truth, anchors=got["anchors"], n_fit=None,
                                                  interval=iv, n_draws=N_DRAWS)})

    # ---- what each basis would propose at the targets ------------------------
    targets: list[dict] = []
    for code, wanted in TARGETS.items():
        if metric not in wanted:
            continue
        reg = frame[l3 == code]
        obs = series.reindex(reg.index).dropna()
        regime = "E" if code == REGIME_E_TARGET else "I"
        kept = {f"kept_{k}": v for k, v in br.disturbance_gap(retained(reg, regime), tvec).items()}
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "units": cfg.get("units", ""), "transform": kind, "regime": regime,
                "n_observed": int(len(obs)), **br.disturbance_gap(reg, tvec), **kept}
        if rule.get("p_low") is not None and len(obs) >= 5:
            blind = series.reindex(reg.index)[~reg["pass_strict"].astype(bool)].dropna()
            a = br.percentile_anchors(blind, rule["p_low"], rule["p_high"])
            targets.append({**base, "basis": "1A_percentile", "kind": "population",
                            "n_fit": int(len(blind)),
                            "detail": "estimates best available, not least disturbed",
                            "pred_q25": None if not a else round(a[0], 4),
                            "pred_q75": None if not a else round(a[1], 4)})
        for item in dict.fromkeys([s for s in (spec_i, *REGIME_E_SPECS) if s]):
            model, train = fit_for(code, regime, ev, series, item, kind=kind, offset=offset)
            got = br.model_anchors(model, reg, tvec, group=code, n_resid=N_RESID,
                                   rng=_rng(metric, f"target-{code}", item, seed=seed))
            boot_rng = _rng(metric, f"target-{code}", item, "draws", seed=seed)

            def refit_t(pos, _item=item, _train=train, _reg=reg, _code=code, _regime=regime):
                m, _ = fit_for(_code, _regime, ev, series, _item, kind=kind, offset=offset,
                               rows=_train.iloc[pos])
                return br.model_anchors(m, _reg, tvec, group=_code, rng=boot_rng,
                                        n_resid=N_RESID)["anchors"]

            iv = br.cluster_bootstrap(refit_t, train["huc12"], n_boot=N_BOOT_MODEL,
                                      rng=_rng(metric, f"target-{code}", item, "iv", seed=seed)) \
                if got["anchors"] else None
            ok = bool(iv and iv.get("q25") is not None)
            a = got["anchors"]
            targets.append({**base, "basis": f"1B_{item}", "kind": "model", "spec": item,
                            "n_fit": int(model["n_train"]) if model else 0,
                            "n_local_train": int(len(retained(reg, regime))),
                            "blup": got["blup"], "selected_on_dev": item == spec_i,
                            "extrapolation_ok": br.extrapolation_share(train, reg, NATURAL),
                            "pred_q25": None if not a else round(a[0], 4),
                            "pred_q75": None if not a else round(a[1], 4),
                            "ci_q25_lo": round(iv["q25"][0], 4) if ok else None,
                            "ci_q25_hi": round(iv["q25"][1], 4) if ok else None,
                            "ci_q75_lo": round(iv["q75"][0], 4) if ok else None,
                            "ci_q75_hi": round(iv["q75"][1], 4) if ok else None})
    frozen = {"percentile": rule, "spec_selected": spec_i, "spec_table": chosen["table"],
              "transform": kind, "log_offset": offset if kind == "log10" else None,
              "n_dev_cells_1a": len(dev_cells)}
    return records, targets, frozen, time.time() - t0


REGISTRY_SPEC = "mixed_local"


def run_metric_iv(metric: str, testable: list[str], regime_e: list[str],
                  seed: int) -> tuple[list[dict], list[dict], dict, float]:
    """Protocol IV: the registry specification in every testable region, fitted
    as production fits it. Records carry the natural coverage and the
    disturbance gap each cell asked the model to bridge, which is what the
    registry's application limits are read from."""
    from streamcurves import modeled_reference as mr
    t0 = time.time()
    frame, l3 = _STATE["frame"], _STATE["l3"]
    cfg = _STATE["mc"][metric]
    tvec = _STATE["target_vector"]
    series = _series(metric)
    kind = br.transform_for(metric)
    offset = br.log_offset(series)
    records: list[dict] = []
    plan = [(code, "I") for code in testable] + [(code, "E") for code in regime_e]
    for code, regime in plan:
        reg = frame[l3 == str(code)]
        ref = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
        evald = series.reindex(reg.index).dropna()
        truth = br.cell_truth(ref, evald, cfg, clusters=reg["huc12"], n_boot=N_BOOT_TRUTH,
                              seed=br.cell_seed(metric, code, "truth", seed=seed))
        if truth is None:
            continue
        kept_rows = retained(reg, regime)
        rows = (mr.training_frame(frame, code) if regime == "I" else
                pd.concat([frame[l3 != str(code)], kept_rows]))
        kept = {f"kept_{k}": v for k, v in br.disturbance_gap(kept_rows, tvec).items()}
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "role": "eval", "regime": regime, "units": cfg.get("units", ""),
                "transform": kind, **br.disturbance_gap(reg, tvec), **kept}
        model, train = fit_for(code, regime, [], series, REGISTRY_SPEC, kind=kind,
                               offset=offset, rows=rows)
        got = br.model_anchors(model, reg, tvec, group=code, n_resid=N_RESID,
                               rng=_rng(metric, code, REGISTRY_SPEC, regime, seed=seed))
        cov = br.coverage_by_covariate(train, reg, NATURAL)
        boot_rng = _rng(metric, code, REGISTRY_SPEC, regime, "draws", seed=seed)

        def refit(pos, _train=train, _reg=reg, _code=code, _regime=regime):
            m, _ = fit_for(_code, _regime, [], series, REGISTRY_SPEC, kind=kind, offset=offset,
                           rows=_train.iloc[pos])
            return br.model_anchors(m, _reg, tvec, group=_code, rng=boot_rng,
                                    n_resid=N_RESID)["anchors"]

        iv = br.cluster_bootstrap(refit, train["huc12"], n_boot=N_BOOT_MODEL,
                                  rng=_rng(metric, code, REGISTRY_SPEC, regime, "iv",
                                           seed=seed)) if got["anchors"] else None
        fields = {"basis": f"1B_{REGISTRY_SPEC}", "kind": "model", "spec": REGISTRY_SPEC,
                  "n_fit": int(model["n_train"]) if model else 0, "n_pred": got["n_pred"],
                  "n_local_train": int(len(kept_rows)), "blup": got["blup"],
                  "extrapolation_ok": br.extrapolation_share(train, reg, NATURAL),
                  "coverage_no_climate": cov.get("joint_no_climate"),
                  "family": rp.family_of(metric)}
        if got["anchors"] is None:
            records.append({**base, **fields, "detail": "no anchors"})
            continue
        records.append({**base, **fields,
                        **br.candidate_record(truth, anchors=got["anchors"], n_fit=None,
                                              interval=iv, n_draws=N_DRAWS)})
    frozen = {"spec": REGISTRY_SPEC, "transform": kind,
              "log_offset": offset if kind == "log10" else None}
    return records, [], frozen, time.time() - t0


def run(out_dir: Path | None, *, seed: int, min_reference: int, jobs: int,
        prereg: Path | None, only_metrics: list[str] | None, protocol: str = "ii") -> dict:
    t0 = time.time()
    _init(protocol)
    frame, l3 = _STATE["frame"], _STATE["l3"]
    testable = sorted([c for c, g in frame.groupby(l3)
                       if int(g["pass_strict"].sum()) >= min_reference and len(g) >= 25],
                      key=lambda c: (len(c), c))
    dev, ev = br.split_regions(testable, seed=seed)
    regime_e = [c for c in testable
                if len(retained(frame[l3 == c], "E")) >= MIN_LOCAL_E]
    if protocol == "iv":
        from run_hierarchy_test import metrics_under_test
        pool_metrics, worker, args = metrics_under_test(), run_metric_iv, (testable, regime_e, seed)
        n_i = len(testable)
    else:
        pool_metrics, worker, args = METRICS, run_metric, (testable, dev, ev, regime_e, seed)
        n_i = len(ev)
    metrics = [m for m in pool_metrics if only_metrics is None or m in only_metrics]
    print(f"[model] protocol {protocol}: {len(metrics)} metrics, {n_i} regime I regions, "
          f"{len(regime_e)} regime E regions {regime_e}, jobs={jobs}", flush=True)
    records, targets, frozen = [], [], {}
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init,
                                 initargs=(protocol,)) as pool:
            futs = {m: pool.submit(worker, m, *args) for m in metrics}
            for m in metrics:
                r, t, f, dt = futs[m].result()
                records += r
                targets += t
                frozen[m] = f
                print(f"[model] {m}: {len(r)} records, spec "
                      f"{f.get('spec_selected') or f.get('spec')} in {dt:.0f}s", flush=True)
    else:
        for m in metrics:
            r, t, f, dt = worker(m, *args)
            records += r
            targets += t
            frozen[m] = f
            print(f"[model] {m}: {len(r)} records, spec "
                  f"{f.get('spec_selected') or f.get('spec')} in {dt:.0f}s", flush=True)
    table, tgt = pd.DataFrame(records), pd.DataFrame(targets)
    summary = {"generated": date.today().isoformat(), "seed": seed, "protocol": protocol,
               "frame": "wadeable (DATA-10)" if protocol == "iv" else "all stream orders",
               "n_frame": int(len(frame)),
               "preregistration_sha256": (hashlib.sha256(prereg.read_bytes()).hexdigest()
                                          if prereg and prereg.exists() else None),
               "development_regions": dev, "evaluation_regions": ev,
               "regime_e_regions": regime_e,
               "regime_e_agriculture_minimum": _STATE["threshold"],
               "prediction_point": _STATE["target_vector"], "n_records": len(table),
               "n_boot": {"truth": N_BOOT_TRUTH, "population": N_BOOT_POP,
                          "model": N_BOOT_MODEL, "variability": N_DRAWS,
                          "residual_draws": N_RESID},
               "frozen_rules": frozen, "runtime_s": round(time.time() - t0)}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "model.csv", index=False)
        tgt.to_csv(out_dir / "model_targets.csv", index=False)
        (out_dir / "model.json").write_text(json.dumps(summary, indent=1, default=str),
                                            encoding="utf-8")
        print(f"[model] wrote {out_dir / 'model.csv'}", flush=True)
    return {"summary": summary, "table": table, "targets": tgt}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--prereg", default=None)
    ap.add_argument("--metric", action="append", default=None)
    ap.add_argument("--protocol", choices=("ii", "iv"), default="ii",
                    help="ii reproduces rounds two and three; iv is Pre-registration IV")
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, seed=a.seed, min_reference=a.min_reference,
              jobs=a.jobs, prereg=Path(a.prereg) if a.prereg else None, only_metrics=a.metric,
              protocol=a.protocol)
    print(json.dumps({k: v for k, v in got["summary"].items() if k != "frozen_rules"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
