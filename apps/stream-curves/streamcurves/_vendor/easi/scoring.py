"""EASI scoring engine — a faithful Python port of the STAF screening rollup.

Pipeline (one metric per stream function):
    metric value -> rating (Good/Fair/Poor) -> index (0-1)
    -> function score (0-15)  = round(index * 15)
    -> outcome sub-indices    = sum(score * weight) / sum(15 * weight)
       per Physical / Chemical / Biological, weight D=1.0, i=0.1, -=0
    -> Ecosystem Condition Index = mean(Physical, Chemical, Biological)

Ported from docs/assets/js/screening-assessment.js (~lines 3225-3507).
Core functions are parameterized (no I/O) for testability; ``score_assessment``
is the convenience entry point that pulls metric/mapping data from ``config``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config


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
    def sub_index(self) -> float:
        return self.weighted / self.max if self.max > 0 else 0.0


@dataclass
class RollupResult:
    function_scores: dict[str, int]
    outcomes: dict[str, OutcomeResult]
    ecosystem_condition_index: float
    sub_indices: dict[str, float] = field(default_factory=dict)


def rollup(
    function_scores: dict[str, int],
    mapping: dict[str, dict[str, str]] | None = None,
    weights: dict[str, float] | None = None,
) -> RollupResult:
    """Roll function scores up to outcome sub-indices and the Ecosystem index.

    ``function_scores``: functionId -> score (0-15).
    ``mapping``: functionId -> {physical, chemical, biological} contribution code.
    """
    mapping = mapping if mapping is not None else config.cwa_mapping()
    weights = weights if weights is not None else config.WEIGHTS

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
    eci = sum(sub_indices.values()) / len(config.OUTCOMES)
    return RollupResult(
        function_scores=dict(function_scores),
        outcomes=outcomes,
        ecosystem_condition_index=eci,
        sub_indices=sub_indices,
    )


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def round2(value: float) -> float:
    """STAF display rounding (2 decimals)."""
    return round(value * 100) / 100


def index_band_color(value: float) -> str:
    for threshold, color in config.INDEX_BANDS:
        if value <= threshold:
            return color
    return config.INDEX_BANDS[-1][1]


def index_band_label(value: float) -> str:
    """Index (0-1) -> STAF condition category (Non-Functioning/Functioning-at-Risk/Functioning), matching the color bands."""
    for (threshold, _color), label in zip(config.INDEX_BANDS, config.INDEX_BAND_LABELS):
        if value <= threshold:
            return label
    return config.INDEX_BAND_LABELS[-1]


def function_score_band_color(value: float) -> str:
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
    return {
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
