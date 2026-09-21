"""DEEP scoring rollup — reused verbatim from SFARI.

Everything here operates on a ``{functionId: score 0-15}`` dict and is identical
to SFARI/EASI, so the three STAF tiers roll up to the same Physical / Chemical /
Biological sub-indices and Ecosystem Condition Index on one scale. DEEP's only
new scoring code lives upstream in :mod:`deep.curves`, which turns measured
metric values into the 0-15 function scores fed to :func:`rollup`.

Functions omitted from ``function_scores`` (Not Applicable, or no metric scored)
are excluded from both the numerator and denominator — correct NA handling for
free. Core functions are pure (no I/O) for testability.

A function the assessment CANNOT assess is a different thing from one that does
not apply, and is passed separately as ``unassessed``. Not Applicable is an
answer; unassessed is a gap, and a gap does not shrink the claim quietly. An
index computed over the remainder is normalised within its own denominator, so
it lands on the same 0 to 1 scale as a complete one and reads as comparable
when it is not. Scoring the same site against a 20-function bundle and against
one missing four functions moved the index from 0.68 to 0.87, across a band
boundary, purely on coverage. So an assessment with a gap reports the interval
the index could occupy once the unassessed functions are allowed their full
range, and names a band only when that interval stays inside one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

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
    # What the unassessed functions would have carried. Held apart from `max`
    # rather than folded in, because the index cannot be a point claim while it
    # is non-zero and the two quantities answer different questions.
    unassessed_max: float = 0.0
    unassessed_direct: int = 0
    unassessed_indirect: int = 0

    @property
    def sub_index(self) -> float | None:
        """The measured sub-index, or None when nothing measured it.

        An outcome with no direct contributor is carried entirely by 0.1-weighted
        indirect signal from other disciplines, which is not a measurement of that
        outcome. Returning 0.0 there, as this did whenever `max` was zero, put an
        unmeasured outcome in the Non-Functioning band and coloured it red.
        """
        if self.max <= 0 or self.direct == 0:
            return None
        return self.weighted / self.max

    @property
    def bounds(self) -> tuple[float, float] | None:
        """(low, high) the sub-index could occupy once every unassessed function is
        allowed its full 0 to 15 range. Equals (sub_index, sub_index) when nothing
        is unassessed, so a complete assessment is unaffected."""
        total = self.max + self.unassessed_max
        if total <= 0:
            return None
        return (self.weighted / total, (self.weighted + self.unassessed_max) / total)


@dataclass
class RollupResult:
    function_scores: dict[str, float]
    outcomes: dict[str, OutcomeResult]
    ecosystem_condition_index: float | None
    sub_indices: dict[str, float | None] = field(default_factory=dict)
    #: Functions the assessment could not assess, which is why the index is an
    #: interval rather than a value.
    unassessed: tuple[str, ...] = ()
    eci_bounds: tuple[float, float] | None = None
    #: The pre-restriction arithmetic: the mean of the three outcome ratios with
    #: an unmeasured outcome read as 0.0, which is what this returned before an
    #: unassessed function widened the claim. Kept because the Excel calculator
    #: computes it cell for cell and the field rail shows progress with it. It is
    #: a running total, never a condition claim.
    eci_over_scored: float = 0.0

    @property
    def assessable(self) -> bool:
        """Whether the index is a point claim. False once anything is unassessed."""
        return self.ecosystem_condition_index is not None


def rollup(
    function_scores: dict[str, float],
    mapping: dict[str, dict[str, str]] | None = None,
    weights: dict[str, float] | None = None,
    unassessed: "Iterable[str] | None" = None,
) -> RollupResult:
    """Roll function scores up to outcome sub-indices and the Ecosystem index.

    ``function_scores``: functionId -> score (0-15). Omit a function (e.g. NA) to
    exclude it from the relevant averages and denominators.

    ``unassessed``: functionIds the assessment has no basis to score. These are
    NOT the same as an omitted function. An omitted one does not apply and is
    correctly out of the denominator; an unassessed one is unknown, so it widens
    the index into an interval instead of silently narrowing the base.
    """
    mapping = mapping if mapping is not None else config.outcome_mapping()
    weights = weights if weights is not None else config.WEIGHTS
    unassessed_ids = tuple(dict.fromkeys(unassessed or ()))

    outcomes = {key: OutcomeResult() for key in config.OUTCOMES}

    for fid, score in function_scores.items():
        if score is None or fid in unassessed_ids:
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

    for fid in unassessed_ids:
        codes = mapping.get(fid)
        if codes is None:
            continue
        for key in config.OUTCOMES:
            code = codes.get(key, "-")
            weight = weights.get(code, 0.0)
            if code == "D":
                outcomes[key].unassessed_direct += 1
            elif code == "i":
                outcomes[key].unassessed_indirect += 1
            if weight:
                outcomes[key].unassessed_max += config.FUNCTION_SCORE_MAX * weight

    sub_indices = {key: outcomes[key].sub_index for key in config.OUTCOMES}
    spans = [outcomes[key].bounds for key in config.OUTCOMES]
    eci_bounds = None
    if all(b is not None for b in spans):
        n = len(spans)
        eci_bounds = (sum(b[0] for b in spans) / n, sum(b[1] for b in spans) / n)
    # A point claim needs every outcome measured and nothing left unassessed.
    complete = not unassessed_ids and all(v is not None for v in sub_indices.values())
    eci = (sum(sub_indices.values()) / len(config.OUTCOMES)) if complete else None
    # The legacy ratio, taken from the outcomes rather than from sub_indices,
    # because an outcome carried only by indirect weight still has a ratio and it
    # is the one the workbook computes. Substituting 0.0 for the new None would
    # silently change what the parity check compares.
    over_scored = sum((o.weighted / o.max) if o.max > 0 else 0.0
                      for o in outcomes.values()) / len(config.OUTCOMES)
    return RollupResult(
        function_scores=dict(function_scores),
        outcomes=outcomes,
        ecosystem_condition_index=eci,
        sub_indices=sub_indices,
        unassessed=unassessed_ids,
        eci_bounds=eci_bounds,
        eci_over_scored=over_scored,
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


def index_band_for_bounds(low: float, high: float) -> str | None:
    """The band both ends of an interval fall in, or None when it straddles a break.

    Naming a band across a break would be the false precision the interval exists
    to avoid.
    """
    lo, hi = index_band_label(low), index_band_label(high)
    return lo if lo == hi else None


def index_claim(result: dict) -> str:
    """The condition claim an assessment supports, in one line.

    A complete assessment claims a value and a band. A partial one claims the
    interval its unassessed functions leave open, and a band only when the
    interval stays inside one.
    """
    eci = result.get("ecosystemConditionIndex")
    if eci is not None:
        return f"{eci:.2f} ({index_band_label(eci)})"
    span = result.get("ecosystemConditionIndexBounds")
    n_un = int(result.get("nUnassessed") or 0)
    n_all = int(result.get("nFunctions") or 0)
    scope = (f"{n_un} of {n_all} functions not assessed" if n_un and n_all
             else "coverage incomplete")
    if not span:
        return f"not determinable, {scope}"
    low, high = float(span[0]), float(span[1])
    band = index_band_for_bounds(low, high)
    tail = f" ({band})" if band else ", condition not determinable"
    return f"{low:.2f} to {high:.2f}{tail}, {scope}"


def function_score_band_label(value: float) -> str:
    for (threshold, _color), label in zip(config.FUNCTION_SCORE_BANDS,
                                          config.FUNCTION_SCORE_BAND_SHORT):
        if value <= threshold:
            return label
    return config.FUNCTION_SCORE_BAND_SHORT[-1]


# --------------------------------------------------------------------------- #
# Convenience entry point
# --------------------------------------------------------------------------- #
def score_assessment(function_scores: dict[str, float],
                     unassessed: "Iterable[str] | None" = None) -> dict:
    """Score a full assessment from per-function 0-15 scores.

    Returns a JSON-serializable result dict for the report / rollup rail — the
    same shape SFARI's report consumes, with the coverage keys added. An index
    key is None where the assessment does not support a point claim, so every
    consumer has to decide what to print rather than inherit a number that looks
    complete. ``index_claim`` is that decision made once.
    """
    result = rollup(function_scores, unassessed=unassessed)
    cats = category_subindices(function_scores)
    opt2 = lambda v: None if v is None else round2(v)   # noqa: E731
    return {
        "functionScores": result.function_scores,
        "subIndices": {k: opt2(v) for k, v in result.sub_indices.items()},
        "subIndicesRaw": result.sub_indices,
        "outcomes": {
            k: {"weighted": round2(o.weighted), "max": round2(o.max),
                "direct": o.direct, "indirect": o.indirect,
                "unassessedDirect": o.unassessed_direct,
                "unassessedIndirect": o.unassessed_indirect,
                "subIndex": opt2(o.sub_index),
                "bounds": None if o.bounds is None else [round2(o.bounds[0]),
                                                         round2(o.bounds[1])]}
            for k, o in result.outcomes.items()
        },
        "categorySubIndices": {k: round2(v) for k, v in cats.items()},
        "categoryLabels": {k: index_band_label(v) for k, v in cats.items()},
        "ecosystemConditionIndex": opt2(result.ecosystem_condition_index),
        "ecosystemConditionIndexRaw": result.ecosystem_condition_index,
        "ecosystemConditionIndexBounds": (
            None if result.eci_bounds is None
            else [round2(result.eci_bounds[0]), round2(result.eci_bounds[1])]),
        # The per-outcome legacy ratios, which is what the workbook's cells compute.
        # An outcome with no direct contributor still has a ratio; what it does not
        # have is a meaning, which is why subIndices reports it as None.
        "subIndicesOverScored": {
            k: round2((o.weighted / o.max) if o.max > 0 else 0.0)
            for k, o in result.outcomes.items()},
        "subIndicesOverScoredRaw": {
            k: (o.weighted / o.max) if o.max > 0 else 0.0
            for k, o in result.outcomes.items()},
        "ecosystemConditionIndexOverScored": round2(result.eci_over_scored),
        "ecosystemConditionIndexOverScoredRaw": result.eci_over_scored,
        "assessable": result.assessable,
        "unassessedFunctions": list(result.unassessed),
        "nUnassessed": len(result.unassessed),
        "nFunctions": len(config.outcome_mapping()),
    }
