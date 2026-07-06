"""DEEP scoring rollup — reused verbatim from SFARI.

Everything here operates on a ``{functionId: score 0-15}`` dict and is identical
to SFARI/EASI, so the three STAF tiers roll up to the same Physical / Chemical /
Biological sub-indices and Ecosystem Condition Index on one scale. DEEP's only
new scoring code lives upstream in :mod:`deep.curves`, which turns measured
metric values into the 0-15 function scores fed to :func:`rollup`.

Functions omitted from ``function_scores`` (Not Applicable, or no metric scored)
are excluded from both the numerator and denominator — correct NA handling for
free. Core functions are pure (no I/O) for testability.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config


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
    function_scores: dict[str, float]
    outcomes: dict[str, OutcomeResult]
    ecosystem_condition_index: float
    sub_indices: dict[str, float] = field(default_factory=dict)


def rollup(
    function_scores: dict[str, float],
    mapping: dict[str, dict[str, str]] | None = None,
    weights: dict[str, float] | None = None,
) -> RollupResult:
    """Roll function scores up to outcome sub-indices and the Ecosystem index.

    ``function_scores``: functionId -> score (0-15). Omit a function (e.g. NA) to
    exclude it from the relevant averages and denominators.
    """
    mapping = mapping if mapping is not None else config.outcome_mapping()
    weights = weights if weights is not None else config.WEIGHTS

    outcomes = {key: OutcomeResult() for key in config.OUTCOMES}

    for fid, score in function_scores.items():
        if score is None:
            continue
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


def category_subindices(
    function_scores: dict[str, float], functions: list[dict] | None = None
) -> dict[str, float]:
    """Optional functional-category sub-index = mean of normalized function scores.

    Reported as an F/AR/NF label (per the STAF method), not a headline number.
    """
    functions = functions if functions is not None else config.functions()
    buckets: dict[str, list[float]] = {}
    for f in functions:
        fid, cat = f["id"], f["category"]
        score = function_scores.get(fid)
        if score is not None:
            buckets.setdefault(cat, []).append(score / config.FUNCTION_SCORE_MAX)
    return {cat: (sum(v) / len(v) if v else 0.0) for cat, v in buckets.items()}


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def round2(value: float) -> float:
    return round(value * 100) / 100


def index_band_color(value: float) -> str:
    for threshold, color in config.INDEX_BANDS:
        if value <= threshold:
            return color
    return config.INDEX_BANDS[-1][1]


def index_band_label(value: float) -> str:
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
    for (threshold, _color), label in zip(config.FUNCTION_SCORE_BANDS,
                                          config.FUNCTION_SCORE_BAND_SHORT):
        if value <= threshold:
            return label
    return config.FUNCTION_SCORE_BAND_SHORT[-1]


# --------------------------------------------------------------------------- #
# Convenience entry point
# --------------------------------------------------------------------------- #
def score_assessment(function_scores: dict[str, float]) -> dict:
    """Score a full assessment from per-function 0-15 scores.

    Returns a JSON-serializable result dict for the report / rollup rail — the
    same shape SFARI's report consumes.
    """
    result = rollup(function_scores)
    cats = category_subindices(function_scores)
    return {
        "functionScores": result.function_scores,
        "subIndices": {k: round2(v) for k, v in result.sub_indices.items()},
        "subIndicesRaw": result.sub_indices,
        "outcomes": {
            k: {"weighted": round2(o.weighted), "max": round2(o.max),
                "direct": o.direct, "indirect": o.indirect,
                "subIndex": round2(o.sub_index)}
            for k, o in result.outcomes.items()
        },
        "categorySubIndices": {k: round2(v) for k, v in cats.items()},
        "categoryLabels": {k: index_band_label(v) for k, v in cats.items()},
        "ecosystemConditionIndex": round2(result.ecosystem_condition_index),
        "ecosystemConditionIndexRaw": result.ecosystem_condition_index,
    }
