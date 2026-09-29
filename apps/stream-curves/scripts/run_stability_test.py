"""Outcome O5, stability: how far an arm's curves move under resampling of their pools.

Per arm, region and metric with a station pool (the evidence package's
``pools.json`` names the admitted stations of every fitted curve):

* the bootstrap class-flip rate at the reference stations: the pool is resampled
  by HUC12 cluster (the protocol's bootstrap unit; HUC8 where HUC12 is missing) 200
  times, the curve is rebuilt by the shipping engine on each resample, and the share
  of the pool's own stations whose DEEP class (NF / AR / F) differs from the class the
  full curve gives is recorded; the median and the 90th percentile over resamples;
* the ACC-04 drop-one shift: ``curve_stability.influence_check``, the largest move of
  a quartile in units of the interquartile range when one station is removed, and
  whether removing one station flips the build.

    python scripts/run_stability_test.py --campaign <arm>/campaign --out <arm>/evaluation/stability \\
        --protocol <yaml> [--n-boot 200] [--seed 11] [--l3 CODE ...] [--metric KEY ...]

Writes ``stability.csv`` (one row per region and metric) and ``stability.json`` (the
stamp and the medians). Run under ``STREAMCURVES_CONFIG_ROOT=<arm config>`` so the
curve is rebuilt under the arm's geometry; the root is recorded. Each cell has its
own seed (the CRC32 of region, metric and the run seed), so the table never depends
on the order cells run in.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from streamcurves import curves, round2  # noqa: E402

TABLE_FILE = "stability.csv"
SUMMARY_FILE = "stability.json"
EVIDENCE_PREFIX = "deep-dev-l3-"
COLUMNS = ["l3", "metric", "option", "status", "n_pool", "n_values", "n_clusters", "curve_status",
           "flip_median", "flip_p90", "flip_mean", "n_boot", "n_boot_valid",
           "acc04_max_shift_iqr", "acc04_max_param_change_frac", "acc04_decision_flip", "acc04_flagged",
           "seed"]


def pools_for_run(run: Path) -> dict[str, dict]:
    """``{metric: {"stationIds": [...], "option": ..., "status": ...}}`` from the run
    folder's evidence package (``evidence/deep-dev-l3-<code>/data/pools.json``)."""
    out: dict[str, dict] = {}
    for p in sorted((run / "evidence").glob(f"{EVIDENCE_PREFIX}*/data/pools.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        for mk, rec in (doc or {}).items():
            ids = [str(x) for x in (rec.get("stationIds") or [])]
            if ids:
                out[str(mk)] = {"stationIds": ids, "option": rec.get("option"),
                                "status": (rec.get("record") or {}).get("status")}
    return out


def scored_metric_keys(bundle: dict, metric_keys: list[str]) -> set[str]:
    """The metric keys of the bundle's fitted (not fixed) curves, by their bundle id."""
    ids = {str(m.get("metricId")) for _f, m in round2.bundle_metrics(bundle)
           if str(m.get("criteriaBasis") or "") != "fixed"}
    return {mk for mk in metric_keys if round2.metric_id_of(mk) in ids}


def staged_bundle(run: Path) -> Optional[dict]:
    for man in sorted((run / "library" / "assessments").glob("*/manifest.json")):
        m = json.loads(man.read_text(encoding="utf-8"))
        v = int(m.get("latestVersion") or 0)
        p = man.parent / f"v{v}" / "assessment.deep.json"
        if v and p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def national_inputs(value_policy: Optional[str] = None) -> dict:
    from streamcurves import nrsa_dataset as nd
    from streamcurves import pressure_evidence as pe
    max_order, protocols = nd.governed_frame("wadeable")
    inp = pe.national_inputs(max_stream_order=max_order, protocols=protocols, value_policy=value_policy)
    frame = inp["frame"].copy()
    frame["station_key"] = frame["station_key"].astype(str)
    values = inp["values"].copy()
    values = values.set_index(values["site_id"].astype(str))
    clusters = frame.set_index("station_key")
    cl = clusters["huc12"].astype(object).where(clusters["huc12"].notna(), clusters.get("huc8"))
    return {"frame": frame, "values": values, "metric_config": inp["metric_config"],
            "clusters": cl, "value_policy": inp["value_policy"]}


def flip_rate(values: pd.Series, entry: dict, clusters: pd.Series, *, n_boot: int, seed: int) -> dict:
    """The bootstrap class-flip rate of one pool: ``values`` indexed by station, the
    engine entry, and each station's cluster label. Every resample draws whole
    clusters with replacement, rebuilds the curve and classes the pool's own
    stations; a resample whose curve cannot be built is counted as invalid."""
    from streamcurves import curve_stability as cs
    vals = pd.to_numeric(values, errors="coerce").dropna()
    out: dict[str, Any] = {"n_values": int(len(vals)), "n_clusters": 0, "curve_status": None,
                           "flip_median": None, "flip_p90": None, "flip_mean": None,
                           "n_boot": int(n_boot), "n_boot_valid": 0}
    if not len(vals):
        return out
    labels = clusters.reindex(vals.index).astype(object)
    labels = labels.where(labels.notna(), pd.Series([f"_row{i}" for i in range(len(vals))], index=vals.index))
    out["n_clusters"] = int(labels.astype(str).nunique())
    full, status = cs._build_points(vals.reset_index(drop=True), entry)
    out["curve_status"] = status
    if full is None:
        return out
    arr = vals.to_numpy(dtype=float)
    base = np.array([round2.band_of(curves.interp_curve(full, float(x))) for x in arr], dtype=object)
    members = round2.cluster_groups(labels.astype(str).tolist())
    rng = np.random.default_rng(int(seed))
    flips = []
    for _ in range(int(n_boot)):
        picked = rng.integers(0, len(members), size=len(members))
        rows = np.concatenate([members[i] for i in picked])
        pts, _s = cs._build_points(pd.Series(arr[rows]), entry)
        if pts is None:
            continue
        got = np.array([round2.band_of(curves.interp_curve(pts, float(x))) for x in arr], dtype=object)
        flips.append(float(np.mean(got != base)))
    if flips:
        f = np.asarray(flips, dtype=float)
        out.update({"flip_median": float(np.median(f)), "flip_p90": float(np.quantile(f, 0.90)),
                    "flip_mean": float(np.mean(f)), "n_boot_valid": int(len(f))})
    return out


def acc04(values: pd.Series, entry: dict) -> dict:
    from streamcurves import curve_stability as cs
    rec = cs.influence_check(values, entry)
    return {"acc04_max_shift_iqr": rec.get("max_param_change_iqr"),
            "acc04_max_param_change_frac": rec.get("max_param_change_frac"),
            "acc04_decision_flip": rec.get("decision_flip"), "acc04_flagged": rec.get("flagged")}


def cell(l3: str, metric: str, pool: dict, entry: dict, values_wide: pd.DataFrame, clusters: pd.Series, *,
         n_boot: int, seed: int) -> dict:
    ids = [i for i in pool["stationIds"] if i in values_wide.index]
    series = pd.to_numeric(values_wide.loc[ids, metric], errors="coerce") if metric in values_wide.columns else pd.Series(dtype=float)
    cell_seed = round2.seed_for(l3, metric, "stability", seed=seed)
    row = {"l3": l3, "metric": metric, "option": pool.get("option"), "status": pool.get("status"),
           "n_pool": int(len(pool["stationIds"])), "seed": cell_seed}
    row.update(flip_rate(series, entry, clusters, n_boot=n_boot, seed=cell_seed))
    row.update(acc04(series.dropna(), entry) if len(series.dropna()) else
               {"acc04_max_shift_iqr": None, "acc04_max_param_change_frac": None,
                "acc04_decision_flip": None, "acc04_flagged": None})
    return row


def run(*, campaign: Path, out_dir: Path, protocol: Path, n_boot: int, seed: int,
        only_l3: Optional[list[str]] = None, only_metrics: Optional[list[str]] = None,
        inputs: Optional[dict] = None, config_root: Optional[Path] = None, progress=print) -> dict:
    inputs = inputs or national_inputs()
    values, clusters, metric_config = inputs["values"], inputs["clusters"], inputs["metric_config"]
    rows: list[dict] = []
    runs = sorted(Path(campaign).glob("l3-*"))
    for run_dir in runs:
        code = run_dir.name.split("-", 1)[-1]
        if only_l3 and code not in set(only_l3):
            continue
        bundle = staged_bundle(run_dir)
        pools = pools_for_run(run_dir)
        if bundle is None or not pools:
            progress(f"[stability] L3-{code}: no staged version or no evidence package, skipped")
            continue
        fitted = scored_metric_keys(bundle, list(pools))
        for mk in sorted(fitted):
            if only_metrics and mk not in set(only_metrics):
                continue
            entry = metric_config.get(mk)
            if not entry:
                progress(f"[stability] L3-{code} {mk}: no metric config under this root, skipped")
                continue
            rows.append(cell(code, mk, pools[mk], entry, values, clusters, n_boot=n_boot, seed=seed))
            r = rows[-1]
            progress(f"[stability] L3-{code} {mk:<22} n={r['n_values']:>4} flip median "
                     f"{round2.fmt(r['flip_median'], 3)} acc04 shift {round2.fmt(r['acc04_max_shift_iqr'], 3)}")
    table = pd.DataFrame(rows, columns=COLUMNS)
    flips = pd.to_numeric(table["flip_median"], errors="coerce").dropna() if len(table) else pd.Series(dtype=float)
    shifts = pd.to_numeric(table["acc04_max_shift_iqr"], errors="coerce").dropna() if len(table) else pd.Series(dtype=float)
    summary = {"schema": "round2-stability/1", "stamp": round2.stamp(protocol, config_root=config_root),
               "campaign_folder": str(campaign), "value_policy": inputs.get("value_policy"),
               "n_boot": int(n_boot), "seed": int(seed), "bootstrap_unit": "huc12 (huc8 where missing)",
               "n_cells": int(len(table)), "cells_hash": round2.station_list_hash(
                   (f"{r['l3']}|{r['metric']}" for r in rows)),
               "flip_rate": {"median": float(flips.median()) if len(flips) else None,
                             "p90_of_medians": float(flips.quantile(0.9)) if len(flips) else None},
               "acc04": {"median_shift_iqr": float(shifts.median()) if len(shifts) else None,
                         "n_decision_flip": int(pd.Series(table["acc04_decision_flip"]).fillna(False).astype(bool).sum()) if len(table) else 0}}
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / TABLE_FILE, index=False, lineterminator="\n")
    (out_dir / SUMMARY_FILE).write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n",
                                        encoding="utf-8", newline="\n")
    return {"summary": summary, "table": table}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--l3", action="append", default=None)
    ap.add_argument("--metric", action="append", default=None)
    ap.add_argument("--value-policy", default=None)
    ap.add_argument("--config-root", default=None)
    a = ap.parse_args(argv)
    got = run(campaign=Path(a.campaign).resolve(), out_dir=Path(a.out).resolve(), protocol=Path(a.protocol).resolve(),
              n_boot=a.n_boot, seed=a.seed, only_l3=a.l3, only_metrics=a.metric,
              inputs=national_inputs(a.value_policy), config_root=Path(a.config_root) if a.config_root else None)
    s = got["summary"]
    print(f"[stability] {s['n_cells']} cells; median flip rate {round2.fmt(s['flip_rate']['median'], 4)}; "
          f"median ACC-04 shift {round2.fmt(s['acc04']['median_shift_iqr'], 4)} -> {Path(a.out) / TABLE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
