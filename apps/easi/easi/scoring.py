"""EASI scoring engine — a faithful Python port of the STAF screening rollup.

Pipeline (one metric per stream function):
    metric value -> rating (Good/Fair/Poor) -> index (0-1)
    -> function score (0-15)  = round(index * 15)
    -> outcome sub-indices    = sum(score * weight) / sum(15 * weight)
       per Physical / Chemical / Biological, weight D=1.0, i=0.1, -=0
    -> Ecosystem Condition Index = mean(Physical, Chemical, Biological)

Originally ported from the retired STAF site screening widget; this module and the
scoring catalog are now the reference implementation.
Core functions are parameterized (no I/O) for testability; ``score_assessment``
is the convenience entry point that pulls metric/mapping data from ``config``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import config

#: ``1`` reports the rollup's completeness fields (``functionsRated`` and the ECI interval)
#: whatever the active catalog says; ``0`` keeps the legacy report shape even when the
#: catalog's ``rollupReporting`` flag asks for them. Unset: the catalog decides.
ENV_ROLLUP_REPORTING = "EASI_ROLLUP_REPORTING"


# --------------------------------------------------------------------------- #
# Metric-level
# --------------------------------------------------------------------------- #
def rating_to_index(rating: str, midpoints: dict[str, float] | None = None) -> float:
    """Good/Fair/Poor -> index (0-1). Uses per-metric bin midpoints when given."""
    table = midpoints or config.RATING_INDEX
    try:
        return float(table[rating])
    except KeyError as exc:  # pragma: no cover - guard
        raise ValueError(f"unknown rating {rating!r}") from exc


def function_score(metric_index: float) -> int:
    """index (0-1) -> function score (0-15), rounded and clamped (STAF)."""
    value = round(metric_index * config.FUNCTION_SCORE_MAX)
    return max(0, min(config.FUNCTION_SCORE_MAX, int(value)))


# --------------------------------------------------------------------------- #
# Rollup
# --------------------------------------------------------------------------- #
@dataclass
class OutcomeResult:
    weighted: float = 0.0
    max: float = 0.0
    direct: int = 0
    indirect: int = 0

    @property
    def sub_index(self) -> float | None:
        return self.weighted / self.max if self.max > 0 else None


@dataclass
class RollupResult:
    function_scores: dict[str, int]
    outcomes: dict[str, OutcomeResult]
    ecosystem_condition_index: float | None
    sub_indices: dict[str, float | None] = field(default_factory=dict)
    #: the functions of the mapping that carry a score (of the twenty)
    functions_rated: int = 0
    #: the ECI with every unrated function taken as Poor (lower) and as Good (upper);
    #: None when the mapping is empty. Equal to the point ECI when every function is rated.
    eci_interval: tuple[float, float] | None = None


def _accumulate(function_scores: dict[str, int], mapping: dict, weights: dict):
    """The outcome accumulators, sub-indices and ECI of a score set (the STAF rollup)."""
    outcomes = {key: OutcomeResult() for key in config.OUTCOMES}

    for fid, score in function_scores.items():
        codes = mapping.get(fid)
        if codes is None:
            continue
        for key in config.OUTCOMES:
            code = codes.get(key, "-")
            weight = weights.get(code, 0.0)
            if code == "D":
                outcomes[key].direct += 1
            elif code == "i":
                outcomes[key].indirect += 1
            if weight:
                outcomes[key].weighted += score * weight
                outcomes[key].max += config.FUNCTION_SCORE_MAX * weight

    sub_indices = {key: outcomes[key].sub_index for key in config.OUTCOMES}
    available = [value for value in sub_indices.values() if value is not None]
    eci = sum(available) / len(available) if available else None
    return outcomes, sub_indices, eci


def rollup(
    function_scores: dict[str, int],
    mapping: dict[str, dict[str, str]] | None = None,
    weights: dict[str, float] | None = None,
) -> RollupResult:
    """Roll function scores up to outcome sub-indices and the Ecosystem index.

    ``function_scores``: functionId -> score (0-15).
    ``mapping``: functionId -> {physical, chemical, biological} contribution code.

    The point ECI is the mean of the available sub-indices, unrated functions omitted.
    Beside it the result carries how many functions are rated and the interval the ECI
    would span if every unrated function were Poor (index 0.195, the lower bound) or
    Good (index 0.85, the upper bound); neither changes the point ECI or any sub-index.
    """
    mapping = mapping if mapping is not None else config.cwa_mapping()
    weights = weights if weights is not None else config.WEIGHTS

    outcomes, sub_indices, eci = _accumulate(function_scores, mapping, weights)
    rated = [fid for fid in function_scores if fid in mapping]
    interval = None
    if mapping:
        bounds = []
        for anchor in (config.RATING_INDEX["Poor"], config.RATING_INDEX["Good"]):
            filled = dict(function_scores)
            fill = function_score(anchor)
            for fid in mapping:
                filled.setdefault(fid, fill)
            bounds.append(_accumulate(filled, mapping, weights)[2])
        if bounds[0] is not None and bounds[1] is not None:
            interval = (bounds[0], bounds[1])
    return RollupResult(
        function_scores=dict(function_scores),
        outcomes=outcomes,
        ecosystem_condition_index=eci,
        sub_indices=sub_indices,
        functions_rated=len(rated),
        eci_interval=interval,
    )


def rollup_reporting_enabled() -> bool:
    """Whether a report carries the rollup's completeness fields: the process flag
    (``EASI_ROLLUP_REPORTING``) when set, else the active catalog's ``rollupReporting``
    (a package asks for them); off by default, so the legacy report shape is unchanged."""
    flag = os.environ.get(ENV_ROLLUP_REPORTING)
    if flag is not None and flag.strip():
        return flag.strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(config.screening_methods().get("rollupReporting"))
    except (OSError, ValueError, KeyError, AttributeError):
        return False


def reporting_fields(result: RollupResult) -> dict:
    """``functionsRated`` and ``ecosystemConditionIndexInterval`` (display rounding, like
    the point ECI) for a report that asks for them."""
    interval = result.eci_interval
    return {"functionsRated": result.functions_rated,
            "ecosystemConditionIndexInterval": (
                None if interval is None else [round2(interval[0]), round2(interval[1])])}


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def round2(value: float | None) -> float | None:
    """STAF display rounding (2 decimals)."""
    return None if value is None else round(value * 100) / 100


def index_band_color(value: float | None) -> str:
    if value is None:
        return "#d7dce5"
    for threshold, color in config.INDEX_BANDS:
        if value <= threshold:
            return color
    return config.INDEX_BANDS[-1][1]


def index_band_label(value: float | None) -> str:
    """Index (0-1) -> STAF condition category (Non-Functioning/Functioning-at-Risk/Functioning), matching the color bands."""
    if value is None:
        return "Not assessed"
    for (threshold, _color), label in zip(config.INDEX_BANDS, config.INDEX_BAND_LABELS):
        if value <= threshold:
            return label
    return config.INDEX_BAND_LABELS[-1]


def function_score_band_color(value: float | None) -> str:
    if value is None:      # unrated: neutral, never the Non-Functioning red
        return "#d7dce5"
    for threshold, color in config.FUNCTION_SCORE_BANDS:
        if value <= threshold:
            return color
    return config.FUNCTION_SCORE_BANDS[-1][1]


def function_score_band_label(value: float) -> str:
    """Function score (0-15) -> short condition code F / AR / NF (STAF bands).

    Mirrors :func:`index_band_label` but returns the short badge form aligned with
    ``config.FUNCTION_SCORE_BANDS`` (<=5 NF, <=10 AR, else F)."""
    for (threshold, _color), label in zip(config.FUNCTION_SCORE_BANDS,
                                          config.FUNCTION_SCORE_BAND_SHORT):
        if value <= threshold:
            return label
    return config.FUNCTION_SCORE_BAND_SHORT[-1]


# --------------------------------------------------------------------------- #
# Convenience entry point (uses bundled EASI metric + mapping data)
# --------------------------------------------------------------------------- #
def score_assessment(ratings: dict[str, str]) -> dict:
    """Score a full EASI assessment from per-metric ratings.

    ``ratings``: metricId -> 'Good'|'Fair'|'Poor'. Metrics that are missing or
    rated ``None`` are skipped (degrade gracefully), so the rollup reflects only
    the functions actually scored.
    Returns a JSON-serializable result dict for the report.
    """
    metrics = config.metrics_by_id()
    function_scores: dict[str, int] = {}
    metric_rows: list[dict] = []

    for metric_id, rating in ratings.items():
        meta = metrics.get(metric_id)
        if meta is None or rating is None:
            continue
        midpoints = meta.get("indexMidpoints")
        index = rating_to_index(rating, midpoints)
        fscore = function_score(index)
        function_scores[meta["functionId"]] = fscore
        metric_rows.append({
            "metricId": metric_id,
            "name": meta.get("name"),
            "discipline": meta.get("discipline"),
            "functionId": meta["functionId"],
            "functionName": meta.get("functionName"),
            "rating": rating,
            "index": index,
            "functionScore": fscore,
            "criteria": meta.get("criteria", {}).get(rating, ""),
        })

    result = rollup(function_scores)
    out = {
        "metrics": metric_rows,
        "functionScores": result.function_scores,
        "subIndices": {k: round2(v) for k, v in result.sub_indices.items()},
        "subIndicesRaw": result.sub_indices,
        "outcomes": {
            k: {"weighted": round2(o.weighted), "max": round2(o.max),
                "direct": o.direct, "indirect": o.indirect,
                "subIndex": round2(o.sub_index)}
            for k, o in result.outcomes.items()
        },
        "ecosystemConditionIndex": round2(result.ecosystem_condition_index),
        "ecosystemConditionIndexRaw": result.ecosystem_condition_index,
    }
    if rollup_reporting_enabled():
        out.update(reporting_fields(result))
    return out
