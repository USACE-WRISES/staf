"""Can a method recover a reference condition that was withheld from it?

The question methodology 0.13 has to answer is not whether a candidate basis
agrees with our curve. It is whether, in a region whose least-disturbed stations
are hidden, a method can put the reference level back where it belongs. This
module runs that test directly: hide a region's reference stations, let a method
predict the reference anchors from what remains, and score the prediction against
the anchors that were hidden.

Four things this is careful about, because each of them can manufacture a result:

* **Sample size.** A curve built on half a pool is less stable than one built on
  the whole pool, so a split-half flip rate overstates how variable the reference
  answer really is. :func:`reference_variability` therefore draws the SAME number
  of stations a candidate was fitted on, so the comparison is like for like, and
  reports where the candidate falls in that distribution rather than against a
  national constant.
* **Selection.** A percentile or a model specification chosen by looking at the
  answer is not a method, it is a fit. Selection runs on development regions and
  evaluation on regions held out of it (:func:`split_regions`).
* **Extrapolation.** A model asked for the metric at zero disturbance in a setting
  where nothing near zero disturbance was ever observed is extrapolating, and
  :func:`extrapolation_share` says so instead of letting the prediction pass as
  an ordinary one.
* **Circularity in the external labels.** EPA's ``RT_NRSA`` designation is a
  screen built partly from water chemistry and physical habitat, so a chemistry
  metric tested against it is being tested against a label its own values helped
  set. :func:`label_dependence` measures that for each metric instead of assuming
  it away, and the AUC is supporting evidence either way: it asks whether
  healthier streams rank above impaired ones, never whether the reference level
  or the class boundaries are right.

Pure: frames in, records out. No file writes, no network.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from . import curves
from .basis_validation import calls_for, curve_for, disagreement

#: Disturbance columns the screen itself uses, so "zero disturbance" means the
#: same thing here as it does in `reference_screen`.
PRESSURE_COLUMNS = ("pctimp2019ws", "agriculture_ws", "rddensws", "dor",
                    "nabd_densws", "npdesdensws", "mines_ws")


# --------------------------------------------------------------------------- #
# what a curve is anchored on
# --------------------------------------------------------------------------- #
def anchors_of(values: Any) -> Optional[tuple[float, float]]:
    """(q25, q75), the two numbers the engine's seed is built from."""
    v = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if len(v) < 5:
        return None
    return float(v.quantile(0.25)), float(v.quantile(0.75))


def recovery_error(predicted: Optional[tuple[float, float]],
                   truth: Optional[tuple[float, float]]) -> dict:
    """How far a predicted reference level sits from the withheld one.

    Reported in units of the true reference interquartile range, because an
    absolute error means nothing across metrics measured in taxa, microsiemens
    and percent. Zero is exact recovery; 1.0 means the anchor is off by the whole
    width of the reference middle.
    """
    out = {"err_q25": None, "err_q75": None, "err_abs_mean": None}
    if predicted is None or truth is None:
        return out
    iqr = truth[1] - truth[0]
    if not np.isfinite(iqr) or iqr <= 0:
        return out
    e25, e75 = (predicted[0] - truth[0]) / iqr, (predicted[1] - truth[1]) / iqr
    out.update({"err_q25": round(float(e25), 4), "err_q75": round(float(e75), 4),
                "err_abs_mean": round(float((abs(e25) + abs(e75)) / 2), 4)})
    return out


# --------------------------------------------------------------------------- #
# reference variability, matched to the candidate's sample size
# --------------------------------------------------------------------------- #
def reference_variability(ref_values: Any, metric_config: dict, eval_values: Any, *,
                          n_draw: int, clusters: Optional[Any] = None,
                          n_draws: int = 100, seed: int = 7,
                          replace: bool = False) -> dict:
    """How differently the reference answer itself lands when it has only ``n_draw``
    stations to work from.

    Each draw takes ``n_draw`` reference stations by watershed cluster, builds the
    curve, and compares its class calls with the FULL reference curve. The result
    is the variability of the reference answer at that sample size, which is the
    thing a candidate fitted on ``n_draw`` values has to be compared against. A
    split in half instead would answer a different question, at a sample size no
    candidate necessarily shares.

    Without replacement a draw of the whole pool is the pool itself, so at
    ``n_draw`` equal to the pool's size every draw would agree perfectly.
    ``replace=True`` draws catchments with replacement instead, the bootstrap
    :func:`anchor_interval` rests on, which is how uncertain the reference answer
    is at its own size.
    """
    vals = pd.to_numeric(pd.Series(ref_values), errors="coerce").dropna()
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    out: dict[str, Any] = {"n_draws_valid": 0, "median": None, "p90": None,
                           "n_reference": int(len(vals)), "n_draw": int(n_draw),
                           "replace": bool(replace)}
    full, _ = curve_for(vals, metric_config)
    if full is None or not len(ev) or n_draw < 5 or n_draw > len(vals):
        return out
    base = calls_for(full, ev)
    key = (pd.Series(clusters).astype(object).reindex(vals.index)
           if clusters is not None else pd.Series(vals.index.astype(str), index=vals.index))
    key = key.where(key.notna(), pd.Series(vals.index.astype(str), index=vals.index))
    by_cluster: dict[Any, list] = {}
    for ix, c in key.items():
        by_cluster.setdefault(c, []).append(ix)
    names = list(by_cluster)
    rng = np.random.default_rng(int(seed))
    flips = []
    for _ in range(int(n_draws)):
        picked: list = []
        if replace:
            while len(picked) < n_draw:     # whole catchments, drawn with replacement
                picked.extend(by_cluster[names[int(rng.integers(len(names)))]])
            sample = vals.loc[picked[:n_draw]].reset_index(drop=True)
        else:
            order = rng.permutation(len(names))
            for i in order:                 # whole catchments until the size is met
                picked.extend(by_cluster[names[i]])
                if len(picked) >= n_draw:
                    break
            sample = vals.loc[picked[:n_draw]]
        pts, _ = curve_for(sample, metric_config)
        if pts is None:
            continue
        got = disagreement(base, calls_for(pts, ev))
        if got is not None:
            flips.append(got)
    if not flips:
        return out
    arr = np.asarray(flips, dtype=float)
    out.update({"n_draws_valid": len(flips),
                "median": round(float(np.median(arr)), 4),
                "p90": round(float(np.quantile(arr, 0.90)), 4),
                "draws": [round(float(x), 4) for x in arr]})
    return out


def exceedance(candidate_flip: Optional[float], variability: dict) -> Optional[float]:
    """Share of same-sized reference draws that disagree at least as much as the
    candidate does.

    Near 1.0 the candidate is ordinary for that sample size. Near 0.0 it sits
    outside anything the reference answer does on its own, which is the only
    footing for calling its disagreement real rather than sampling.
    """
    draws = (variability or {}).get("draws")
    if candidate_flip is None or not draws:
        return None
    arr = np.asarray(draws, dtype=float)
    return round(float(np.mean(arr >= candidate_flip)), 4)


# --------------------------------------------------------------------------- #
# development and evaluation
# --------------------------------------------------------------------------- #
def split_regions(region_codes: Iterable[str], *, frac_dev: float = 0.5,
                  seed: int = 11) -> tuple[list[str], list[str]]:
    """Development and evaluation regions, fixed by seed and taken before anything
    is measured. Selecting on the same regions a method is scored on is how a fit
    comes to look like a method."""
    codes = sorted({str(c) for c in region_codes})
    rng = np.random.default_rng(int(seed))
    order = rng.permutation(len(codes))
    cut = int(round(len(codes) * float(frac_dev)))
    dev = sorted(codes[i] for i in order[:cut])
    ev = sorted(codes[i] for i in order[cut:])
    return dev, ev


# --------------------------------------------------------------------------- #
# method 1: a selected percentile of what is left when reference is withheld
# --------------------------------------------------------------------------- #
def percentile_anchors(values: Any, p_low: float, p_high: float
                       ) -> Optional[tuple[float, float]]:
    v = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if len(v) < 5:
        return None
    lo, hi = float(v.quantile(p_low)), float(v.quantile(p_high))
    return (lo, hi) if hi > lo else None


def calibrate_percentiles(cells: Iterable[dict], *, grid: Optional[Iterable[float]] = None
                          ) -> dict:
    """The pair of percentiles of the non-reference population that best reproduces
    the reference anchors, learned on development cells only.

    ``cells`` are ``{"blind": values, "truth": (q25, q75)}``. The rule is one pair
    per metric, chosen to minimise the median absolute recovery error across the
    development regions, which is the same loss the evaluation reports.
    """
    grid = list(grid if grid is not None else np.round(np.arange(0.05, 1.00, 0.05), 2))
    rows = [c for c in cells if c.get("truth") and c.get("blind") is not None]
    best = {"p_low": None, "p_high": None, "dev_err": None, "n_dev_cells": len(rows)}
    if not rows:
        return best
    for p_low in grid:
        for p_high in grid:
            if p_high <= p_low:
                continue
            errs = []
            for c in rows:
                pred = percentile_anchors(c["blind"], p_low, p_high)
                got = recovery_error(pred, c["truth"])
                if got["err_abs_mean"] is not None:
                    errs.append(got["err_abs_mean"])
            if not errs:
                continue
            med = float(np.median(errs))
            if best["dev_err"] is None or med < best["dev_err"]:
                best = {"p_low": float(p_low), "p_high": float(p_high),
                        "dev_err": round(med, 4), "n_dev_cells": len(rows)}
    return best


# --------------------------------------------------------------------------- #
# method 2: a stressor-response model, predicted at zero disturbance
# --------------------------------------------------------------------------- #
#: Specifications to choose between on development data, then freeze. Named so the
#: chosen one is reported rather than implied.
SPECS = ("linear", "spline", "interaction", "quantile", "mixed")


def _design(rows: pd.DataFrame, natural: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=rows.index)
    for c in natural:
        v = pd.to_numeric(rows[c], errors="coerce")
        out[c] = np.log10(v.clip(lower=1e-5)) if c in (
            "drainage_area_sqkm", "nhd_slope", "hydrlcondws") else v
    for c in PRESSURE_COLUMNS:
        if c in rows.columns:
            out[c] = np.log1p(pd.to_numeric(rows[c], errors="coerce").clip(lower=0))
    return out


def fit_stressor_response(train: pd.DataFrame, y: pd.Series, natural: list[str], *,
                          spec: str = "linear") -> Optional[dict]:
    """Fit metric ~ natural setting + disturbance on the training regions.

    Uses the project's own statsmodels idiom (``smf.ols`` and friends, as in
    ``diagnostics.py`` and ``regional.py``). Returns a fitted object plus the
    column order, or None when the fit cannot be made.
    """
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    X = _design(train, natural)
    d = X.copy()
    d["__y"] = pd.to_numeric(y, errors="coerce")
    d = d.replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 40:
        return None
    nat = [c for c in natural if c in d.columns]
    prs = [c for c in PRESSURE_COLUMNS if c in d.columns]
    if not nat or not prs:
        return None
    nat_terms = {"spline": [f"cr({c}, df=4)" for c in nat]}.get(spec, list(nat))
    rhs = " + ".join(nat_terms + prs)
    if spec == "interaction":
        rhs += " + " + " + ".join(f"{a}:{b}" for a in nat[:2] for b in prs[:2])
    formula = f"__y ~ {rhs}"
    try:
        if spec == "quantile":
            fit = smf.quantreg(formula, d).fit(q=0.75)
        elif spec == "mixed" and "__group" in train.columns:
            d["__group"] = train.loc[d.index, "__group"].to_numpy()
            fit = smf.mixedlm(formula, d, groups=d["__group"]).fit(reml=False)
        else:
            fit = smf.ols(formula, d).fit()
    except Exception:                     # a specification that will not converge
        return None                       # is simply not available for this metric
    return {"fit": fit, "natural": nat, "pressure": prs, "spec": spec,
            "n_train": int(len(d)), "formula": formula,
            "sm": sm}


def predict_reference(model: dict, rows: pd.DataFrame) -> Optional[pd.Series]:
    """The metric each site would carry at zero disturbance, holding its natural
    setting as observed. This is the modelled reference expectation."""
    if not model:
        return None
    X = _design(rows, model["natural"]).copy()
    for c in model["pressure"]:
        X[c] = 0.0                        # log1p(0) = 0, the screen's own zero
    X = X.replace([np.inf, -np.inf], np.nan)
    keep = X.dropna().index
    if not len(keep):
        return None
    try:
        pred = model["fit"].predict(X.loc[keep])
    except Exception:
        return None
    return pd.Series(np.asarray(pred, dtype=float), index=keep)


def extrapolation_share(train: pd.DataFrame, target: pd.DataFrame, natural: list[str], *,
                        pressure_quantile: float = 0.25) -> Optional[float]:
    """Share of the target region's sites whose natural setting is represented among
    the LOW-DISTURBANCE training observations.

    A model asked for the value at zero disturbance is only interpolating where the
    training data actually holds lightly disturbed streams of that kind. Near zero
    the prediction is an extrapolation along the disturbance axis and has to be
    reported as one.
    """
    nat = [c for c in natural if c in train.columns and c in target.columns]
    if not nat or not len(target):
        return None
    load = sum(pd.to_numeric(train[c], errors="coerce").rank(pct=True)
               for c in PRESSURE_COLUMNS if c in train.columns)
    if load is None or not len(load):
        return None
    clean = train[load <= load.quantile(pressure_quantile)]
    if len(clean) < 10:
        return None
    lo = {c: pd.to_numeric(clean[c], errors="coerce").quantile(0.05) for c in nat}
    hi = {c: pd.to_numeric(clean[c], errors="coerce").quantile(0.95) for c in nat}
    inside = pd.Series(True, index=target.index)
    for c in nat:
        v = pd.to_numeric(target[c], errors="coerce")
        inside &= v.between(lo[c], hi[c])
    return round(float(inside.mean()), 4)


# --------------------------------------------------------------------------- #
# how far the external labels lean on the metric being tested
# --------------------------------------------------------------------------- #
def label_dependence(metric_values: Any, labels: Any) -> dict:
    """How strongly the raw metric itself separates EPA's R from Im, before any
    curve is involved.

    EPA's ``RT_NRSA`` screen is built partly from water chemistry and physical
    habitat, so for those metrics a high separation is partly the label reading
    back its own input. It is reported next to every AUC so the AUC is read as
    supporting evidence rather than proof.
    """
    from . import discrimination as dz
    v = pd.to_numeric(pd.Series(metric_values), errors="coerce")
    lab = pd.Series(labels).astype(object).reindex(v.index)
    good, bad = v[lab == "R"].dropna(), v[lab == "Im"].dropna()
    out = {"n_R": int(len(good)), "n_Im": int(len(bad)), "raw_auc": None}
    if len(good) >= dz.MIN_GROUP and len(bad) >= dz.MIN_GROUP:
        out["raw_auc"] = dz.auc(good, bad)
    return out


def within_region_auc(points_by_region: dict, values_by_region: dict,
                      labels_by_region: dict) -> dict:
    """AUC computed inside each region and then summarised, beside the pooled one.

    Pooling regions can manufacture separation out of nothing more than regional
    offsets: if reference-designated sites cluster in regions whose values run
    high, the pooled area looks strong while no region separates anything. The
    two numbers together say which is happening.
    """
    from . import discrimination as dz
    per = {}
    for code, pts in points_by_region.items():
        if pts is None:
            continue
        vals = values_by_region.get(code)
        labs = labels_by_region.get(code)
        if vals is None or labs is None:
            continue
        idx = dz.score_values(pts, vals)
        lab = pd.Series(labs).astype(object).reindex(idx.index)
        good, bad = idx[lab == "R"].dropna(), idx[lab == "Im"].dropna()
        if len(good) >= dz.MIN_GROUP and len(bad) >= dz.MIN_GROUP:
            per[code] = dz.auc(good, bad)
    vals = [v for v in per.values() if v is not None]
    return {"per_region": per, "n_regions": len(vals),
            "median": round(float(np.median(vals)), 4) if vals else None}


# --------------------------------------------------------------------------- #
# C1: is the recovered anchor inside the truth's own interval?
# --------------------------------------------------------------------------- #
def anchor_interval(ref_values: Any, *, clusters: Optional[Any] = None,
                    n_boot: int = 400, seed: int = 7, level: float = 0.90) -> dict:
    """A bootstrap interval on the TRUE anchors, by resampling the reference pool.

    Resampled by watershed cluster, so stations of one catchment move together and
    the interval is not narrowed by treating them as independent draws. This is
    what containment is tested against: it says how well the reference data pin
    their own answer, and a candidate inside it is not distinguishable from the
    truth given that data. The candidate's own error is never added to it, so this
    is a containment test and not an error allowance.
    """
    vals = pd.to_numeric(pd.Series(ref_values), errors="coerce").dropna()
    out: dict[str, Any] = {"q25": None, "q75": None, "n": int(len(vals)),
                           "n_boot_valid": 0}
    if len(vals) < 8:
        return out
    key = (pd.Series(clusters).astype(object).reindex(vals.index)
           if clusters is not None else pd.Series(vals.index.astype(str), index=vals.index))
    key = key.where(key.notna(), pd.Series(vals.index.astype(str), index=vals.index))
    groups: dict[Any, list] = {}
    for ix, c in key.items():
        groups.setdefault(c, []).append(ix)
    names = list(groups)
    rng = np.random.default_rng(int(seed))
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    draws = []
    for _ in range(int(n_boot)):
        pick = rng.choice(len(names), size=len(names), replace=True)
        ix = [i for p in pick for i in groups[names[p]]]
        got = anchors_of(vals.loc[ix])
        if got:
            draws.append(got)
    if not draws:
        return out
    arr = np.asarray(draws, dtype=float)
    out.update({"q25": (float(np.quantile(arr[:, 0], lo_q)), float(np.quantile(arr[:, 0], hi_q))),
                "q75": (float(np.quantile(arr[:, 1], lo_q)), float(np.quantile(arr[:, 1], hi_q))),
                "n_boot_valid": len(draws)})
    return out


def contains(predicted: Optional[tuple[float, float]], interval: dict) -> dict:
    """Whether each recovered anchor falls inside the truth's own interval (C1)."""
    out = {"q25_in": None, "q75_in": None, "both_in": None}
    if predicted is None or interval.get("q25") is None:
        return out
    a = interval["q25"][0] <= predicted[0] <= interval["q25"][1]
    b = interval["q75"][0] <= predicted[1] <= interval["q75"][1]
    out.update({"q25_in": bool(a), "q75_in": bool(b), "both_in": bool(a and b)})
    return out


# --------------------------------------------------------------------------- #
# C2: what the difference does to a site's score, and in which direction
# --------------------------------------------------------------------------- #
_BAND_ORDER = {"NF": 0, "AR": 1, "F": 2}


def band_of(index: Optional[float]) -> Optional[str]:
    if index is None or index != index:
        return None
    return "NF" if index <= 0.39 else "AR" if index <= 0.69 else "F"


def score_outcomes(points_reference: Optional[list[dict]],
                   points_candidate: Optional[list[dict]],
                   eval_values: Any) -> dict:
    """What a candidate curve does to the region's own sites, against the reference
    curve, on the scale decisions are actually made on.

    Total disagreement hides the failure that matters. A curve anchored on a
    degraded population promotes degraded sites, and a promotion is not the same
    event as a demotion: it tells a user a stream is healthier than the reference
    comparison would. So categories are split by direction and the net reported,
    because symmetric disagreement is noise and one-directional promotion is bias.
    """
    from . import discrimination as dz
    out: dict[str, Any] = {"n_scored": 0, "index_diff_median": None,
                           "index_diff_p90": None, "promoted": None,
                           "demoted": None, "disagree": None, "net_optimism": None}
    if points_reference is None or points_candidate is None:
        return out
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    if not len(ev):
        return out
    a = dz.score_values(points_reference, ev)
    b = dz.score_values(points_candidate, ev)
    both = pd.concat([a, b], axis=1).dropna()
    if not len(both):
        return out
    ia, ib = both.iloc[:, 0], both.iloc[:, 1]
    ba = [band_of(x) for x in ia]
    bb = [band_of(x) for x in ib]
    up = sum(1 for x, y in zip(ba, bb) if _BAND_ORDER[y] > _BAND_ORDER[x])
    dn = sum(1 for x, y in zip(ba, bb) if _BAND_ORDER[y] < _BAND_ORDER[x])
    n = len(both)
    out.update({"n_scored": int(n),
                "index_diff_median": round(float(np.median(ib - ia)), 4),
                "index_diff_p90": round(float(np.quantile(np.abs(ib - ia), 0.90)), 4),
                "promoted": round(up / n, 4), "demoted": round(dn / n, 4),
                "disagree": round((up + dn) / n, 4),
                "net_optimism": round((up - dn) / n, 4)})
    return out


# --------------------------------------------------------------------------- #
# Option 2: explicit, named relaxations of the pressure screen
# --------------------------------------------------------------------------- #
#: The strict screen as reference_screen applies it, then progressively relaxed
#: alternatives. Each names its exact thresholds and why the change is defensible,
#: so a verdict can say which threshold moved rather than "a looser screen".
SCREENS: dict[str, dict] = {
    "strict": {
        "why": "methodology 0.12 as published (least-disturbed-v1, strict tier)",
        "thresholds": {"pctimp2019ws": 1.0, "agriculture_ws": 10.0, "rddensws": 2.0,
                       "dor": 2.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
                       "mines_ws": 0.0}},
    "relaxed": {
        "why": "the project's own relaxed tier, already defined for the REF-07 local "
               "comparison: impervious 3, agriculture 25, DOR 5",
        "thresholds": {"pctimp2019ws": 3.0, "agriculture_ws": 25.0, "rddensws": 2.0,
                       "dor": 5.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
                       "mines_ws": 0.0}},
    "agriculture_50": {
        "why": "agriculture alone relaxed to 50 percent, every other pressure held at "
               "the relaxed tier. Defensible where row-crop conversion is the regional "
               "condition rather than a site-level impact, and it isolates one "
               "threshold so any effect is attributable to it",
        "thresholds": {"pctimp2019ws": 3.0, "agriculture_ws": 50.0, "rddensws": 2.0,
                       "dor": 5.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
                       "mines_ws": 0.0}},
    "agriculture_75": {
        "why": "agriculture to 75 percent with impervious and roads eased a step. The "
               "loosest screen tested, included to find where a region like the "
               "Eastern Corn Belt Plains first admits any station at all",
        "thresholds": {"pctimp2019ws": 5.0, "agriculture_ws": 75.0, "rddensws": 3.0,
                       "dor": 5.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
                       "mines_ws": 0.0}},
    "point_source_strict": {
        "why": "agriculture to 50 percent but discharges, mines and regulation held at "
               "zero. For water chemistry, where point sources and mine drainage "
               "dominate the signal and diffuse row-crop cover does not act the same "
               "way, so the pressures held strict are the ones the metric responds to",
        "thresholds": {"pctimp2019ws": 3.0, "agriculture_ws": 50.0, "rddensws": 2.0,
                       "dor": 2.0, "nabd_densws": 0.0, "npdesdensws": 0.0,
                       "mines_ws": 0.0}},
}


def apply_screen(rows: pd.DataFrame, name: str) -> pd.Series:
    """Boolean mask of the stations a named screen admits.

    Applied to the columns reference_screen itself uses, so a relaxed screen here
    means the same quantities measured the same way with stated thresholds moved,
    not a different notion of disturbance.
    """
    spec = SCREENS[name]["thresholds"]
    ok = pd.Series(True, index=rows.index)
    for col, limit in spec.items():
        if col not in rows.columns:
            continue
        v = pd.to_numeric(rows[col], errors="coerce")
        ok &= v.notna() & ((v <= limit) if limit > 0 else (v <= 0))
    return ok


def screen_residual(rows: pd.DataFrame, mask: Any) -> dict:
    """What disturbance remains in a screened population, so a relaxed screen has to
    declare what it admitted and not only how many stations it reached."""
    sub = rows[mask]
    out: dict[str, Any] = {"n": int(len(sub)),
                           "n_huc12": int(sub["huc12"].nunique()) if len(sub) else 0}
    for col in ("agriculture_ws", "pctimp2019ws", "rddensws"):
        if col in sub.columns and len(sub):
            v = pd.to_numeric(sub[col], errors="coerce")
            out[f"{col}_median"] = round(float(v.median()), 3)
            out[f"{col}_max"] = round(float(v.max()), 3)
    return out


# =========================================================================== #
# Round two (Pre-registration II)
#
# Everything above this line is round one and is left exactly as it ran, so its
# published results still reproduce. What follows corrects the five defects the
# round-one test was found to have: an untransformed response, a specification
# chosen at one prediction point and applied at another, a region's own level
# fitted and then thrown away, transfer without adjustment, and candidates that
# never stated an interval of their own.
# =========================================================================== #
def cell_seed(*parts: Any, seed: int = 11) -> int:
    """One cell's seed, from what the cell is rather than when it runs.

    ``zlib.crc32`` of the metric, region, basis, regime and run seed, so a result
    does not depend on the order cells run in or on which metrics a run was
    restricted to, and a parallel run writes the same table as a serial one.
    """
    import zlib
    key = "|".join(str(p) for p in parts) + f"|{int(seed)}"
    return int(zlib.crc32(key.encode("utf-8")))


# --------------------------------------------------------------------------- #
# the response transform, fixed by what the quantity is
# --------------------------------------------------------------------------- #
#: Concentrations and turbidity are fitted on log10. pH is already logarithmic,
#: and richness, diversity and percentages are left as measured. Chosen by the
#: kind of quantity, not by looking at the data, so the choice cannot follow a
#: result. Round one fitted every response untransformed, and its model errors
#: blew out on exactly these five.
LOG10_METRICS = frozenset({"chem_COND", "chem_PTL", "chem_NTL_DISS", "chem_TURB",
                           "chem_CHLA"})


def transform_for(metric: str) -> str:
    return "log10" if metric in LOG10_METRICS else "identity"


def log_offset(values: Any) -> float:
    """What a zero becomes before logging: half the smallest positive value."""
    v = pd.to_numeric(pd.Series(values), errors="coerce")
    pos = v[v > 0]
    return float(pos.min()) / 2.0 if len(pos) else 1.0


def to_scale(values: Any, kind: str, offset: float = 1.0) -> np.ndarray:
    v = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)
    if kind != "log10":
        return v
    v = np.where(np.isnan(v), np.nan, np.where(v > 0, v, float(offset)))
    return np.log10(v)


def from_scale(values: Any, kind: str) -> np.ndarray:
    a = np.asarray(values, dtype=float)
    return np.power(10.0, a) if kind == "log10" else a


# --------------------------------------------------------------------------- #
# the prediction point, and a curve from two anchors
# --------------------------------------------------------------------------- #
def reference_pressure_vector(frame: pd.DataFrame) -> dict:
    """The median pressure of the nationally screened reference pool.

    An observed combination the screen already calls least disturbed, rather than a
    vector of zeros that may never occur together. Promoted unchanged from
    ``run_three_options.low_disturbance_target``, and used for choosing a
    specification as well as for scoring it, which round one did not do.
    """
    ref = frame[frame["pass_strict"].astype(bool)]
    return {c: float(pd.to_numeric(ref[c], errors="coerce").median())
            for c in PRESSURE_COLUMNS if c in ref.columns}


def curve_from_anchors(anchors: Optional[tuple[float, float]],
                       metric_config: dict) -> Optional[list[dict]]:
    """A curve whose q25 and q75 are ``anchors``, built by the shipping engine.

    The engine is handed a synthetic sample carrying exactly those quartiles, so the
    shape and the tails are the published geometry. The sample is held inside the
    metric's declared domain. Round one's sample ran a full IQR below q25, which
    goes negative when q25 is smaller than the IQR, and with ``signed_scale``
    undeclared the engine then chose its signed seed. That seed, clipped at zero,
    is the same function on the domain as the non-negative one (its 0.30 point at
    three sevenths of q25 lies on the line from the origin to q25), so no class
    call changed. Holding the sample in the domain gives the canonical point list.
    """
    if not anchors:
        return None
    lo, hi = float(anchors[0]), float(anchors[1])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    span = hi - lo
    sample = np.concatenate([np.linspace(lo - span, lo, 25), np.linspace(lo, hi, 50),
                             np.linspace(hi, hi + span, 25)])
    d_min, d_max = metric_config.get("domain_min"), metric_config.get("domain_max")
    if d_min is not None and lo >= float(d_min):
        sample = np.maximum(sample, float(d_min))
    if d_max is not None and hi <= float(d_max):
        sample = np.minimum(sample, float(d_max))
    pts, _ = curve_for(pd.Series(sample), metric_config)
    return pts


# --------------------------------------------------------------------------- #
# stressor-response models, properly specified
# --------------------------------------------------------------------------- #
#: Round two's specifications. Round one's ``SPECS`` and ``fit_stressor_response``
#: stay as they were.
MODEL_SPECS = ("linear", "spline", "tensor", "interaction", "mixed_local", "quantile")
#: The two pressures every natural covariate is crossed with in ``interaction``.
#: Round one crossed only the first two covariates with the first two pressures.
INTERACT_WITH = ("agriculture_ws", "pctimp2019ws")
CLIMATE_COVARIATES = ("tmean8110ws", "precip8110ws")


def fit_model(train: pd.DataFrame, y: Any, natural: list[str], *, spec: str,
              kind: str = "identity", offset: float = 1.0, pressures: bool = True,
              groups: Any = None) -> Optional[dict]:
    """Fit one round-two specification. ``y`` and ``groups`` are positional with
    ``train``'s rows.

    The response is on the scale ``kind`` names. Residuals are kept centred within
    each training region, so a draw from them is one region's noise and not the
    spread between regions; a mixed fit's conditional residuals already are.
    ``pressures=False`` fits the natural setting alone, which is how a reference
    expectation is fitted on reference streams (basis 3b).

    Returns ``None`` when the data cannot support the fit. An unknown ``spec`` is
    a programming error and raises.
    """
    import statsmodels.formula.api as smf

    if spec not in MODEL_SPECS:
        raise ValueError(f"unknown specification {spec!r}")
    X = _design(train, natural).reset_index(drop=True)
    nat = [c for c in natural if c in X.columns]
    prs = [c for c in PRESSURE_COLUMNS if c in X.columns] if pressures else []
    d = X[nat + prs].copy()
    d["__y"] = to_scale(np.asarray(y, dtype=float), kind, offset)
    d["__g"] = (pd.Series(np.asarray(groups, dtype=object)).astype(str).to_numpy()
                if groups is not None else "all")
    d = d.replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 40:
        return None
    # a column with no spread in this training set carries nothing, and it makes a
    # mixed fit singular
    nat = [c for c in nat if float(d[c].std()) > 0]
    prs = [c for c in prs if float(d[c].std()) > 0]
    if not nat:
        return None
    if spec == "spline":
        terms = [f"cr({c}, df=4)" for c in nat]
    elif spec == "tensor":
        pair = [c for c in ("drainage_area_sqkm", "nhd_slope") if c in nat]
        if len(pair) < 2:
            return None
        terms = ([f"te(cr({pair[0]}, df=3), cr({pair[1]}, df=3))"]
                 + [c for c in nat if c not in pair])
    else:
        terms = list(nat)
    rhs = " + ".join(terms + prs)
    if spec == "interaction":
        extra = [f"{a}:{b}" for a in nat for b in INTERACT_WITH if b in prs]
        if extra:
            rhs += " + " + " + ".join(extra)
    formula = f"__y ~ {rhs}"
    fits = None
    resid = None
    try:
        if spec == "quantile":
            fits = {q: smf.quantreg(formula, d).fit(q=q, max_iter=5000) for q in (0.25, 0.75)}
            fit = fits[0.25]
        elif spec == "mixed_local":
            fit = smf.mixedlm(formula, d, groups=d["__g"]).fit(reml=False)
            resid = np.asarray(fit.resid, dtype=float)
        else:
            fit = smf.ols(formula, d).fit()
            r = pd.Series(np.asarray(fit.resid, dtype=float), index=d.index)
            resid = (r - r.groupby(d["__g"]).transform("mean")).to_numpy()
    except Exception:                     # a fit that will not converge is not available
        return None
    return {"fit": fit, "fits": fits, "spec": spec, "kind": kind, "offset": float(offset),
            "natural": nat, "pressure": prs, "formula": formula, "n_train": int(len(d)),
            "resid": resid, "groups": sorted(set(d["__g"]))}


def predict_model(model: dict, rows: pd.DataFrame, target: Optional[dict], *,
                  group: Optional[str] = None) -> dict:
    """Predictions on the fitted scale for ``rows`` with pressures held at ``target``.

    ``{"mid": array}``, or ``{"q25": array, "q75": array}`` for the quantile spec,
    with ``n`` rows predicted. For ``mixed_local``, ``blup`` is the named region's
    own intercept, added to every row, or ``None`` when the fit never saw that
    region, in which case the prediction is the fixed effects alone and says so.
    """
    X = _design(rows, model["natural"]).reset_index(drop=True)
    for c in model["pressure"]:
        X[c] = float(np.log1p(max(0.0, float((target or {}).get(c, 0.0)))))
    X = X[model["natural"] + model["pressure"]].replace([np.inf, -np.inf], np.nan).dropna()
    out: dict[str, Any] = {"n": int(len(X)), "blup": None}
    if not len(X):
        return out
    if model["spec"] == "quantile":
        out["q25"] = np.asarray(model["fits"][0.25].predict(X), dtype=float)
        out["q75"] = np.asarray(model["fits"][0.75].predict(X), dtype=float)
        return out
    pred = np.asarray(model["fit"].predict(X), dtype=float)
    if model["spec"] == "mixed_local" and group is not None:
        re = model["fit"].random_effects.get(str(group))
        if re is not None:
            out["blup"] = float(np.asarray(re, dtype=float)[0])
            pred = pred + out["blup"]
    out["mid"] = pred
    return out


def model_anchors(model: Optional[dict], rows: pd.DataFrame, target: Optional[dict], *,
                  group: Optional[str] = None, rng: Any = None, n_resid: int = 200,
                  fitted_only: bool = False) -> dict:
    """The anchors a fitted model implies for ``rows`` at ``target``, on the metric's
    own scale.

    Predictive by default: each row's fitted value plus ``n_resid`` draws from the
    within-region residuals, back-transformed and pooled, so the anchors describe
    streams and not their conditional means, the same thing the withheld anchors
    describe. ``fitted_only`` gives round one's estimator, the quartiles of the
    fitted values, as a sensitivity. The quantile spec reads its anchors off its two
    fits: the median over rows of each back-transformed conditional quartile.
    """
    out: dict[str, Any] = {"anchors": None, "n_pred": 0, "blup": None}
    if not model:
        return out
    p = predict_model(model, rows, target, group=group)
    out.update({"n_pred": p["n"], "blup": p["blup"]})
    if not p["n"]:
        return out
    kind = model["kind"]
    if model["spec"] == "quantile":
        lo = float(np.median(from_scale(p["q25"], kind)))
        hi = float(np.median(from_scale(p["q75"], kind)))
        out["anchors"] = (lo, hi) if hi > lo else None
        return out
    if fitted_only or model.get("resid") is None:
        vals = from_scale(p["mid"], kind)
    else:
        rng = rng if rng is not None else np.random.default_rng(7)
        e = rng.choice(np.asarray(model["resid"], dtype=float), size=int(n_resid), replace=True)
        vals = from_scale(np.add.outer(p["mid"], e).ravel(), kind)
    got = anchors_of(vals)
    out["anchors"] = got if got and got[1] > got[0] else None
    return out


# --------------------------------------------------------------------------- #
# a basis states its own interval
# --------------------------------------------------------------------------- #
def cluster_bootstrap(estimate: Any, clusters: Any, *, n_boot: int, rng: Any,
                      level: float = 0.90) -> dict:
    """A resampling interval on a basis's own anchors.

    ``clusters`` labels each row of the basis's fitting data, a HUC12 in practice.
    Whole clusters are drawn with replacement and ``estimate(positions)`` returns
    the anchors those rows give, or ``None``: a population re-takes its quartiles, a
    model refits. Same shape as :func:`anchor_interval`, so a basis's interval and
    the truth's can be read side by side. When fewer than half the resamples give
    an answer the basis cannot state an interval and ``q25`` stays ``None``.
    """
    key = pd.Series(np.asarray(clusters, dtype=object))
    key = key.where(key.notna(), pd.Series([f"_row{i}" for i in range(len(key))]))
    groups: dict[Any, list[int]] = {}
    for pos, c in enumerate(key.tolist()):
        groups.setdefault(c, []).append(pos)
    names = list(groups)
    out: dict[str, Any] = {"q25": None, "q75": None, "n_boot_valid": 0, "n_boot": int(n_boot)}
    if len(names) < 2:
        return out
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    draws = []
    for _ in range(int(n_boot)):
        pick = rng.choice(len(names), size=len(names), replace=True)
        pos = np.asarray([i for p in pick for i in groups[names[p]]], dtype=int)
        got = estimate(pos)
        if got:
            draws.append(got)
    out["n_boot_valid"] = len(draws)
    if len(draws) < max(2, (int(n_boot) + 1) // 2):
        return out
    arr = np.asarray(draws, dtype=float)
    out.update({"q25": (float(np.quantile(arr[:, 0], lo_q)), float(np.quantile(arr[:, 0], hi_q))),
                "q75": (float(np.quantile(arr[:, 1], lo_q)), float(np.quantile(arr[:, 1], hi_q)))})
    return out


# --------------------------------------------------------------------------- #
# one cell: the withheld answer, and everything measured against it
# --------------------------------------------------------------------------- #
def cell_truth(ref_values: Any, eval_values: Any, metric_config: dict, *,
               clusters: Any = None, n_boot: int = 300, seed: int = 7) -> Optional[dict]:
    """The withheld answer for one region and metric, computed once and shared by
    every candidate scored in that cell.

    The reference anchors and their bootstrap interval (C1), the reference curve
    and its class calls on the region's stations (C2 and A1), and a cache of
    same-sized reference draws (A1). ``None`` where round one also dropped the
    cell: no anchors, fewer than ten stations to score, or no reference curve.
    """
    ref = pd.to_numeric(pd.Series(ref_values), errors="coerce").dropna()
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    truth = anchors_of(ref)
    if truth is None or len(ev) < 10:
        return None
    pts, _ = curve_for(ref, metric_config)
    if pts is None:
        return None
    cl = pd.Series(clusters).reindex(ref.index) if clusters is not None else None
    return {"ref": ref, "eval": ev, "clusters": cl, "anchors": truth,
            "interval": anchor_interval(ref, clusters=cl, n_boot=n_boot, seed=seed),
            "points": pts, "calls": calls_for(pts, ev), "cfg": metric_config,
            "seed": int(seed), "variability": {}}


def variability_for(truth: dict, n_fit: Optional[int], *, n_draws: int = 100) -> dict:
    """Pre-registration II's yardstick for A1 in one cell.

    The draw size is the smaller of the candidate's fitting size and the reference
    pool's. Below the pool's size, whole catchments without replacement; at it,
    which is every model and every external criterion (``n_fit=None``), catchments
    with replacement. Cached per size, so every candidate in a cell is measured
    against the same draws.
    """
    n_ref = len(truth["ref"])
    n_match = n_ref if n_fit is None else max(5, min(int(n_fit), n_ref))
    replace = n_match >= n_ref
    cache = truth["variability"]
    if (n_match, replace) not in cache:
        cache[(n_match, replace)] = reference_variability(
            truth["ref"], truth["cfg"], truth["eval"], n_draw=n_match,
            clusters=truth["clusters"], n_draws=n_draws, seed=truth["seed"],
            replace=replace)
    return cache[(n_match, replace)]


def candidate_record(truth: dict, *, anchors: Optional[tuple[float, float]] = None,
                     points: Optional[list[dict]] = None, n_fit: Optional[int] = None,
                     interval: Optional[dict] = None, external: bool = False,
                     n_draws: int = 100) -> dict:
    """Everything Pre-registration II measures for one candidate in one cell.

    A candidate is two anchors (built into a curve by the engine) or, for an
    external criterion, a curve of its own. ``n_fit`` is the candidate's fitting
    size; leave it ``None`` for a model or an external criterion, which are held to
    the reference pool's full-size variability.
    """
    t = truth["anchors"]
    iv = truth["interval"]
    rec: dict[str, Any] = {
        "n_reference": int(len(truth["ref"])), "n_eval": int(len(truth["eval"])),
        "true_q25": round(t[0], 4), "true_q75": round(t[1], 4),
        "true_q25_lo": None if not iv.get("q25") else round(iv["q25"][0], 4),
        "true_q25_hi": None if not iv.get("q25") else round(iv["q25"][1], 4),
        "true_q75_lo": None if not iv.get("q75") else round(iv["q75"][0], 4),
        "true_q75_hi": None if not iv.get("q75") else round(iv["q75"][1], 4),
        "pred_q25": None if not anchors else round(float(anchors[0]), 4),
        "pred_q75": None if not anchors else round(float(anchors[1]), 4)}
    pts = points if points is not None else curve_from_anchors(anchors, truth["cfg"])
    rec.update(recovery_error(anchors, t))
    rec.update(contains(anchors, iv) if not external
               else {"q25_in": None, "q75_in": None, "both_in": None})
    rec.update(score_outcomes(truth["points"], pts, truth["eval"]))
    flip = disagreement(truth["calls"], calls_for(pts, truth["eval"])) if pts else None
    var = variability_for(truth, None if external else n_fit, n_draws=n_draws) \
        if flip is not None else {}
    rec.update({"flip": None if flip is None else round(float(flip), 4),
                "exceedance": exceedance(flip, var),
                "var_n_draw": var.get("n_draw"), "var_replace": var.get("replace"),
                "var_median": var.get("median"), "var_p90": var.get("p90")})
    ok = bool(interval and interval.get("q25") is not None)
    rec.update({"interval_ok": None if external else ok,
                "ci_q25_lo": round(interval["q25"][0], 4) if ok else None,
                "ci_q25_hi": round(interval["q25"][1], 4) if ok else None,
                "ci_q75_lo": round(interval["q75"][0], 4) if ok else None,
                "ci_q75_hi": round(interval["q75"][1], 4) if ok else None,
                "ci_n_boot_valid": (interval or {}).get("n_boot_valid"),
                "ci_covers_truth": contains(t, interval)["both_in"] if ok else None})
    return rec


# --------------------------------------------------------------------------- #
# the domain a prediction is made in
# --------------------------------------------------------------------------- #
def coverage_by_covariate(train: pd.DataFrame, target: pd.DataFrame, natural: list[str], *,
                          pressure_quantile: float = 0.25) -> dict:
    """What :func:`extrapolation_share` is made of.

    For each natural covariate, the share of the target's stations inside the 5th
    to 95th percentile of the low-disturbance training stations, then the joint
    share over every covariate (which equals ``extrapolation_share``) and over the
    non-climate covariates alone. Round one reported only the joint share, which
    cannot say whether a target is outside the training data because its streams
    are unlike them or because its climate is.
    """
    nat = [c for c in natural if c in train.columns and c in target.columns]
    out: dict[str, Any] = {"joint": None, "joint_no_climate": None, "by": {}}
    if not nat or not len(target):
        return out
    load = sum(pd.to_numeric(train[c], errors="coerce").rank(pct=True)
               for c in PRESSURE_COLUMNS if c in train.columns)
    clean = train[load <= load.quantile(pressure_quantile)]
    if len(clean) < 10:
        return out
    inside: dict[str, pd.Series] = {}
    for c in nat:
        lo = pd.to_numeric(clean[c], errors="coerce").quantile(0.05)
        hi = pd.to_numeric(clean[c], errors="coerce").quantile(0.95)
        inside[c] = pd.to_numeric(target[c], errors="coerce").between(lo, hi)
        out["by"][c] = round(float(inside[c].mean()), 4)
    joint = pd.Series(True, index=target.index)
    for c in nat:
        joint &= inside[c]
    out["joint"] = round(float(joint.mean()), 4)
    local = [c for c in nat if c not in CLIMATE_COVARIATES]
    if local:
        jl = pd.Series(True, index=target.index)
        for c in local:
            jl &= inside[c]
        out["joint_no_climate"] = round(float(jl.mean()), 4)
    return out


def disturbance_gap(rows: pd.DataFrame, target: dict) -> dict:
    """How far the prediction point sits below the region's own least-disturbed
    station, per pressure: the region's minimum minus the point.

    Zero or less means the region itself reaches the point and a prediction there
    interpolates. The Eastern Corn Belt Plains reads 30.7 on agriculture: its
    cleanest stream is 30.7 percentage points of cropland above the point.
    """
    out = {}
    for c, v in (target or {}).items():
        if c in rows.columns:
            m = pd.to_numeric(rows[c], errors="coerce").min()
            out[f"gap_{c}"] = None if m != m else round(float(m) - float(v), 4)
    return out


# --------------------------------------------------------------------------- #
# selection, audited
# --------------------------------------------------------------------------- #
def select_by_leave_one_out(candidates: Iterable[str], held_out: Iterable[str],
                            error_of: Any, *, min_regions: int = 3) -> dict:
    """The candidate with the smallest median error over regions left out in turn.

    ``error_of(candidate, region)`` fits without that region's reference stations
    and returns the absolute anchor error, or ``None``. Lifted out of the round-one
    script, which kept only the winner: every candidate's median is returned here,
    so the choice can be audited. Ties go to the earlier candidate.
    """
    cands = list(candidates)
    regions = list(held_out)
    table: dict[str, dict] = {}
    for cand in cands:
        errs = [e for e in (error_of(cand, r) for r in regions) if e is not None]
        table[cand] = {"median": round(float(np.median(errs)), 4) if len(errs) >= min_regions
                       else None, "n": len(errs)}
    ok = [c for c in cands if table[c]["median"] is not None]
    best = min(ok, key=lambda c: (table[c]["median"], cands.index(c))) if ok else None
    return {"best": best, "table": table}


# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #
_TWO_THIRDS = 2.0 / 3.0 - 1e-9
VALIDATED = "validated"
VALIDATED_EXTERNAL = "validated as an external criterion"
PROMISING = "promising"
UNSUPPORTED = "unsupported"
UNSUPPORTED_COVERAGE = "unsupported by data coverage"
NOT_EVALUATED = "not evaluated"
NOT_QUANTIFIED = "not quantified"


def _share(mask: pd.Series) -> float:
    return float(mask.mean()) if len(mask) else 0.0


def _tier(cells: pd.DataFrame, external: bool) -> tuple[str, dict]:
    both = cells["both_in"].map(lambda v: bool(v) if v == v and v is not None else False)
    exc = pd.to_numeric(cells["exceedance"], errors="coerce")
    net = pd.to_numeric(cells["net_optimism"], errors="coerce")
    facts = {"c1_share": round(_share(both), 4),
             "a1_share": round(_share(exc >= 0.10), 4),
             "net_opt": round(float(net.median()), 4) if net.notna().any() else None}
    c1 = facts["c1_share"] >= _TWO_THIRDS
    a1 = facts["a1_share"] >= _TWO_THIRDS
    c2 = facts["net_opt"] is not None and facts["net_opt"] <= 0.05
    if external:
        return (VALIDATED_EXTERNAL if (a1 and c2) else UNSUPPORTED), facts
    if c1 and a1 and c2:
        return VALIDATED, facts
    if a1 and c2:
        return PROMISING, facts
    return UNSUPPORTED, facts


def score_verdicts(records: pd.DataFrame, *, rule: str = "prereg-2",
                   min_cells: int = 4) -> pd.DataFrame:
    """Per metric, basis and regime verdicts from per-cell records.

    ``rule="prereg-2"`` applies Pre-registration II. ``rule="round-one"``
    reproduces the labels of round one's ``verdicts.csv`` from
    ``three_options.csv``, including its "promising" rule (containment in at least
    half of cells with C2 met), which was never registered and is reproduced only so
    the two rounds can be compared.

    Records carry ``metric``, ``basis`` (or round one's ``option``), and for
    ``prereg-2`` also ``regime``, ``kind`` (population, model or external),
    ``flip``, ``exceedance``, ``interval_ok`` and ``extrapolation_ok``.
    """
    df = records.copy()
    if "basis" not in df.columns and "option" in df.columns:
        df["basis"] = df["option"]
    if "regime" not in df.columns:
        df["regime"] = "I"
    rows = []
    for (metric, basis, regime), g in df.groupby(["metric", "basis", "regime"], sort=True):
        if rule == "round-one":
            rows.append({"metric": metric, "basis": basis, "regime": regime,
                         **_round_one_verdict(g, str(basis), min_cells)})
            continue
        kind = str(g["kind"].iloc[0]) if "kind" in g.columns else "population"
        external = kind == "external"
        cells = g[g["flip"].notna()]
        n = len(cells)
        err = pd.to_numeric(cells.get("err_abs_mean"), errors="coerce")
        base = {"metric": metric, "basis": basis, "regime": regime, "kind": kind,
                "n_cells": int(n),
                "contain": int(cells["both_in"].map(lambda v: v is True or v == 1).sum())
                if not external else None,
                "err_iqr": round(float(err.median()), 4) if err.notna().any() else None,
                "exceed_median": round(float(pd.to_numeric(cells["exceedance"],
                                       errors="coerce").median()), 4) if n else None}
        if n < min_cells:
            rows.append({**base, "verdict": NOT_EVALUATED, "info": f"{n} evaluable cells"})
            continue
        if not external and not cells["interval_ok"].map(lambda v: v is True).all():
            rows.append({**base, "verdict": NOT_QUANTIFIED,
                         "info": f"{int((~cells['interval_ok'].map(lambda v: v is True)).sum())} "
                                 f"cells without an interval"})
            continue
        info = ""
        if kind == "model":
            share = pd.to_numeric(cells["extrapolation_ok"], errors="coerce")
            covered = share >= 0.25
            base["c3_share"] = round(_share(covered), 4)
            if base["c3_share"] < _TWO_THIRDS:
                sub = cells[covered.to_numpy()]
                what = (_tier(sub, False)[0] if len(sub) >= min_cells
                        else f"{len(sub)} covered cells")
                rows.append({**base, **_tier(cells, False)[1],
                             "verdict": UNSUPPORTED_COVERAGE,
                             "info": f"on its covered cells alone: {what}"})
                continue
        verdict, facts = _tier(cells, external)
        cover = cells.get("ci_covers_truth")
        if cover is not None and not external:
            known = cover.dropna()
            info = (f"own interval covers the truth in {int(known.map(bool).sum())} of "
                    f"{len(known)} cells") if len(known) else ""
        rows.append({**base, **facts, "verdict": verdict, "info": info})
    return pd.DataFrame(rows)


def _round_one_verdict(g: pd.DataFrame, option: str, min_cells: int) -> dict:
    """Round one's labels, rebuilt from its per-cell records."""
    cells = g[g["both_in"].notna()]
    n = len(cells)
    contain = int(cells["both_in"].map(lambda v: v is True or v == 1 or v == "True").sum())
    net = pd.to_numeric(cells["net_optimism"], errors="coerce")
    err = pd.to_numeric(cells["err_abs_mean"], errors="coerce")
    out = {"n_cells": int(n), "contain": contain,
           "err_iqr": round(float(err.median()), 4) if err.notna().any() else None,
           "net_opt": round(float(net.median()), 4) if net.notna().any() else None}
    if n < min_cells:
        return {**out, "verdict": f"not evaluated ({n} evaluable region{'' if n == 1 else 's'})"}
    if option.startswith("1B"):
        share = pd.to_numeric(cells["extrapolation_ok"], errors="coerce")
        if _share(share >= 0.25) < _TWO_THIRDS:
            return {**out, "verdict": UNSUPPORTED_COVERAGE}
    c2 = out["net_opt"] is not None and out["net_opt"] <= 0.05
    frac = contain / n
    if frac >= _TWO_THIRDS and c2:
        return {**out, "verdict": "supported"}
    if frac >= 0.5 and c2:
        return {**out, "verdict": PROMISING}
    return {**out, "verdict": UNSUPPORTED}
