"""Round four: the station sources of methodology 0.14, under Pre-registration IV.

Every source of the 0.14 hierarchy that rests on stations is scored the way
Pre-registration II scores a basis. Each testable region's own least-disturbed
stations are withheld as the truth, and the source builds its pool exactly as a
production build would for that region, from everything else, through the same
functions (``reference_pool.choose_pool`` with ``only_option`` and
``withhold``, ``basis_transfer.national_donors_for``):

  2r_l3        the region's own streams under its regional screen, which with the
               strict reference withheld are its relaxed-only stations
  2r_l2        the Level II pool under the regional screen
  2r_nars9     the NARS-9 pool under the regional screen
  2r_l1        the Level I pool under the regional screen
  3c_matched   the three nearest national least-disturbed streams to each of the
               region's streams, inside its faunal province for an assemblage
  3a_envelope  national least-disturbed streams inside the widened envelope,
               faunal province likewise

A region is testable when it holds at least 12 strict-screen and 25 in-frame
stations, as in Pre-registration II. Each cell has its own seed, so ``--jobs``
changes the run time and never the table.

    py -3.12 scripts/run_hierarchy_test.py --out <folder> --prereg <file> --jobs 10

The fold rule (campaign Round 2, evaluation protocol V, section 4): a withheld
region informs nothing it is scored under. Its strict stations leave every pool
(``withhold``), the region leaves the EPA agriculture-limit calibration of the
regional screen (``withhold_l3``, ``reference_pool.epa_agriculture_limit``), and
the scale registry a cell reads is a per-fold one: ``--registry CODE=PATH``
(repeatable) or ``--registry-dir DIR`` (``DIR/l3-<CODE>/registry_candidate.yaml``,
as ``run_national_scale_analysis.py --out DIR/l3-<CODE> --withhold-l3 CODE``
writes them). Each cell's registry is compared with the committed full one and
the share of identical decisions is reported (``registry_agreement`` in the
summary); the protocol's shortcut lets the full registry stand in for a fold
that reproduces at least 0.95 of its decisions, and a cell with no fold registry
reads the committed one and says so. The registry sets each source's recorded
transfer risk; it never changes which stations a pool holds.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
from streamcurves import pressure_evidence as pe       # noqa: E402
from streamcurves import reference_pool as rp          # noqa: E402
from streamcurves import scale_analysis as sa          # noqa: E402

REGIONAL = ("l3", "l2", "nars9", "l1")
#: Fixed in Pre-registration II and kept by IV.
N_BOOT_TRUTH, N_BOOT_POP, N_DRAWS = 300, 60, 100
#: the evaluation protocol's leave-one-region-out shortcut for the scale registry
REGISTRY_AGREEMENT_SHORTCUT = 0.95

_STATE: dict = {}


def _scale_script():
    """``scripts/run_national_scale_analysis.py`` as a module (``registry_agreement``)."""
    path = Path(__file__).resolve().with_name("run_national_scale_analysis.py")
    spec = importlib.util.spec_from_file_location("run_national_scale_analysis", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_registry_args(items) -> dict[str, Path]:
    """``--registry CODE=PATH`` items as ``{code: path}``."""
    out: dict[str, Path] = {}
    for item in items or []:
        if "=" not in str(item):
            raise ValueError(f"--registry expects CODE=PATH, got {item!r}")
        code, path = str(item).split("=", 1)
        code, path = code.strip(), path.strip()
        if not code or not path:
            raise ValueError(f"--registry expects CODE=PATH, got {item!r}")
        out[code] = Path(path)
    return out


def registry_for(code: str, registries: dict | None, registry_dir: Path | None) -> Path | None:
    """The fold registry file of one cell: the explicit ``--registry`` entry, else
    ``<registry_dir>/l3-<code>/registry_candidate.yaml`` when it exists, else None
    (the cell reads the committed registry)."""
    code = str(code)
    if registries and code in registries:
        return Path(registries[code])
    if registry_dir is not None:
        candidate = Path(registry_dir) / f"l3-{code}" / "registry_candidate.yaml"
        if candidate.exists():
            return candidate
    return None


def load_cell_registries(testable, registries: dict | None, registry_dir: Path | None,
                         full: dict | None = None) -> dict[str, dict]:
    """Per cell: the registry read (``registry``), where it came from (``path``,
    or None for the committed one), its sha256, whether its ``withheldL3`` names
    the cell, and its agreement with the full registry (``registry_agreement``).
    A fold registry that does not name the cell as withheld is refused: it would
    let the region inform its own evaluation."""
    full = sa.load_registry() if full is None else full
    agreement = _scale_script().registry_agreement
    out: dict[str, dict] = {}
    for code in testable:
        path = registry_for(code, registries, registry_dir)
        if path is None:
            out[str(code)] = {"registry": full, "path": None, "sha256": sa.registry_sha256(),
                              "fold": False, "withheldL3": [],
                              "agreement": {"nMetrics": len(full.get("metrics") or {}),
                                            "nIdentical": len(full.get("metrics") or {}),
                                            "share": 1.0 if full.get("metrics") else None,
                                            "differ": [],
                                            "shortcut": REGISTRY_AGREEMENT_SHORTCUT,
                                            "fullRegistryMayStandIn": True,
                                            "note": "no fold registry given: the committed "
                                                    "registry was read"}}
            continue
        if not path.exists():
            raise FileNotFoundError(f"fold registry for L3 {code} not found: {path}")
        reg = sa.load_registry.__wrapped__(str(path))
        held = [str(c) for c in ((reg.get("inputs") or {}).get("withheldL3") or [])]
        if str(code) not in held:
            raise ValueError(f"{path} does not withhold L3 {code} (withheldL3 = {held}); a "
                             "cell's fold registry must have been computed without it")
        out[str(code)] = {"registry": reg, "path": str(path),
                          "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                          "fold": True, "withheldL3": held,
                          "agreement": agreement(reg, full, shortcut=REGISTRY_AGREEMENT_SHORTCUT)}
    return out


def production_inputs() -> dict:
    """The national inputs a production build reads: the governed wadeable frame
    (DATA-10), which ``run_region_batch.py stage`` passes by default. Rounds two
    and three read every stream order."""
    from streamcurves import nrsa_dataset as nd
    max_order, protocols = nd.governed_frame("wadeable")
    return pe.national_inputs(max_stream_order=max_order, protocols=protocols)


def _init() -> None:
    warnings.simplefilter("ignore")
    inp = production_inputs()
    frame, values = inp["frame"], inp["values"]
    _STATE.update({"frame": frame, "mc": inp["metric_config"],
                   "wide": values.set_index(values["site_id"].astype(str)),
                   "keys": frame["station_key"].astype(str)})


def metrics_under_test() -> list[str]:
    """Every metric a build loads that may borrow (it has a family), reserve
    candidates included: the hierarchy can reach any of them in some region."""
    inp = production_inputs()
    cfg = rp.load_transfer_config()
    fam = set((cfg.get("metric_family") or {}))
    return sorted(m for m in inp["metric_config"] if m in fam and m in inp["values"].columns)


def _series(metric: str) -> pd.Series:
    s = _STATE
    return pd.Series(s["keys"].map(pd.to_numeric(s["wide"][metric], errors="coerce")).to_numpy(),
                     index=s["frame"].index)


def _by_key(metric: str) -> pd.Series:
    return pd.to_numeric(_STATE["wide"][metric], errors="coerce")


def _rng(*parts, seed):
    return np.random.default_rng(br.cell_seed(*parts, seed=seed))


def _population(values: pd.Series, clusters: pd.Series, *, parts, seed):
    iv = br.cluster_bootstrap(lambda pos: br.anchors_of(values.iloc[pos]),
                              clusters.reset_index(drop=True), n_boot=N_BOOT_POP,
                              rng=_rng(*parts, "iv", seed=seed))
    return br.anchors_of(values), iv


def _sources(metric: str, code: str, reg: pd.DataFrame, strict_keys: list[str], *, seed: int,
             registry: dict | None = None):
    """``(basis, fields, anchors, n_fit, interval)`` for every source, one region.

    The fold rule: the region's strict stations are withheld from every pool and
    the region leaves the agriculture calibration (``withhold_l3``); ``registry``
    is the cell's fold registry, which sets the recorded transfer risk."""
    frame = _STATE["frame"]
    by_key = _by_key(metric)
    series = _series(metric)
    profile = rp.family_profile(metric)
    order = rp.search_order(profile)
    huc = frame.set_index(frame["station_key"].astype(str))["huc12"]
    scale_entry = ((registry or {}).get("metrics") or {}).get(metric)
    out = []
    for level in REGIONAL:
        basis = f"2r_{level}"
        if level not in order:
            continue
        d, _ = rp.choose_pool(metric, by_key, frame, code, profile=profile,
                              only_option=f"regional_{level}", withhold=strict_keys,
                              withhold_l3=code, scale_entry=scale_entry)
        fields = {"n_fit": int(d.n_usable), "n_local": int(d.n_local),
                  "agriculture_limit": (d.screen_detail or {}).get("agriculture_limit"),
                  "calibration_withheld_l3": ",".join(
                      (d.screen_detail or {}).get("calibration_withheld_l3") or []),
                  "supported_level": d.supported_level, "transfer_risk": d.transfer_risk}
        if d.status == rp.STATUS_INSUFFICIENT or not d.station_ids:
            out.append((basis, {**fields, "detail": "fewer than 10 usable stations"}, None,
                        int(d.n_usable), None))
            continue
        ids = list(d.station_ids)
        vals = by_key.reindex(ids).dropna()
        anchors, iv = _population(vals.reset_index(drop=True), huc.reindex(vals.index),
                                  parts=(metric, code, basis), seed=seed)
        out.append((basis, fields, anchors, len(vals), iv))

    donors = bt.national_donors_for(metric, reg, frame, exclude_l3=code)
    matches = bt.gower_matches(metric, reg, donors, series)
    s = bt.matched_summary(matches)
    f3c = {"n_fit": s["n_distinct"], "n_matches": int(len(matches)),
           "distance_median": s["distance_median"], "distance_max": s["distance_max"]}
    if s["n_distinct"] >= bt.MIN_DONORS:
        vals = matches["value"].reset_index(drop=True)
        anchors, iv = _population(vals, matches["target_huc12"],
                                  parts=(metric, code, "3c_matched"), seed=seed)
        out.append(("3c_matched", f3c, anchors, s["n_distinct"], iv))
    else:
        out.append(("3c_matched", {**f3c, "detail": "fewer than 10 distinct donors"}, None,
                    s["n_distinct"], None))

    spans = rp.national_spans(frame, (profile or {}).get("covariates") or [])
    drows, dvals = bt.envelope_donors(metric, reg, donors, series, spans=spans)
    f3a = {"n_fit": int(len(dvals))}
    if len(dvals) >= bt.MIN_DONORS:
        anchors, iv = _population(dvals.reset_index(drop=True), drows.loc[dvals.index, "huc12"],
                                  parts=(metric, code, "3a_envelope"), seed=seed)
        out.append(("3a_envelope", f3a, anchors, len(dvals), iv))
    else:
        out.append(("3a_envelope", {**f3a, "detail": "fewer than 10 donors"}, None,
                    len(dvals), None))
    return out


def run_metric(metric: str, testable: list[str], dev: list[str], ev: list[str],
               seed: int, registries: dict | None = None) -> tuple[list[dict], float]:
    t0 = time.time()
    frame, cfg = _STATE["frame"], _STATE["mc"][metric]
    series = _series(metric)
    kind = br.transform_for(metric)
    l3 = frame["l3"].astype(str)
    records: list[dict] = []
    for code in testable:
        reg = frame[l3 == code]
        strict = reg["pass_strict"].astype(bool)
        ref = series.reindex(reg.index)[strict].dropna()
        evald = series.reindex(reg.index).dropna()
        truth = br.cell_truth(ref, evald, cfg, clusters=reg["huc12"], n_boot=N_BOOT_TRUTH,
                              seed=br.cell_seed(metric, code, "truth", seed=seed))
        if truth is None:
            continue
        cell = (registries or {}).get(str(code)) or {}
        base = {"metric": metric, "l3": code, "region": str(reg["l3_name"].iloc[0]),
                "nars9": str(reg["nars9"].mode().iat[0]) if reg["nars9"].notna().any() else "",
                "role": "eval" if code in ev else "dev", "units": cfg.get("units", ""),
                "regime": "all", "transform": kind, "kind": "population",
                "family": rp.family_of(metric),
                "region_agriculture_median": round(float(pd.to_numeric(
                    reg.get("agriculture_ws"), errors="coerce").median()), 2),
                "registry_fold": bool(cell.get("fold"))}
        strict_keys = reg.loc[strict, "station_key"].astype(str).tolist()
        for basis, fields, anchors, n_fit, iv in _sources(metric, code, reg, strict_keys,
                                                          seed=seed,
                                                          registry=cell.get("registry")):
            if anchors is None:
                records.append({**base, "basis": basis, **fields})
                continue
            rec = br.candidate_record(truth, anchors=anchors, n_fit=n_fit, interval=iv,
                                      n_draws=N_DRAWS)
            records.append({**base, "basis": basis, **fields, **rec})
    return records, time.time() - t0


def run(out_dir: Path | None, *, seed: int, min_reference: int, jobs: int,
        prereg: Path | None, only_metrics: list[str] | None,
        registries: dict | None = None, registry_dir: Path | None = None) -> dict:
    t0 = time.time()
    _init()
    frame = _STATE["frame"]
    testable = sorted([c for c, g in frame.groupby(frame["l3"].astype(str))
                       if int(g["pass_strict"].sum()) >= min_reference and len(g) >= 25],
                      key=lambda c: (len(c), c))
    dev, ev = br.split_regions(testable, seed=seed)
    metrics = [m for m in metrics_under_test() if only_metrics is None or m in only_metrics]
    cells = load_cell_registries(testable, registries, registry_dir)
    n_fold = sum(1 for c in cells.values() if c.get("fold"))
    print(f"[hierarchy] {len(metrics)} metrics x {len(testable)} regions, jobs={jobs}, "
          f"fold registries for {n_fold} of {len(testable)} cells", flush=True)
    records = []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init) as pool:
            futs = {m: pool.submit(run_metric, m, testable, dev, ev, seed, cells)
                    for m in metrics}
            for m in metrics:
                r, dt = futs[m].result()
                records += r
                print(f"[hierarchy] {m}: {len(r)} records in {dt:.0f}s", flush=True)
    else:
        for m in metrics:
            r, dt = run_metric(m, testable, dev, ev, seed, cells)
            records += r
            print(f"[hierarchy] {m}: {len(r)} records in {dt:.0f}s", flush=True)
    table = pd.DataFrame(records)
    summary = {"generated": date.today().isoformat(), "seed": seed,
               "frame": "wadeable (DATA-10)", "n_frame": int(len(frame)),
               "preregistration_sha256": (hashlib.sha256(prereg.read_bytes()).hexdigest()
                                          if prereg and prereg.exists() else None),
               "testable_regions": testable, "development_regions": dev,
               "evaluation_regions": ev, "metrics": metrics, "n_records": len(table),
               "n_boot": {"truth": N_BOOT_TRUTH, "population": N_BOOT_POP,
                          "variability": N_DRAWS},
               "gower_k": bt.GOWER_K, "min_donors": bt.MIN_DONORS,
               # the fold rule: strict stations out of every pool, the region out
               # of the agriculture calibration, and the registry each cell read
               "fold_rule": {"pools": "strict stations withheld",
                             "agriculture_calibration": "region withheld (withhold_l3)",
                             "scale_registry": "per-fold registry where given, else the "
                                               "committed one; the protocol's shortcut lets "
                                               "the full registry stand in at agreement "
                                               f">= {REGISTRY_AGREEMENT_SHORTCUT:g}"},
               "registry_agreement": {code: {k: v for k, v in c.items() if k != "registry"}
                                      for code, c in cells.items()},
               "runtime_s": round(time.time() - t0)}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        table.to_csv(out_dir / "hierarchy.csv", index=False)
        (out_dir / "hierarchy.json").write_text(json.dumps(summary, indent=1, default=str),
                                                encoding="utf-8")
        print(f"[hierarchy] wrote {out_dir / 'hierarchy.csv'}", flush=True)
    return {"summary": summary, "table": table}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--min-reference", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--prereg", default=None, help="the pre-registration this run is under")
    ap.add_argument("--metric", action="append", default=None)
    ap.add_argument("--registry", action="append", default=None, metavar="CODE=PATH",
                    help="repeatable; the per-fold scale registry a cell reads "
                         "(run_national_scale_analysis.py --withhold-l3 CODE --out ...)")
    ap.add_argument("--registry-dir", default=None,
                    help="a folder holding l3-<CODE>/registry_candidate.yaml per cell")
    a = ap.parse_args(argv)
    got = run(Path(a.out) if a.out else None, seed=a.seed, min_reference=a.min_reference,
              jobs=a.jobs, prereg=Path(a.prereg) if a.prereg else None, only_metrics=a.metric,
              registries=parse_registry_args(a.registry),
              registry_dir=Path(a.registry_dir) if a.registry_dir else None)
    print(json.dumps(got["summary"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
