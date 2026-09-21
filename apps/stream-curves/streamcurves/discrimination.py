"""Does a reference curve separate least-disturbed from pressured streams?

Rule CURVE-12 (methodology 0.12), advisory. A curve is fitted on reference
stations only, so nothing in the fit shows that the metric responds to
disturbance at all: every within-pool diagnostic can fail an unstable curve and
none can fail a useless one. This check is the missing half. It scores the
curve's index at two groups of stations and reports how well the index tells
them apart:

* the pool's own reference stations, each scored on a curve rebuilt WITHOUT it
  (leave-one-out), because a curve puts its own interquartile range at 0.70 or
  better and self-scoring would flatter every metric alike, and
* the in-frame stations of the same geography that fail even the relaxed
  pressure screen.

The area under the ROC curve is the chance a random reference station outscores
a random pressured one: 0.5 is no separation, 1.0 is complete. Where EPA's own
2013-14 reference designation is available on enough stations, the same
statistic is reported for its least-disturbed (R) against most-disturbed (Im)
stations. The stations that are not reference are used here to CHECK a curve,
never to fit one.

Pure: no network, no file writes.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from . import curves
from .curve_stability import _build_points, _clean

VERDICT_DISCRIMINATES = "discriminates"
VERDICT_WEAK = "weak"
VERDICT_NONE = "none"
VERDICT_INVERTED = "inverted"
VERDICT_NOT_EVALUABLE = "not_evaluable"

AUC_DISCRIMINATES = 0.65
AUC_WEAK = 0.55
AUC_INVERTED = 0.45
MIN_GROUP = 5


def auc(positive: Any, negative: Any) -> Optional[float]:
    """The Mann-Whitney area: P(positive > negative) + half the ties."""
    pos = _clean(positive).to_numpy()
    neg = _clean(negative).to_numpy()
    if len(pos) == 0 or len(neg) == 0:
        return None
    both = np.concatenate([pos, neg])
    ranks = pd.Series(both).rank(method="average").to_numpy()
    rank_sum = ranks[: len(pos)].sum()
    u = rank_sum - len(pos) * (len(pos) + 1) / 2.0
    return float(u / (len(pos) * len(neg)))


def verdict_of(value: Optional[float]) -> str:
    if value is None:
        return VERDICT_NOT_EVALUABLE
    if value >= AUC_DISCRIMINATES:
        return VERDICT_DISCRIMINATES
    if value >= AUC_WEAK:
        return VERDICT_WEAK
    if value < AUC_INVERTED:
        return VERDICT_INVERTED
    return VERDICT_NONE


def score_values(points: Any, values: Any) -> pd.Series:
    """The curve's index at each value (NaN where a value is missing)."""
    s = pd.to_numeric(pd.Series(values), errors="coerce")
    out = [np.nan if pd.isna(v) else curves.interp_curve(points, float(v)) for v in s]
    return pd.Series(out, index=s.index, dtype="float64")


def loo_indices(values: Any, entry: dict) -> pd.Series:
    """Each reference station's index on the curve rebuilt without it. A fold
    whose curve cannot be built is left missing."""
    v = _clean(values)
    out = pd.Series(np.nan, index=v.index, dtype="float64")
    if len(v) <= curves.CURVE_ENGINE_HARD_FLOOR_N:
        return out
    for i in range(len(v)):
        pts, _status = _build_points(v.drop(v.index[i]), entry)
        if pts is None:
            continue
        got = curves.interp_curve(pts, float(v.iloc[i]))
        if got is not None:
            out.iloc[i] = got
    return out


def curve_discrimination(metric: str, entry: dict, points: Any, ref_values: Any,
                         pressure_values: Any, *, rt_values: Optional[dict] = None) -> dict:
    """The CURVE-12 record for one curve.

    ``ref_values``: the pool's reference values. ``pressure_values``: values at
    in-frame stations of the same geography that fail the relaxed screen.
    ``rt_values``: optional ``{"R": values, "Im": values}`` at stations carrying
    EPA's 2013-14 designation.
    """
    ref_idx = loo_indices(ref_values, entry).dropna()
    pres_idx = score_values(points, pressure_values).dropna()
    record: dict[str, Any] = {
        "metric": metric, "nRef": int(len(ref_idx)), "nPressure": int(len(pres_idx)),
        "aucRefVsPressure": None, "medianRefIndex": None, "medianPressureIndex": None,
        "aucRvsIm": None, "nR": 0, "nIm": 0, "verdict": VERDICT_NOT_EVALUABLE,
    }
    if len(ref_idx) >= MIN_GROUP and len(pres_idx) >= MIN_GROUP:
        a = auc(ref_idx, pres_idx)
        record.update({"aucRefVsPressure": None if a is None else round(a, 3),
                       "medianRefIndex": round(float(ref_idx.median()), 3),
                       "medianPressureIndex": round(float(pres_idx.median()), 3),
                       "verdict": verdict_of(a)})
    if rt_values:
        r_idx = score_values(points, rt_values.get("R")).dropna()
        im_idx = score_values(points, rt_values.get("Im")).dropna()
        record["nR"], record["nIm"] = int(len(r_idx)), int(len(im_idx))
        if len(r_idx) >= MIN_GROUP and len(im_idx) >= MIN_GROUP:
            a = auc(r_idx, im_idx)
            record["aucRvsIm"] = None if a is None else round(a, 3)
    return record


def sentence(record: dict) -> str:
    """One plain sentence for a review packet or a tooltip."""
    a = record.get("aucRefVsPressure")
    if a is None:
        return ("Too few pressured stations carry this metric to check whether the curve "
                "separates them from reference stations.")
    words = {VERDICT_DISCRIMINATES: "separates", VERDICT_WEAK: "weakly separates",
             VERDICT_NONE: "does not separate", VERDICT_INVERTED: "ranks backwards"}
    return (f"The curve {words.get(record.get('verdict'), 'does not separate')} reference from "
            f"pressured stations (area under the curve {a:.2f}, {record.get('nRef')} reference "
            f"and {record.get('nPressure')} pressured stations).")
