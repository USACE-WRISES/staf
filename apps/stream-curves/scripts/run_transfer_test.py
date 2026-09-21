"""Option 3 improved, and the published criterion, on Pre-registration II.

Bases scored here borrow from outside the region and use none of its own
observations, so there is no selection step and nothing to leak: every region
with a reference pool (at least 12 strict-pass and 25 in-frame stations) is a
test, left out in turn. That is 19 regions, against round one's 9.

  3a_envelope    round one's basis: donors inside the comparability envelope
  3b_adjusted    the reference expectation fitted on natural setting, predicted
                 for the region's own streams
  3c_matched     the three nearest national least-disturbed streams to each of the
                 region's streams, by Gower distance
  4_nrsa_bands   NRSA Table 7-1 total phosphorus for the region's NARS-9 region
                 (chem_PTL only; validated at most as an external criterion)

A second pass computes what each basis would propose for the two targets, where
nothing is withheld because there is nothing to withhold. It is what a
recommendation would rest on, beside the region's own local comparison and, for
phosphorus, the Michigan SQT criterion as a cross-check. It scores nothing.

Each cell has its own seed, so ``--jobs`` changes the run time and not the table.

    py -3.12 scripts/run_transfer_test.py --out <folder> --prereg <file> --jobs 10
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
from streamcurves import basis_transfer as bt          # noqa: E402
from streamcurves import basis_validation as bv        # noqa: E402
from streamcurves import pressure_evidence as pe       # noqa: E402
from streamcurves import reference_pool as rp          # noqa: E402

NATURAL = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
METRICS = ["bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND", "bent_TOTLNTAX",
           "fish_NAT_TOTLNTAX", "fish_NAT_NTOLNTAX", "chem_CHLA", "chem_COND",
           "chem_NTL", "chem_PH", "chem_PTL", "chem_TURB"]
#: The two targets and the metrics each is missing.
TARGETS = {"55": METRICS, "71": ["bent_TOLRPIND", "bent_TOTLNTAX", "chem_NTL"]}
#: Fixed in Pre-registration II.
N_BOOT_TRUTH, N_BOOT_POP, N_BOOT_MODEL, N_DRAWS, N_RESID = 300, 60, 20, 100, 200

_STATE: dict = {}


def _init() -> None:
    warnings.simplefilter("ignore")
    inp = pe.national_inputs()
    frame, values = inp["frame"], inp["values"]
    _STATE.update({"frame": frame, "mc": inp["metric_config"],
                   "wide": values.set_index(values["site_id"].astype(str)),
                   "keys": frame["station_key"].astype(str)})


def _series(metric: str) -> pd.Series:
    s = _STATE
    return pd.Series(s["keys"].map(pd.to_numeric(s["wide"][metric], errors="coerce")).to_numpy(),
                     index=s["frame"].index)


def _rng(*parts, seed):
    return np.random.default_rng(br.cell_seed(*parts, seed=seed))


def _band_shares(points, values) -> dict:
    """The share of a region's stations each class would take under a curve."""
    if not points:
        return {}
    calls = [c for c in bv.calls_for(points, values) if c]
    if not calls:
        return {}
    n = len(calls)
    return {f"share_{b}": round(sum(1 for c in calls if c == b) / n, 4) for b in ("F", "AR", "NF")}


def _bases(metric: str, reg: pd.DataFrame, donors_frame: pd.DataFrame, series: pd.Series,
           cfg: dict, *, kind: str, offset: float, code: str, seed: int):
    """Every basis's anchors, interval and domain for one region, as ``(basis,
    kind, fields, anchors-or-points, n_fit, interval)``."""
    out = []
    # 3a: round one's envelope donors
    donors, dvals = bt.envelope_donors(metric, reg, donors_frame, series)
    f3a = {"n_fit": int(len(dvals)),
           "n_donor_huc12": int(donors["huc12"].nunique()) if len(donors) else 0}
    if len(dvals) >= bt.MIN_DONORS:
        iv = br.cluster_bootstrap(lambda pos: br.anchors_of(dvals.iloc[pos]),
                                  donors.loc[dvals.index, "huc12"], n_boot=N_BOOT_POP,
                                  rng=_rng(metric, code, "3a_envelope", "iv", seed=seed))
        out.append(("3a_envelope", "population", f3a, br.anchors_of(dvals), len(dvals), iv))
    else:
        out.append(("3a_envelope", "population", {**f3a, "detail": "fewer than 10 donors"},
                    None, len(dvals), None))

    # 3b: the reference expectation adjusted for natural setting
    y = pd.to_numeric(series.reindex(donors_frame.index), errors="coerce")
    train = donors_frame[y.notna().to_numpy()]
    model = bt.adjusted_model(train, series, NATURAL, kind=kind, offset=offset)
    got = br.model_anchors(model, reg, None, rng=_rng(metric, code, "3b_adjusted", seed=seed),
                           n_resid=N_RESID)
    fitted = br.model_anchors(model, reg, None, fitted_only=True)["anchors"]
    cov = br.coverage_by_covariate(train, reg, NATURAL, pressure_quantile=1.0)
    boot_rng = _rng(metric, code, "3b_adjusted", "draws", seed=seed)

    def refit(pos):
        m = bt.adjusted_model(train.iloc[pos], series, NATURAL, kind=kind, offset=offset)
        return br.model_anchors(m, reg, None, rng=boot_rng, n_resid=N_RESID)["anchors"]

    iv = br.cluster_bootstrap(refit, train["huc12"], n_boot=N_BOOT_MODEL,
                              rng=_rng(metric, code, "3b_adjusted", "iv", seed=seed)) \
        if got["anchors"] else None
    out.append(("3b_adjusted", "model",
                {"n_fit": int(model["n_train"]) if model else 0, "n_pred": got["n_pred"],
                 "extrapolation_ok": br.extrapolation_share(train, reg, NATURAL,
                                                            pressure_quantile=1.0),
                 "coverage_no_climate": cov.get("joint_no_climate"),
                 "fitted_q25": None if not fitted else round(fitted[0], 4),
                 "fitted_q75": None if not fitted else round(fitted[1], 4)},
                got["anchors"], None, iv))

    # 3c: the nearest comparable streams
    matches = bt.gower_matches(metric, reg, donors_frame, series)
    s = bt.matched_summary(matches)
    f3c = {"n_fit": s["n_distinct"], "n_matches": int(len(matches)),
           "n_donor_l3": s.get("n_donor_l3"), "distance_median": s["distance_median"],
           "distance_max": s["distance_max"]}
    if s["n_distinct"] >= bt.MIN_DONORS:
        vals = matches["value"].reset_index(drop=True)
        iv = br.cluster_bootstrap(lambda pos: br.anchors_of(vals.iloc[pos]),
                                  matches["target_huc12"].reset_index(drop=True),
                                  n_boot=N_BOOT_POP,
                                  rng=_rng(metric, code, "3c_matched", "iv", seed=seed))
        out.append(("3c_matched", "population", f3c, s["anchors"], s["n_distinct"], iv))
    else:
        out.append(("3c_matched", "population",
                    {**f3c, "detail": "fewer than 10 distinct donors"}, None, s["n_distinct"], None))

    # 4: the published criterion, phosphorus only
    if metric == "chem_PTL":
        nars, share = bt.majority_nars9(reg)
        pts = bt.nrsa_tp_points(nars) if nars else None
        bands = bt.nrsa_tp_bands(nars) if nars else None
        out.append(("4_nrsa_bands", "external",
                    {"nars9": nars, "nars9_share": share,
                     "band_good_fair": None if not bands else bands[0],
                     "band_fair_poor": None if not bands else bands[1]}, pts, None, None))
    return out


def run_metric(metric: str, testable: list[str], dev: list[str], ev: list[str],
               seed: int) -> tuple[list[dict], list[dict], float]:
    t0 = time.time()
    frame, cfg = _STATE["frame"], _STATE["mc"][metric]
    series = _series(metric)
    kind = br.transform_for(metric)
    offset = br.log_offset(series)
    l3 = frame["l3"].astype(str)
    records: list[dict] = []
    for code in testable:
        reg = frame[l3 == code]
        ref = series.reindex(reg.index)[reg["pass_strict"].astype(bool)].dropna()
        evald = series.reindex(reg.index).dropna()
        truth = br.cell_truth(ref, evald, cfg, clusters=reg["huc12"], n_boot=N_BOOT_TRUTH,
                              seed=br.cell_seed(metric, code, "truth", seed=seed))
        if truth is None:
            continue
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "role": "eval" if code in ev else "dev", "units": cfg.get("units", ""),
                "regime": "all", "transform": kind}
        donors_frame = bt.national_reference(frame, exclude_l3=code)
        for basis, bkind, fields, cand, n_fit, iv in _bases(
                metric, reg, donors_frame, series, cfg, kind=kind, offset=offset,
                code=code, seed=seed):
            if cand is None:
                records.append({**base, "basis": basis, "kind": bkind, **fields})
                continue
            rec = (br.candidate_record(truth, points=cand, external=True, n_draws=N_DRAWS)
                   if bkind == "external" else
                   br.candidate_record(truth, anchors=cand, n_fit=n_fit, interval=iv,
                                       n_draws=N_DRAWS))
            records.append({**base, "basis": basis, "kind": bkind, **fields, **rec})

    targets: list[dict] = []
    for code, wanted in TARGETS.items():
        if metric not in wanted:
            continue
        reg = frame[l3 == code]
        obs = series.reindex(reg.index).dropna()
        # local_comparison keys its values by station_key, not by frame position
        by_key = pd.Series(series.to_numpy(), index=_STATE["keys"].to_numpy())
        local = rp.local_comparison(metric, by_key, reg) or {}
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "units": cfg.get("units", ""), "transform": kind, "n_observed": int(len(obs)),
                "observed_q25": round(float(obs.quantile(0.25)), 4) if len(obs) else None,
                "observed_q50": round(float(obs.quantile(0.50)), 4) if len(obs) else None,
                "observed_q75": round(float(obs.quantile(0.75)), 4) if len(obs) else None,
                "local_comparison_n": local.get("n"), "local_q25": local.get("q25"),
                "local_q50": local.get("q50"), "local_q75": local.get("q75")}
        # the application borrows every national least-disturbed stream, the
        # target's own included where it has any, as the ladder admits local ones
        donors_frame = bt.national_reference(frame)
        for basis, bkind, fields, cand, n_fit, iv in _bases(
                metric, reg, donors_frame, series, cfg, kind=kind, offset=offset,
                code=f"target-{code}", seed=seed):
            pts = cand if bkind == "external" else br.curve_from_anchors(cand, cfg)
            ok = bool(iv and iv.get("q25") is not None)
            targets.append({**base, "basis": basis, "kind": bkind, **fields,
                            "pred_q25": None if bkind == "external" or not cand else round(cand[0], 4),
                            "pred_q75": None if bkind == "external" or not cand else round(cand[1], 4),
                            "ci_q25_lo": round(iv["q25"][0], 4) if ok else None,
                            "ci_q25_hi": round(iv["q25"][1], 4) if ok else None,
                            "ci_q75_lo": round(iv["q75"][0], 4) if ok else None,
                            "ci_q75_hi": round(iv["q75"][1], 4) if ok else None,
                            **_band_shares(pts, obs)})
        if metric == "chem_PTL" and code == "55":
            mi = bt.michigan_tp_points()
            nrsa = bt.nrsa_tp_points("TPL")
            a, b = bv.calls_for(nrsa, obs), bv.calls_for(mi, obs)
            pairs = [(x, y) for x, y in zip(a, b) if x and y]
            targets.append({**base, "basis": "4_michigan_sqt", "kind": "external",
                            "detail": "cross-check against 4_nrsa_bands on the same stations",
                            "agree_with_nrsa": round(sum(x == y for x, y in pairs) / len(pairs), 4)
                            if pairs else None, **_band_shares(mi, obs)})
    return records, targets, time.time() - t0


def run(out_dir: Path | None, *, seed: int, min_reference: int, jobs: int,
        prereg: Path | None, only_metrics: list[str] | None) -> dict:
    t0 = time.time()
    _init()
    frame = _STATE["frame"]
    testable = sorted([c for c, g in frame.groupby(frame["l3"].astype(str))
                       if int(g["pass_strict"].sum()) >= min_reference and len(g) >= 25],
                      key=lambda c: (len(c), c))
    dev, ev = br.split_regions(testable, seed=seed)
    metrics = [m for m in METRICS if only_metrics is None or m in only_metrics]
    print(f"[transfer] {len(metrics)} metrics x {len(testable)} regions, jobs={jobs}", flush=True)
    records, targets = [], []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init) as pool:
            futs = {m: pool.submit(run_metric, m, testable, dev, ev, seed) for m in metrics}
            for m in metrics:
                r, t, dt = futs[m].result()
                records += r
                targets += t
                print(f"[transfer] {m}: {len(r)} records in {dt:.0f}s", flush=True)
    else:
        for m in metrics:
            r, t, dt = run_metric(m, testable, dev, ev, seed)
            records += r
            targets += t
            print(f"[transfer] {m}: {len(r)} records in {dt:.0f}s", flush=True)
    table = pd.DataFrame(records)
    tgt = pd.DataFrame(targets)
    summary = {"generated": date.today().isoformat(), "seed": seed,
               "preregistration_sha256": (hashlib.sha256(prereg.read_bytes()).hexdigest()
                                          if prereg and prereg.exists() else None),
               "testable_regions": testable, "development_regions": dev,
               "evaluation_regions": ev, "n_records": len(table), "n_target_rows": len(tgt),
               "n_boot": {"truth": N_BOOT_TRUTH, "population": N_BOOT_POP,
                          "model": N_BOOT_MODEL, "variability": N_DRAWS,
                          "residual_draws": N_RESID},
               "gower_k": bt.GOWER_K, "min_donors": bt.MIN_DONORS,
               "runtime_s": round(time.time() - t0)}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "transfer.csv", index=False)
        tgt.to_csv(out_dir / "transfer_targets.csv", index=False)
        (out_dir / "transfer.json").write_text(json.dumps(summary, indent=1, default=str),
                                               encoding="utf-8")
        print(f"[transfer] wrote {out_dir / 'transfer.csv'}", flush=True)
    return {"summary": summary, "table": table, "targets": tgt}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--prereg", default=None, help="the pre-registration this run is under")
    ap.add_argument("--metric", action="append", default=None)
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, seed=a.seed, min_reference=a.min_reference,
              jobs=a.jobs, prereg=Path(a.prereg) if a.prereg else None, only_metrics=a.metric)
    print(json.dumps(got["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
