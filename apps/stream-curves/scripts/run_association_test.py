"""Outcome O3, independent association: an arm's curves scored at non-reference stations.

For every region of an arm's staged campaign (a folder of ``l3-<code>`` run folders,
each with a staged version under ``library/``), every non-reference in-frame station
of the region is scored on the arm's curves exactly as DEEP scores a site (the metric
index by piecewise-linear interpolation, the layer its slope or drainage area selects,
the function score as the mean index times 15, the ECI rollup), and the function
scores and the ECI are set against the NRSA biological indices of
``scripts/build_bio_indices.py``: the area under the ROC curve for the benthic MMI
class (Good against Poor), the O/E class and the fish MMI class, and Spearman's rho
against the scores, per region and pooled. The folds are HUC8: every station carries
its HUC8, the pooled intervals resample HUC8 clusters, and the station lists are
hashed so a paired comparison between two arms (``round2.paired_auc_delta``) runs on
identical stations. Curves are fitted per arm on the development set; the folds
partition the evaluation stations.

    python scripts/run_association_test.py --campaign <arm>/campaign \\
        --bio-indices <root>/bio_indices.parquet --out <arm>/evaluation/association \\
        --protocol <yaml> [--n-boot 200] [--seed 11] [--l3 CODE ...]

Writes ``association.csv`` (one row per scope, subject and target), ``association.json``
(the stamp, the station lists hashed, the counts) and ``association_stations.csv``
(one row per evaluation station with its scores, fold and targets, the input of the
paired comparison). Run under ``STREAMCURVES_CONFIG_ROOT=<arm config>`` so the frame
and the values are read under the arm's configuration; the root is recorded.

O3 supports association, never validation: the MMI reference designation shares
landscape variables with the screen (protocol outcomes.O3.independence_limit).
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

from streamcurves import round2  # noqa: E402

TARGETS = {
    "benthic_mmi": {"class": "benthic_mmi_class", "score": "mmi_bent"},
    "oe": {"class": "oe_class", "score": "oe_score"},
    "fish_mmi": {"class": "fish_mmi_class", "score": "mmi_fish"},
}
POSITIVE, NEGATIVE = "Good", "Poor"
ECI = "eci"
STATIONS_FILE = "association_stations.csv"
TABLE_FILE = "association.csv"
SUMMARY_FILE = "association.json"


# --------------------------------------------------------------------------- #
# the staged campaign
# --------------------------------------------------------------------------- #
def staged_bundles(campaign: Path) -> list[tuple[str, Path, dict]]:
    """``[(l3 code, bundle path, bundle)]`` for every run folder with a staged version."""
    out = []
    for run in sorted(Path(campaign).glob("l3-*")):
        lib = run / "library" / "assessments"
        if not lib.is_dir():
            continue
        for man in sorted(lib.glob("*/manifest.json")):
            m = json.loads(man.read_text(encoding="utf-8"))
            v = int(m.get("latestVersion") or 0)
            p = man.parent / f"v{v}" / "assessment.deep.json"
            if v and p.is_file():
                b = json.loads(p.read_text(encoding="utf-8"))
                code = str((b.get("region") or {}).get("code") or run.name.split("-", 1)[-1])
                out.append((code, p, b))
    return out


def national_frame_and_values(value_policy: Optional[str]) -> dict:
    """The frame and the DATA-11 values a build reads (pressure_evidence.national_inputs
    under the governed wadeable frame), keyed by station."""
    from streamcurves import nrsa_dataset as nd
    from streamcurves import pressure_evidence as pe
    max_order, protocols = nd.governed_frame("wadeable")
    inp = pe.national_inputs(max_stream_order=max_order, protocols=protocols, value_policy=value_policy)
    frame = inp["frame"].copy()
    frame["station_key"] = frame["station_key"].astype(str)
    values = inp["values"].copy()
    values = values.set_index(values["site_id"].astype(str))
    return {"frame": frame, "values": values, "value_policy": inp["value_policy"]}


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def evaluation_stations(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """The region's non-reference in-frame stations (the strict reference withheld)."""
    sel = frame[(frame["l3"].astype(str) == str(code)) & ~frame["pass_strict"].astype(bool)]
    return sel.reset_index(drop=True)


def score_stations(bundle: dict, stations: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """One row per station: the function scores (``f__<functionId>``), the ECI
    (None while a function is unassessed), the over-scored ratio and the coverage
    bounds, with the fold (HUC8), the HUC12 and the NARS-9 group."""
    columns = list(dict.fromkeys(list(values.columns) + list(stations.columns)))
    columns_of = round2.metric_columns(bundle, columns)
    rows = []
    for st in stations.itertuples(index=False):
        key = str(st.station_key)
        vals: dict[str, Any] = {}
        if key in values.index:
            vals.update({c: v for c, v in values.loc[key].items()})
        for c in stations.columns:
            vals.setdefault(c, getattr(st, c, None))
        got = round2.score_station(bundle, vals, columns_of=columns_of,
                                   drainage_area_sqkm=getattr(st, "drainage_area_sqkm", None),
                                   nhd_slope=getattr(st, "nhd_slope", None))
        row = {"station_key": key, "l3": str(st.l3), "huc8": _text(getattr(st, "huc8", None)),
               "huc12": _text(getattr(st, "huc12", None)), "nars9": _text(getattr(st, "nars9", None)),
               "eci": got["eci"], "eci_over_scored": got["eci_over_scored"],
               "eci_lo": None if got["eci_bounds"] is None else got["eci_bounds"][0],
               "eci_hi": None if got["eci_bounds"] is None else got["eci_bounds"][1],
               "n_functions_scored": sum(1 for v in got["functions"].values() if v is not None)}
        for fid in round2.FUNCTION_IDS:
            row[f"f__{fid}"] = got["functions"].get(fid)
        rows.append(row)
    return pd.DataFrame(rows)


def _text(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def fold_of(row: pd.Series) -> str:
    """The HUC8 fold (the region code where a station has no HUC8, so it still folds).
    Read through ``_text``: a string column holds a missing value as NaN, which is truthy."""
    h = _text(row.get("huc8"))
    return h if h else f"l3-{_text(row.get('l3'))}"


# --------------------------------------------------------------------------- #
# association
# --------------------------------------------------------------------------- #
def subjects(scored: pd.DataFrame) -> list[str]:
    """The ECI and every function at least one station scored (a function no curve of
    the arm reaches has no association to report)."""
    return [ECI] + [c for c in scored.columns if c.startswith("f__")
                    and pd.to_numeric(scored[c], errors="coerce").notna().any()]


def subject_scores(scored: pd.DataFrame, subject: str) -> np.ndarray:
    col = "eci_over_scored" if subject == ECI else subject
    return pd.to_numeric(scored[col], errors="coerce").to_numpy(dtype=float)


def associate(scored: pd.DataFrame, *, scope: str, n_boot: int = 0, seed: int = 11) -> list[dict]:
    """The AUC (Good against Poor) and Spearman rows of one scope for every subject and
    target, with HUC8-cluster bootstrap intervals when ``n_boot`` is positive."""
    rows = []
    folds = scored.apply(fold_of, axis=1).tolist() if len(scored) else []
    for subject in subjects(scored):
        x = subject_scores(scored, subject)
        for target, cols in TARGETS.items():
            cls = scored[cols["class"]].astype(object) if cols["class"] in scored.columns else pd.Series([None] * len(scored))
            labelled = cls.isin([POSITIVE, NEGATIVE]).to_numpy() & np.isfinite(x)
            pos = (cls == POSITIVE).to_numpy()
            y = pd.to_numeric(scored[cols["score"]], errors="coerce").to_numpy(dtype=float) \
                if cols["score"] in scored.columns else np.full(len(scored), np.nan)
            row: dict[str, Any] = {"scope": scope, "subject": subject, "target": target,
                                   "n": int(np.isfinite(x).sum()), "n_class": int(labelled.sum()),
                                   "n_pos": int((labelled & pos).sum()), "n_neg": int((labelled & ~pos).sum()),
                                   "auc": None, "auc_lo": None, "auc_hi": None,
                                   "rho": None, "rho_lo": None, "rho_hi": None,
                                   "n_rho": int((np.isfinite(x) & np.isfinite(y)).sum())}
            if row["n_pos"] and row["n_neg"]:
                xs, ps = x[labelled], pos[labelled]
                row["auc"] = round2.auc(xs, ps)
                if n_boot and row["auc"] is not None:
                    cl = [f for f, ok in zip(folds, labelled) if ok]
                    samples = round2.cluster_bootstrap(cl, lambda r: round2.auc(xs[r], ps[r]),
                                                       n_boot=n_boot, seed=round2.seed_for(scope, subject, target, "auc", seed=seed))
                    row["auc_lo"], row["auc_hi"] = round2.interval(samples, round2.MARGINS["O3"]["level"])
            rho = round2.spearman(x, y)
            row["rho"] = rho
            if n_boot and rho is not None:
                ok = np.isfinite(x) & np.isfinite(y)
                xo, yo = x[ok], y[ok]
                cl = [f for f, k in zip(folds, ok) if k]
                samples = round2.cluster_bootstrap(cl, lambda r: round2.spearman(xo[r], yo[r]),
                                                   n_boot=n_boot, seed=round2.seed_for(scope, subject, target, "rho", seed=seed))
                row["rho_lo"], row["rho_hi"] = round2.interval(samples, round2.MARGINS["O3"]["level"])
            rows.append(row)
    return rows


def run(*, campaign: Path, bio_indices: Path, out_dir: Path, protocol: Path, n_boot: int, seed: int,
        only_l3: Optional[list[str]] = None, value_policy: Optional[str] = None,
        inputs: Optional[dict] = None, config_root: Optional[Path] = None) -> dict:
    bundles = staged_bundles(campaign)
    if only_l3:
        bundles = [b for b in bundles if b[0] in set(only_l3)]
    if not bundles:
        raise SystemExit(f"no staged version under {campaign}")
    inputs = inputs or national_frame_and_values(value_policy)
    frame, values = inputs["frame"], inputs["values"]
    bio = pd.read_parquet(bio_indices) if str(bio_indices).endswith(".parquet") else pd.read_csv(bio_indices, dtype={"station_key": str})
    bio["station_key"] = bio["station_key"].astype(str)
    scored_all = []
    per_region: dict[str, dict] = {}
    for code, path, bundle in bundles:
        stations = evaluation_stations(frame, code)
        scored = score_stations(bundle, stations, values)
        scored = scored.merge(bio[["station_key"] + [c for t in TARGETS.values() for c in (t["class"], t["score"])]],
                              on="station_key", how="left")
        scored.insert(1, "arm_region", code)
        per_region[code] = {"bundle": str(path), "n_stations": int(len(scored)),
                            "stations_hash": round2.station_list_hash(scored["station_key"]),
                            "n_folds": int(scored.apply(fold_of, axis=1).nunique()) if len(scored) else 0,
                            "coverage": round2.coverage_of(bundle)}
        scored_all.append(scored)
    stations_table = pd.concat(scored_all, ignore_index=True) if scored_all else pd.DataFrame()
    rows: list[dict] = []
    for code in sorted(per_region, key=lambda c: (len(c), c)):
        rows.extend(associate(stations_table[stations_table["arm_region"] == code], scope=code, n_boot=0))
    rows.extend(associate(stations_table, scope="pooled", n_boot=n_boot, seed=seed))
    table = pd.DataFrame(rows)
    summary = {
        "schema": "round2-association/1",
        "stamp": round2.stamp(protocol, config_root=config_root),
        "campaign_folder": str(campaign),
        "value_policy": inputs.get("value_policy"),
        "bio_indices": {"path": str(bio_indices), "sha256": round2.sha256_of(bio_indices)},
        "targets": {k: {**v, "positive": POSITIVE, "negative": NEGATIVE} for k, v in TARGETS.items()},
        "folds": "huc8 (the region code where a station has no HUC8)",
        "n_boot": int(n_boot), "seed": int(seed), "interval_level": round2.MARGINS["O3"]["level"],
        "regions": per_region,
        "pooled": {"n_stations": int(len(stations_table)),
                   "stations_hash": round2.station_list_hash(stations_table["station_key"]) if len(stations_table) else None,
                   "n_folds": int(stations_table.apply(fold_of, axis=1).nunique()) if len(stations_table) else 0},
        "independence_limit": ("O3 supports association, never validation: the MMI reference designation "
                               "shares landscape variables with the screen"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / TABLE_FILE, index=False, lineterminator="\n")
    stations_table.to_csv(out_dir / STATIONS_FILE, index=False, lineterminator="\n")
    (out_dir / SUMMARY_FILE).write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n",
                                        encoding="utf-8", newline="\n")
    return {"summary": summary, "table": table, "stations": stations_table}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--campaign", required=True, help="the arm's staged campaign folder (run folders l3-<code>)")
    ap.add_argument("--bio-indices", required=True, help="bio_indices.parquet or .csv from build_bio_indices.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--protocol", required=True, help="the evaluation protocol whose sha256 the output records")
    ap.add_argument("--n-boot", type=int, default=200, help="HUC8-cluster resamples for the pooled intervals")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--l3", action="append", default=None, help="restrict to these regions (repeatable)")
    ap.add_argument("--value-policy", default=None, help="the archive value policy (the new-build default when absent)")
    ap.add_argument("--config-root", default=None, help="recorded in the output; set STREAMCURVES_CONFIG_ROOT to read under it")
    a = ap.parse_args(argv)
    got = run(campaign=Path(a.campaign).resolve(), bio_indices=Path(a.bio_indices).resolve(),
              out_dir=Path(a.out).resolve(), protocol=Path(a.protocol).resolve(), n_boot=a.n_boot, seed=a.seed,
              only_l3=a.l3, value_policy=a.value_policy,
              config_root=Path(a.config_root) if a.config_root else None)
    pooled = got["table"][(got["table"]["scope"] == "pooled") & (got["table"]["subject"] == ECI)]
    for r in pooled.to_dict("records"):
        print(f"[association] pooled ECI vs {r['target']}: AUC {round2.fmt(r['auc'], 3)} "
              f"[{round2.fmt(r['auc_lo'], 3)}, {round2.fmt(r['auc_hi'], 3)}] on {r['n_class']} stations; "
              f"rho {round2.fmt(r['rho'], 3)} on {r['n_rho']}")
    print(f"[association] {got['summary']['pooled']['n_stations']} evaluation stations in "
          f"{len(got['summary']['regions'])} region(s) -> {Path(a.out) / TABLE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
