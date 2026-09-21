"""One protocol for judging any candidate scoring basis (methodology 0.13).

Methodology 0.12 fits every curve on least-disturbed stations of the region, its
Level II parent, or its Level I parent, and withholds the metric where no level
supports it (REF-06). Fifteen of the 75 Level III ecoregions with a usable panel
hold no least-disturbed station at all, and 37 are under 15 percent, so the
question is what else may legitimately anchor a curve there.

Candidate answers differ in where the fitting values come from, not in how the
curve is drawn: ``curves.build_reference_curve`` takes whatever series it is
handed. So a basis here is a population, and comparing bases is comparing the
class calls their curves produce on the same sites.

Two outputs decide admission, and a basis that cannot produce both is not
admissible:

* **Agreement.** The share of a region's own sites whose Non-Functioning /
  At-Risk / Functioning call changes when the curve is anchored on the candidate
  instead of on reference stations. This is the project's existing test of
  whether a choice changes the answer: ``scale_analysis`` rejects a class split
  above a worst-case 0.045, and that is the scale these numbers live on.
* **Uncertainty.** A resampling interval on the index the curve returns, by
  watershed cluster, so a basis states how well it knows its own anchors rather
  than asserting them.

Judging a basis on the sites it was fitted to flatters it, so the driver holds
out whatever the basis consumed: a national model excludes the region, and a
within-region basis is scored on the region's full in-frame population.

``metric_config`` throughout is the entry from ``pressure_evidence.national_inputs``,
which carries the response direction, curve form and domain. The scale registry's
entry does not, and handing that to the engine instead silently builds the curve
the wrong way up.

Pure: frames in, records out. No file writes, no network.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from . import curves
from .curve_stability import _build_points

#: The basis every other one is measured against.
REFERENCE_BASIS = "reference"

#: Class calls, on DEEP's published breaks.
BANDS = ("NF", "AR", "F")


def band_of(index: Optional[float]) -> Optional[str]:
    if index is None or index != index:
        return None
    return "NF" if index <= 0.39 else "AR" if index <= 0.69 else "F"


def curve_for(values: Any, metric_config: dict) -> tuple[Optional[list[dict]], str]:
    """(points, curve_status) for one fitting population, through the shipping engine."""
    return _build_points(pd.Series(values), metric_config)


def calls_for(points: Optional[list[dict]], eval_values: Any) -> list[Optional[str]]:
    """The class call at each evaluation value, or [] when there is no curve."""
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    if points is None or not len(ev):
        return []
    return [band_of(curves.interp_curve(points, float(x))) for x in ev]


def disagreement(a: list[Optional[str]], b: list[Optional[str]]) -> Optional[float]:
    """Share of evaluation sites whose class call differs between two curves."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return None
    return float(np.mean([x != y for x, y in pairs]))


def index_interval(fit_values: Any, metric_config: dict, eval_values: Any, *,
                   clusters: Optional[Any] = None, n_boot: int = 200,
                   seed: int = 7) -> dict:
    """A resampling interval on the index this basis returns.

    Resampled by watershed cluster where one is given, because stations of a
    catchment are not independent draws. Returns the median 5th-to-95th width
    across the evaluation sites, which is the number an admission rule can read,
    with the per-site bounds kept for a figure.
    """
    vals = pd.to_numeric(pd.Series(fit_values), errors="coerce").dropna()
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    out: dict[str, Any] = {"n_boot_valid": 0, "median_width": None,
                           "low": [], "high": [], "n_eval": int(len(ev))}
    if len(vals) < 5 or not len(ev):
        return out
    key = (pd.Series(clusters).astype(object).reindex(vals.index)
           if clusters is not None else pd.Series(vals.index.astype(str), index=vals.index))
    key = key.where(key.notna(), pd.Series(vals.index.astype(str), index=vals.index))
    groups = {c: g.to_numpy() for c, g in vals.groupby(key)}
    names = list(groups)
    rng = np.random.default_rng(int(seed))
    draws: list[list[float]] = []
    for _ in range(int(n_boot)):
        pick = rng.choice(len(names), size=len(names), replace=True)
        sample = np.concatenate([groups[names[i]] for i in pick])
        pts, _status = curve_for(sample, metric_config)
        if pts is None:
            continue
        draws.append([float(curves.interp_curve(pts, float(x))) for x in ev])
    if not draws:
        return out
    arr = np.asarray(draws, dtype=float)
    low = np.nanquantile(arr, 0.05, axis=0)
    high = np.nanquantile(arr, 0.95, axis=0)
    out.update({"n_boot_valid": len(draws),
                "median_width": round(float(np.nanmedian(high - low)), 4),
                "low": [round(float(v), 4) for v in low],
                "high": [round(float(v), 4) for v in high]})
    return out


def interval_from_resampler(resample: Any, eval_values: Any, *, n_boot: int = 200,
                            seed: int = 7) -> dict:
    """The same interval, for a basis that builds its own curve.

    ``resample(rng)`` returns curve points from one resampling of whatever the
    basis consumed, or None when that draw yields no curve. A model refits inside
    it, so the interval covers the model's own uncertainty and not merely the
    spread of its predictions.
    """
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    out: dict[str, Any] = {"n_boot_valid": 0, "median_width": None,
                           "low": [], "high": [], "n_eval": int(len(ev))}
    if not len(ev):
        return out
    rng = np.random.default_rng(int(seed))
    draws = []
    for _ in range(int(n_boot)):
        pts = resample(rng)
        if not pts:
            continue
        draws.append([float(curves.interp_curve(pts, float(x))) for x in ev])
    if not draws:
        return out
    arr = np.asarray(draws, dtype=float)
    low, high = np.nanquantile(arr, 0.05, axis=0), np.nanquantile(arr, 0.95, axis=0)
    out.update({"n_boot_valid": len(draws),
                "median_width": round(float(np.nanmedian(high - low)), 4),
                "low": [round(float(v), 4) for v in low],
                "high": [round(float(v), 4) for v in high]})
    return out


def evaluate_basis(name: str, fit_values: Any, metric_config: dict, eval_values: Any, *,
                   reference_calls: Optional[list[Optional[str]]] = None,
                   clusters: Optional[Any] = None, n_boot: int = 200,
                   seed: int = 7, with_interval: bool = True,
                   points: Optional[list[dict]] = None,
                   resample: Optional[Any] = None,
                   reference_index: Optional[Any] = None) -> dict:
    """One basis, measured: its curve, its agreement with reference, its uncertainty.

    ``reference_calls`` is the class calls of the reference-anchored curve on the
    same evaluation values. Passing them for the reference basis itself is
    deliberate: it agrees with itself, so the yardstick reads as accepted rather
    than as unevaluable.

    Most bases are a population and the engine draws the curve. A selected
    percentile rule or a fitted stressor-response model is not a population, so
    those supply ``points`` directly, and ``resample(rng) -> points | None`` for
    the interval. Without ``resample`` such a basis cannot state its uncertainty
    and ``verdict`` refuses it, which is the same bar every other basis meets.

    ``reference_index`` is the reference pool's station index. The share of this
    basis's own stations that are reference stations is recorded as
    ``overlap_with_reference``, and it separates a test from a tautology: a basis
    drawn largely from the yardstick agrees with the yardstick for a reason that
    says nothing about a region without one. In the regions that can be measured
    at all, the region's own lowest-pressure fraction overlaps the reference set
    by a median of 0.80 and by 1.00 in the cleanest of them.
    """
    if points is None:
        points, status = curve_for(fit_values, metric_config)
    else:
        status = "supplied"
    calls = calls_for(points, eval_values)
    n_fit = int(pd.to_numeric(pd.Series(fit_values), errors="coerce").dropna().size)         if fit_values is not None else 0
    rec: dict[str, Any] = {
        "basis": name, "n_fit": n_fit, "curve_status": status,
        "has_curve": points is not None,
        "n_eval_called": sum(1 for c in calls if c is not None),
        "flip_vs_reference": (None if reference_calls is None
                              else disagreement(reference_calls, calls)),
        "band_share": {b: (round(float(np.mean([c == b for c in calls])), 4)
                           if calls else None) for b in BANDS},
    }
    if reference_index is not None and fit_values is not None:
        own = pd.Index(pd.Series(fit_values).dropna().index)
        rec["overlap_with_reference"] = (
            round(float(own.isin(pd.Index(reference_index)).mean()), 4) if len(own) else None)
    else:
        rec["overlap_with_reference"] = None
    if with_interval and points is not None and resample is not None:
        iv = interval_from_resampler(resample, eval_values, n_boot=n_boot, seed=seed)
        rec["median_index_width"] = iv["median_width"]
        rec["n_boot_valid"] = iv["n_boot_valid"]
    elif with_interval and points is not None and fit_values is not None:
        iv = index_interval(fit_values, metric_config, eval_values, clusters=clusters,
                            n_boot=n_boot, seed=seed)
        rec["median_index_width"] = iv["median_width"]
        rec["n_boot_valid"] = iv["n_boot_valid"]
    else:
        rec["median_index_width"] = None
        rec["n_boot_valid"] = 0
    # A basis states its uncertainty or it is not admissible, whatever its
    # agreement looks like.
    rec["quantified"] = rec["median_index_width"] is not None
    rec["calls"] = calls
    return rec


def compare_bases(bases: "dict[str, Any]", metric_config: dict, eval_values: Any, *,
                  clusters: "Optional[dict[str, Any]]" = None, n_boot: int = 200,
                  seed: int = 7) -> list[dict]:
    """Every candidate basis for one metric in one region, reference first.

    ``bases`` maps a basis name to the values its curve is fitted on. The one
    named :data:`REFERENCE_BASIS` is the yardstick; the rest are measured against
    it. A region with no reference pool has no yardstick, and the records come
    back with ``flip_vs_reference`` None, which is the honest answer and the
    reason the admission rule is calibrated where a yardstick exists.
    """
    clusters = clusters or {}
    ref_calls = None
    ref_index = None
    if REFERENCE_BASIS in bases:
        ref_points, _ = curve_for(bases[REFERENCE_BASIS], metric_config)
        ref_calls = calls_for(ref_points, eval_values)
        ref_index = pd.Series(bases[REFERENCE_BASIS]).dropna().index
    out = []
    order = ([REFERENCE_BASIS] if REFERENCE_BASIS in bases else []) + \
            [k for k in bases if k != REFERENCE_BASIS]
    for name in order:
        out.append(evaluate_basis(
            name, bases[name], metric_config, eval_values,
            reference_calls=ref_calls, reference_index=ref_index,
            clusters=clusters.get(name), n_boot=n_boot, seed=seed))
    return out


def noise_floor(reference_values: Any, metric_config: dict, eval_values: Any, *,
                clusters: Optional[Any] = None, n_splits: int = 100,
                seed: int = 7) -> dict:
    """How much the yardstick disagrees with ITSELF, from sampling alone.

    Two curves built from random halves of the same reference pool differ only by
    which stations fell in which half. The flip rate between them is the floor
    below which no candidate can be distinguished from the yardstick, and it is
    the honest source of an accept threshold: a threshold tighter than this floor
    rejects the reference pool itself.

    Split by watershed cluster where one is given, so a catchment's stations do
    not straddle the halves and make the floor look smaller than it is.
    """
    vals = pd.to_numeric(pd.Series(reference_values), errors="coerce").dropna()
    ev = pd.to_numeric(pd.Series(eval_values), errors="coerce").dropna()
    out: dict[str, Any] = {"n_splits_valid": 0, "median": None, "p90": None,
                           "n_reference": int(len(vals))}
    if len(vals) < 10 or not len(ev):
        return out
    key = (pd.Series(clusters).astype(object).reindex(vals.index)
           if clusters is not None else pd.Series(vals.index.astype(str), index=vals.index))
    key = key.where(key.notna(), pd.Series(vals.index.astype(str), index=vals.index))
    groups = list(pd.Series(vals.index).groupby(key.to_numpy()).apply(list))
    if len(groups) < 4:
        return out
    rng = np.random.default_rng(int(seed))
    flips = []
    for _ in range(int(n_splits)):
        order = rng.permutation(len(groups))
        half = len(groups) // 2
        a_ix = [i for g in order[:half] for i in groups[g]]
        b_ix = [i for g in order[half:] for i in groups[g]]
        pa, _ = curve_for(vals.loc[a_ix], metric_config)
        pb, _ = curve_for(vals.loc[b_ix], metric_config)
        if pa is None or pb is None:
            continue
        got = disagreement(calls_for(pa, ev), calls_for(pb, ev))
        if got is not None:
            flips.append(got)
    if not flips:
        return out
    out.update({"n_splits_valid": len(flips),
                "median": round(float(np.median(flips)), 4),
                "p90": round(float(np.quantile(flips, 0.90)), 4)})
    return out


def discrimination_auc(points: Optional[list[dict]], good_values: Any,
                       impaired_values: Any) -> Optional[float]:
    """Does this curve separate independently designated good from impaired sites?

    The band-flip rate asks whether a basis agrees with OUR curve, which assumes
    our curve is right. This asks something our curve cannot answer for itself:
    whether the index tracks condition as an outside judgement recorded it. A
    basis may disagree with the reference curve and discriminate better, and that
    is evidence about the reference curve rather than about the basis.

    Reuses the project's Mann-Whitney area (``discrimination.auc``) so the number
    is the same one CURVE-12 reports.
    """
    from . import discrimination as dz
    if points is None:
        return None
    good = dz.score_values(points, good_values).dropna()
    bad = dz.score_values(points, impaired_values).dropna()
    if len(good) < dz.MIN_GROUP or len(bad) < dz.MIN_GROUP:
        return None
    return dz.auc(good, bad)


def verdict(record: dict, *, accept: float, exploratory: float) -> str:
    """Admission for one basis against thresholds fixed before the results.

    ``not_quantified`` outranks agreement: a basis that cannot state its own
    uncertainty is refused however well it agrees.
    """
    if not record.get("has_curve"):
        return "no_curve"
    if not record.get("quantified"):
        return "not_quantified"
    flip = record.get("flip_vs_reference")
    if flip is None:
        return "not_evaluable"
    if flip <= accept:
        return "accept"
    return "exploratory" if flip <= exploratory else "reject"


def summarize(records: Iterable[dict]) -> pd.DataFrame:
    """The comparison table, one row per basis per metric per region."""
    keep = ("region", "metric", "basis", "n_fit", "curve_status", "n_eval_called",
            "flip_vs_reference", "overlap_with_reference", "median_index_width",
            "n_boot_valid", "quantified", "verdict")
    rows = [{k: r.get(k) for k in keep} for r in records]
    return pd.DataFrame(rows, columns=list(keep))
