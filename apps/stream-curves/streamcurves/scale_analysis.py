"""The national scale and stratifier analysis (rule STRAT-10, methodology 0.12).

One analysis, run once over every least-disturbed station of the archive,
decides two things for each metric and records them in
``config/metric_scale_registry.yaml``, which every regional build then reads:

* the SCALE the metric's variance supports: whether reference values differ
  between Level III ecoregions more than they do between Level II or Level I
  regions. A metric that varies no more at Level III than at Level I can borrow
  reference stations from the parent region at low risk. One that carries a
  strong Level III signal cannot, and a borrowed pool says so.
* whether a CLASS SPLIT (channel slope, drainage area) explains enough of what
  is left to deserve its own curves.

This is EASI's pre-registered rule (notes/EASI_Rework/03_stress_test_protocol.md)
applied to the field metrics. EASI ran it on 2.65 million reaches. Here there
are a few hundred reference stations, where the plain rank eta-squared H/(n-1)
inflates with the number of groups, so decisions read the bias-adjusted form
(H - (k - 1)) / (n - k) and both are reported.

Classes are declared constants (slope 0.5 and 2 percent, drainage area 10 and
100 km2), read from NHDPlus by COMID, because that is what DEEP has at scoring
time: a curve set must be chosen on the same variable it was split on.

Pure: data frames in, records out. The CLI is scripts/run_national_scale_analysis.py.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import curves
from . import discrimination as dz
from . import reference_screen as rscreen
from .config import read_yaml
from .curve_stability import _build_points
from .paths import CONFIG_DIR
from .screening import _kruskal

ANALYSIS_VERSION = "scale-1"
REGISTRY_PATH = CONFIG_DIR / "metric_scale_registry.yaml"

LEVELS = ("l3", "l2", "l1")
DIAGNOSTIC_LEVELS = ("nars9",)

STRATIFIERS: dict[str, dict] = {
    "NhdSlopeClass": {
        "variable": "nhd_slope", "column": "slope_class", "units": "m/m",
        "source": "NHDPlus V2 flowline slope", "breaks": list(rscreen.SLOPE_BREAKS),
        "right": False, "labels": list(rscreen.SLOPE_LABELS),
        "display": {"lt_0.5": "Low gradient (under 0.5 percent)",
                    "0.5_to_2": "Moderate gradient (0.5 to 2 percent)",
                    "ge_2": "Steep (2 percent and above)"},
    },
    "NhdDrainageAreaClass": {
        "variable": "drainage_area_sqkm", "column": "da_class", "units": "km2",
        "source": "NHDPlus V2 total drainage area", "breaks": list(rscreen.DA_BREAKS),
        "right": True, "labels": list(rscreen.DA_LABELS),
        "display": {"le_10": "Headwater (10 km2 and under)",
                    "10_to_100": "Small stream (10 to 100 km2)",
                    "gt_100": "Large stream (over 100 km2)"},
    },
}

DEFAULT_RULE = {
    "eta2": "rank_adjusted", "level_ratio": 0.8, "national_floor": 0.02, "tie": 0.02,
    "split_incremental_min": 0.05, "stratum_min_n": 15, "flip_accept": 0.10,
    "flip_exploratory": 0.20, "min_group": 5, "min_reference_n": 50,
    "yardstick_max_auc_loss": 0.02,
}


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def rank_eta2(values: Any, groups: Any, *, min_group: int = 5) -> dict:
    """Rank-based variance explained by a grouping.

    ``eta2`` is H/(n-1) (the epsilon-squared ``effects.py`` reports) and
    ``eta2_adj`` is (H-(k-1))/(n-k), which does not grow with the number of
    groups when there is no effect. Groups under ``min_group`` are left out
    and counted.
    """
    df = pd.DataFrame({"v": pd.to_numeric(pd.Series(values).reset_index(drop=True),
                                          errors="coerce"),
                       "g": pd.Series(groups).reset_index(drop=True).astype(object)}).dropna()
    sizes = df.groupby("g")["v"].size()
    keep = sizes[sizes >= int(min_group)].index
    dropped = int((sizes < int(min_group)).sum())
    df = df[df["g"].isin(keep)]
    k, n = int(df["g"].nunique()), int(len(df))
    out = {"eta2": None, "eta2_adj": None, "H": None, "p": None, "k": k, "n": n,
           "n_groups_dropped": dropped}
    if k < 2 or n <= k:
        return out
    got = _kruskal([g["v"].to_numpy() for _, g in df.groupby("g")])
    if got is None or not np.isfinite(got[0]):
        return out
    h, p = got
    out.update({"H": float(h), "p": float(p), "eta2": float(h / (n - 1)),
                "eta2_adj": float(max(0.0, (h - (k - 1)) / (n - k)))})
    return out


def level_table(values: pd.Series, frame: pd.DataFrame, *, min_group: int = 5,
                levels=LEVELS + DIAGNOSTIC_LEVELS) -> dict:
    return {lvl: rank_eta2(values, frame[lvl], min_group=min_group)
            for lvl in levels if lvl in frame.columns}


def supported_level(table: dict, *, ratio: float = 0.8, floor: float = 0.02,
                    tie: float = 0.02) -> str:
    """The coarsest level that keeps the Level III signal.

    ``national`` when the Level III signal itself is under the floor. Otherwise
    the coarsest of Level I and Level II whose adjusted eta-squared is at least
    ``ratio`` of Level III's (or within ``tie`` of it), and Level III when
    neither is. Level III is never compared with itself: its ratio is always
    one, which would make it unreachable.
    """
    l3 = (table.get("l3") or {}).get("eta2_adj")
    if l3 is None:
        return "l3"
    if l3 < floor:
        return "national"
    for lvl in ("l1", "l2"):                      # coarsest first
        got = (table.get(lvl) or {}).get("eta2_adj")
        if got is not None and (got >= ratio * l3 or (l3 - got) <= tie):
            return lvl
    return "l3"


def incremental_eta2(values: pd.Series, base_groups: Optional[pd.Series],
                     split_groups: pd.Series, *, min_group: int = 5) -> dict:
    """What a class split adds beyond the supported level's own grouping."""
    split = pd.Series(split_groups).reset_index(drop=True).astype(object)
    if base_groups is None:
        base = {"eta2_adj": 0.0}
        combined = split
    else:
        b = pd.Series(base_groups).reset_index(drop=True).astype(object)
        base = rank_eta2(values, b, min_group=min_group)
        combined = (b.astype(str) + "|" + split.astype(str)).where(b.notna() & split.notna())
    both = rank_eta2(values, combined, min_group=min_group)
    if both["eta2_adj"] is None or base["eta2_adj"] is None:
        return {"increment": None, "base": base.get("eta2_adj"), "combined": both["eta2_adj"],
                "k": both["k"], "n": both["n"]}
    return {"increment": float(both["eta2_adj"] - base["eta2_adj"]), "base": base["eta2_adj"],
            "combined": both["eta2_adj"], "k": both["k"], "n": both["n"]}


def _deep_band(index: Any) -> Optional[str]:
    if index is None or index != index:
        return None
    return "NF" if index <= 0.39 else "AR" if index <= 0.69 else "F"


def band_flip_rate(ref_values: pd.Series, clusters: pd.Series, eval_values: pd.Series,
                   entry: dict, *, n_boot: int = 200, seed: int = 7) -> dict:
    """How often a stratum's class calls change when its reference stations are
    resampled by watershed (HUC12, else HUC8) and the curve is rebuilt through
    the real engine."""
    ref = pd.DataFrame({"v": pd.to_numeric(ref_values, errors="coerce"),
                        "c": pd.Series(clusters).astype(object)}).dropna(subset=["v"])
    ref["c"] = ref["c"].where(ref["c"].notna(), ref.index.astype(str))
    ev = pd.to_numeric(eval_values, errors="coerce").dropna()
    out = {"flip_mean": None, "flip_p90": None, "n_boot_valid": 0, "n_eval": int(len(ev)),
           "verdict": "not_evaluable"}
    full, _ = _build_points(ref["v"], entry)
    if full is None or len(ev) < 5 or ref["c"].nunique() < 5:
        return out
    base = [_deep_band(curves.interp_curve(full, float(x))) for x in ev]
    rng = np.random.default_rng(int(seed))
    groups = {c: g["v"].to_numpy() for c, g in ref.groupby("c")}
    keys = list(groups)
    flips: list[float] = []
    for _ in range(int(n_boot)):
        draw = rng.choice(len(keys), size=len(keys), replace=True)
        sample = np.concatenate([groups[keys[i]] for i in draw])
        pts, _status = _build_points(pd.Series(sample), entry)
        if pts is None:
            continue
        again = [_deep_band(curves.interp_curve(pts, float(x))) for x in ev]
        flips.append(float(np.mean([a != b for a, b in zip(base, again)])))
    if not flips:
        return out
    out.update({"flip_mean": round(float(np.mean(flips)), 4),
                "flip_p90": round(float(np.quantile(flips, 0.90)), 4),
                "n_boot_valid": len(flips)})
    return out


def _flip_verdict(flip: Optional[float], rule: dict) -> str:
    if flip is None:
        return "not_evaluable"
    if flip <= rule["flip_accept"]:
        return "accept"
    return "exploratory" if flip <= rule["flip_exploratory"] else "reject"


def _yardstick(values: pd.Series, frame: pd.DataFrame, entry: dict,
               split_col: Optional[str]) -> Optional[float]:
    """AUC of the curve index, EPA R against Im, with one national curve or
    one curve per class."""
    ref = frame["pass_strict"].astype(bool)
    rt = frame["rt_nrsa"].astype(object)
    idx = pd.Series(np.nan, index=frame.index, dtype="float64")
    if split_col is None:
        pts, _ = _build_points(values[ref], entry)
        if pts is None:
            return None
        idx = dz.score_values(pts, values)
        idx.index = frame.index
    else:
        for label, members in frame.groupby(split_col):
            pts, _ = _build_points(values[ref & (frame[split_col] == label)], entry)
            if pts is None:
                continue
            got = dz.score_values(pts, values.loc[members.index])
            idx.loc[members.index] = got.to_numpy()
    r, im = idx[rt == "R"].dropna(), idx[rt == "Im"].dropna()
    if len(r) < dz.MIN_GROUP or len(im) < dz.MIN_GROUP:
        return None
    return dz.auc(r, im)


# --------------------------------------------------------------------------- #
# one metric
# --------------------------------------------------------------------------- #
def analyze_metric(metric: str, entry: dict, frame: pd.DataFrame, values: pd.Series, *,
                   rule: Optional[dict] = None, n_boot: int = 200, seed: int = 7) -> dict:
    """The registry record for one metric.

    ``frame``: every in-frame station (``reference_pool.national_frame``) with
    ``pass_strict``, the level codes, the class columns, ``rt_nrsa`` and a
    cluster id. ``values``: the metric's latest non-null value, aligned to it.
    """
    rule = {**DEFAULT_RULE, **(rule or {})}
    values = pd.to_numeric(pd.Series(values.to_numpy(), index=frame.index), errors="coerce")
    ref = frame["pass_strict"].astype(bool) & values.notna()
    rf, rv = frame[ref], values[ref]
    record: dict[str, Any] = {"status": "decided", "n_reference": int(ref.sum()),
                              "supported_level": "l3", "split": None}
    if int(ref.sum()) < rule["min_reference_n"]:
        record.update({"status": "insufficient_data",
                       "decision_basis": (f"{int(ref.sum())} reference stations carry this "
                                          f"metric, under the {rule['min_reference_n']} the "
                                          "analysis needs. Treated as Level III, unsplit.")})
        return record

    table = level_table(rv, rf, min_group=rule["min_group"])
    level = supported_level(table, ratio=rule["level_ratio"], floor=rule["national_floor"],
                            tie=rule["tie"])
    record["eta2"] = {lvl: {"raw": _r(t["eta2"]), "adj": _r(t["eta2_adj"]), "k": t["k"],
                            "n": t["n"], "p": _r(t["p"], 6)} for lvl, t in table.items()}
    record["supported_level"] = level

    base_groups = None if level == "national" else rf[level]
    increments: dict[str, dict] = {}
    for key, spec in STRATIFIERS.items():
        col = spec["column"]
        if col not in rf.columns:
            continue
        inc = incremental_eta2(rv, base_groups, rf[col], min_group=rule["min_group"])
        sizes = rf[col].value_counts()
        increments[key] = {"increment": _r(inc["increment"]), "combined": _r(inc["combined"]),
                           "n_by_class": {str(k): int(v) for k, v in sizes.items()}}
    record["incremental"] = increments

    candidates = [(k, v["increment"]) for k, v in increments.items()
                  if v["increment"] is not None and v["increment"] >= rule["split_incremental_min"]
                  and sum(1 for n in v["n_by_class"].values() if n >= rule["stratum_min_n"]) >= 2]
    basis = [f"Level III adjusted eta-squared {_r((table.get('l3') or {}).get('eta2_adj'))}; "
             f"supported level {level}."]
    if candidates:
        # one candidate is tested: the split that explains the most. A second
        # qualifying split is recorded with its increment and nothing else.
        key = max(candidates, key=lambda kv: kv[1])[0]
        record["split_evaluated"] = key
        col = STRATIFIERS[key]["column"]
        worst = None
        per_class = {}
        for label, members in rf.groupby(col):
            if len(members) < rule["stratum_min_n"]:
                continue
            clusters = _clusters(members)
            ev = values[(frame[col] == label) & values.notna()]
            got = band_flip_rate(rv.loc[members.index], clusters, ev, entry,
                                 n_boot=n_boot, seed=seed)
            per_class[str(label)] = got
            if got["flip_mean"] is not None:
                worst = got["flip_mean"] if worst is None else max(worst, got["flip_mean"])
        stability = _flip_verdict(worst, rule)
        auc_unsplit = _yardstick(values, frame, entry, None)
        auc_split = _yardstick(values, frame, entry, col)
        yard_ok = (auc_unsplit is None or auc_split is None
                   or auc_split >= auc_unsplit - rule["yardstick_max_auc_loss"])
        record["stability"] = {"worst_flip_mean": _r(worst), "verdict": stability,
                               "by_class": per_class}
        record["yardstick"] = {"auc_unsplit": _r(auc_unsplit), "auc_split": _r(auc_split),
                               "verdict": "ok" if yard_ok else "split_lowers_agreement"}
        if stability in ("accept", "exploratory") and yard_ok:
            record["split"] = key
            record["split_status"] = stability
            basis.append(f"{key} adds {increments[key]['increment']} of adjusted eta-squared, "
                         f"resampling flip rate {_r(worst)} ({stability}).")
        else:
            basis.append(f"{key} adds {increments[key]['increment']} but was not adopted "
                         f"(stability {stability}, yardstick "
                         f"{'ok' if yard_ok else 'lower'}).")
    else:
        basis.append("No class split adds the required 0.05 with two classes of 15 or more.")
    record["decision_basis"] = " ".join(basis)
    return record


def _clusters(members: pd.DataFrame) -> pd.Series:
    h12 = members["huc12"].astype(object) if "huc12" in members.columns else None
    h8 = members["huc8"].astype(object) if "huc8" in members.columns else None
    if h12 is None:
        return h8 if h8 is not None else pd.Series(members.index.astype(str), index=members.index)
    return h12.where(h12.notna(), h8) if h8 is not None else h12


def _r(x: Any, digits: int = 4) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, digits)


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def build_registry(results: dict[str, dict], *, inputs: dict, decided_on: str,
                   rule: Optional[dict] = None) -> dict:
    return {"version": 1, "analysis_version": ANALYSIS_VERSION, "decided_on": decided_on,
            "inputs": inputs, "rule": {**DEFAULT_RULE, **(rule or {})},
            "stratifiers": STRATIFIERS,
            "metrics": {k: results[k] for k in sorted(results)}}


@lru_cache(maxsize=1)
def load_registry(path: Optional[str] = None) -> dict:
    p = Path(path) if path else REGISTRY_PATH
    return (read_yaml(p) or {}) if p.exists() else {}


def clear_cache() -> None:
    load_registry.cache_clear()


def registry_sha256() -> Optional[str]:
    p = REGISTRY_PATH
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def entry_for(metric: str, registry: Optional[dict] = None) -> Optional[dict]:
    reg = registry if registry is not None else load_registry()
    return (reg.get("metrics") or {}).get(str(metric))


def stratifier_for(metric: str, registry: Optional[dict] = None) -> Optional[dict]:
    """The adopted class split of a metric, with the machine-readable
    definition DEEP needs to choose a curve set from a reach's slope or area."""
    reg = registry if registry is not None else load_registry()
    entry = entry_for(metric, reg) or {}
    key = entry.get("split")
    if not key:
        return None
    spec = (reg.get("stratifiers") or STRATIFIERS).get(key)
    if not spec:
        return None
    return {"key": key, **{k: spec[k] for k in ("variable", "column", "units", "source",
                                                "breaks", "right", "labels", "display")},
            "registryVersion": reg.get("version"), "status": entry.get("split_status")}


def classify(values: Any, stratifier: dict) -> pd.Series:
    """Class labels of a numeric series under a declared stratifier."""
    v = pd.to_numeric(pd.Series(values), errors="coerce")
    breaks = [float(b) for b in stratifier["breaks"]]
    labels = list(stratifier["labels"])
    right = bool(stratifier.get("right"))
    floor = 0.0 if right else -np.inf
    bins = [-np.inf if not right else floor] + breaks + [np.inf]
    cut = pd.cut(v.where(v > 0) if right else v.where(v >= 0), bins=bins, labels=labels,
                 right=right, include_lowest=not right)
    return cut.astype(object).where(cut.notna(), None)
