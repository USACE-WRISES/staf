"""DEEP curve-scoring engine — the one piece of scoring math unique to the tier.

Turn a measured metric value into a 0-1 index by piecewise-linear interpolation
on the metric's reference curve, then average a function's metric indices into a
0-15 function score. Everything downstream (function -> outcome -> ECI) is
:mod:`deep.scoring`, reused from SFARI.

Curve points come from the STAF metric library. Every layer stores points sorted
ascending in x, with the index (y) encoding direction (higher- vs lower-is-
better), so interpolation only needs sort-by-x + clamping at both ends. We sort
defensively anyway and clamp indices into [0, 1].
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence, Union

from . import config, scoring
from .models import FunctionResult, MeasuredValue


def _clamp01(y: float) -> float:
    return 0.0 if y < 0.0 else 1.0 if y > 1.0 else y


def interp_curve(points: Sequence[dict], x: float) -> Optional[float]:
    """Interpolate a reference curve at ``x`` -> index in [0, 1].

    ``points`` is a list of ``{"x": float, "y": float}`` in any order. Returns the
    clamped index, or ``None`` if there are no usable points. Values beyond the
    curve domain clamp to the nearest endpoint's index (so a "higher-is-better"
    curve saturates at 1 on the right, a "lower-is-better" curve at 1 on the left
    — direction is carried entirely by the y values).
    """
    pts = sorted(
        (
            {"x": float(p["x"]), "y": float(p["y"])}
            for p in points
            if p.get("x") is not None and p.get("y") is not None
        ),
        key=lambda p: p["x"],
    )
    if not pts:
        return None
    if len(pts) == 1 or x <= pts[0]["x"]:
        return _clamp01(pts[0]["y"])
    if x >= pts[-1]["x"]:
        return _clamp01(pts[-1]["y"])
    for a, b in zip(pts, pts[1:]):
        if a["x"] <= x <= b["x"]:
            span = b["x"] - a["x"]
            if span <= 0:  # coincident x (vertical step) -> take the later point
                return _clamp01(b["y"])
            t = (x - a["x"]) / span
            return _clamp01(a["y"] + t * (b["y"] - a["y"]))
    return _clamp01(pts[-1]["y"])  # pragma: no cover — unreachable given the clamps above


def _fmt_bound(v: float) -> str:
    """Domain bounds for the advisory: four decimals at most, so a seed edge
    such as 2.086666666666667 reads 2.0867 while 8.0 stays 8.0."""
    return str(round(float(v), 4))


def domain_warning(points: Sequence[dict], x: float) -> Optional[str]:
    """Advisory when ``x`` falls outside a curve's x-domain (its score is clamped).

    :func:`interp_curve` silently clamps an out-of-domain value to the nearest
    endpoint's index instead of interpolating. This surfaces that clamp as a short
    human-readable message when ``x`` is strictly below the minimum or above the
    maximum x over the curve's usable points (same ``x``/``y``-not-None filter as
    interpolation), else ``None``. Curves with fewer than two usable points have no
    meaningful domain, so they return ``None``.

    Purely additive: it never changes the interpolated index — it only describes
    the clamp that :func:`interp_curve` already performs.
    """
    xs = sorted(
        float(p["x"])
        for p in points
        if p.get("x") is not None and p.get("y") is not None
    )
    if len(xs) < 2:
        return None
    lo, hi = xs[0], xs[-1]
    xf = float(x)
    if xf < lo:
        return f"value {_fmt_bound(xf)} is below the curve domain [{_fmt_bound(lo)}, {_fmt_bound(hi)}]; score clamped to the endpoint"
    if xf > hi:
        return f"value {_fmt_bound(xf)} is above the curve domain [{_fmt_bound(lo)}, {_fmt_bound(hi)}]; score clamped to the endpoint"
    return None


def curve_strata(metric_spec: dict) -> list[str]:
    """Stratum labels for a metric's curve layers (empty list if single-curve)."""
    return [str(layer.get("stratum", "")) for layer in (metric_spec.get("curveLayers") or [])]


def active_points(metric_spec: dict, stratum: Optional[str] = None) -> list:
    """Points for the metric's active curve layer.

    Multi-stratum metrics carry ``curveLayers`` (one layer per stratum); pick the
    chosen ``stratum``, else the declared ``activeStratum``, else the first layer.
    Single-curve metrics use ``curve.points``.
    """
    layers = metric_spec.get("curveLayers")
    if layers:
        if stratum:
            for layer in layers:
                if str(layer.get("stratum", "")) == str(stratum):
                    return layer.get("points") or []
        active = metric_spec.get("activeStratum")
        if active:
            for layer in layers:
                if str(layer.get("stratum", "")) == str(active):
                    return layer.get("points") or []
        return layers[0].get("points") or []
    return (metric_spec.get("curve") or {}).get("points") or []


# The train/serve pairing rule's mode. "refuse": an engine-computed value never
# scores against a curve fitted on StreamCat predictors (it displays as
# reference evidence). "label": it scores, with an approximation advisory.
# The score-level equivalence study (libs/site_engine/scripts/
# score_equivalence_study.py, 30 NRSA sites in the Northeastern Highlands and
# the Eastern Corn Belt Plains) is the only thing that flips this: "label" on
# Outcome A (rating agreement and ECI class agreement at or above 0.90 and a
# median DEEP index shift under 0.05), "refuse" otherwise. It reported
# Outcome B on 2026-09-02 (rating agreement 0.84 pooled, 0.78 and 0.90 by
# region; class agreement 0.97; median shift 0.013), so this stays "refuse".
ENGINE_PAIRING_MODE = "refuse"
_ENGINE_PAIRING_MODES = ("refuse", "label")


def _mismatched_pairing(measured: Optional[MeasuredValue], metric_spec: dict) -> bool:
    """An engine-computed value meeting a curve fitted on StreamCat predictors."""
    if measured is None or not getattr(measured, "engine", False):
        return False
    spec_ps = str((metric_spec or {}).get("predictorSource") or "streamcat")
    return spec_ps == "streamcat"


def engine_pairing_advisory(measured: Optional[MeasuredValue],
                            metric_spec: dict) -> Optional[str]:
    """The train/serve pairing rule, stated: an engine-computed value must not
    score against a curve fitted on StreamCat predictors.

    Engine values (``measured.engine``) score only when the curve's provenance
    records engine predictors (the per-metric ``predictorSource`` stamp, or the
    absent-means-streamcat default). Returns the advisory text when the pairing
    is blocked, else ``None``. The value still displays as labeled reference
    evidence; it just never enters the function mean. In ``label`` mode
    (:data:`ENGINE_PAIRING_MODE`) nothing is blocked and this returns ``None``;
    :func:`engine_approximation_advisory` carries the caveat instead.
    """
    if ENGINE_PAIRING_MODE == "label":
        return None
    if not _mismatched_pairing(measured, metric_spec):
        return None
    return ("engine-computed value shown as reference only: this curve was "
            "fitted on StreamCat predictors, so scoring it against an "
            "exact-watershed value would mix training and serving sources")


def engine_approximation_advisory(measured: Optional[MeasuredValue],
                                  metric_spec: dict) -> Optional[str]:
    """In ``label`` mode, the caveat that rides beside a scored engine value on a
    StreamCat-fitted curve. ``None`` in ``refuse`` mode or when the pairing
    matches."""
    if ENGINE_PAIRING_MODE != "label":
        return None
    if not _mismatched_pairing(measured, metric_spec):
        return None
    return ("engine-computed value scored against a StreamCat-fitted curve, "
            "accepted as an approximation by the score-level equivalence study")


def metric_index(measured: Optional[MeasuredValue], metric_spec: dict) -> Optional[float]:
    """Index (0-1) for one metric's measured value, or ``None`` if unscored.

    Returns ``None`` when the value is missing / Not Applicable, when the
    metric carries no curve points (so it drops out of its function's mean), or
    when the train/serve pairing rule blocks an engine-computed value against a
    StreamCat-fitted curve (:func:`engine_pairing_advisory`). For a
    multi-stratum metric the measured value's ``stratum`` selects the curve layer.
    """
    if measured is None or not measured.is_scored:
        return None
    if engine_pairing_advisory(measured, metric_spec) is not None:
        return None
    points = active_points(metric_spec, getattr(measured, "stratum", None))
    return interp_curve(points, float(measured.value))


THIN_SAMPLE_DISPOSITIONS = ("insufficient", "too_few")


def sample_advisory(metric_spec: dict) -> Optional[str]:
    """Advisory for a curve built from a reference sample below the builder's
    exploratory floor (the bundle's ``sampleDisposition`` and ``referenceN``,
    stamped by StreamCurves). The point value of such a curve moves by up to a
    band width when one reference site is dropped, so the honest reading is the
    band, not the number. ``None`` when the bundle carries no such stamp.
    """
    disp = str((metric_spec or {}).get("sampleDisposition") or "").strip().lower()
    if disp not in THIN_SAMPLE_DISPOSITIONS:
        return None
    n = (metric_spec or {}).get("referenceN")
    n_txt = f"{int(n)} reference sites" if isinstance(n, (int, float)) else "few reference sites"
    return (f"built from {n_txt}, below the exploratory floor: read the condition "
            "band, not the point value")


def reference_range_advisory(metric_spec: dict, x: float) -> Optional[str]:
    """Advisory when ``x`` falls outside the reference pool's observed span
    (the bundle's ``referenceRange``, stamped by StreamCurves). Inside the
    curve's domain but outside the pool, the score comes from the seed's
    sub-reference convention rather than from reference data, which a scorer
    should know. ``None`` when the bundle carries no range."""
    rng = (metric_spec or {}).get("referenceRange")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return None
    try:
        lo, hi = float(rng[0]), float(rng[1])
    except (TypeError, ValueError):
        return None
    xf = float(x)
    if xf < lo:
        return (f"value {xf:g} is below the reference pool's range ({lo:g} to {hi:g}); "
                "the score follows the seed's sub-reference convention, not reference data")
    if xf > hi:
        return (f"value {xf:g} is above the reference pool's range ({lo:g} to {hi:g}); "
                "the score follows the seed's convention beyond the data")
    return None


def metric_warning(measured: Optional[MeasuredValue], metric_spec: dict) -> Optional[str]:
    """Scoring advisories for one metric's measured value, or ``None``.

    Mirrors :func:`metric_index`: ``None`` when the value is missing / Not
    Applicable. Otherwise flags when the value sits outside the active curve's
    x-domain (where the interpolated index is clamped to an endpoint) and, for a
    curve the builder stamped as thin-sampled, says to read the band rather than
    the point value. The measured value's ``stratum`` selects the curve layer,
    exactly as for scoring. Purely additive: never changes the index.
    """
    if measured is None or not measured.is_scored:
        return None
    pairing = engine_pairing_advisory(measured, metric_spec)
    if pairing is not None:
        # The value is excluded from scoring; the advisory is the whole story.
        return pairing
    points = active_points(metric_spec, getattr(measured, "stratum", None))
    parts = [engine_approximation_advisory(measured, metric_spec),
             domain_warning(points, float(measured.value)),
             reference_range_advisory(metric_spec, float(measured.value)),
             sample_advisory(metric_spec)]
    parts = [p for p in parts if p]
    return "; ".join(parts) if parts else None


def function_index(
    metrics: Sequence[dict], measured_by_id: dict[str, MeasuredValue]
) -> tuple[Optional[float], dict[str, Optional[float]]]:
    """(function score 0-15 | None, {metricId: index}) for one function.

    The score is the mean of the function's non-NA metric indices scaled to 0-15.
    Returns ``(None, {...})`` when no metric in the function was scored, so the
    caller omits the function from the rollup (NA handling).
    """
    indices: dict[str, Optional[float]] = {}
    scored: list[float] = []
    for m in metrics:
        mid = m["metricId"]
        idx = metric_index(measured_by_id.get(mid), m)
        indices[mid] = idx
        if idx is not None:
            scored.append(idx)
    if not scored:
        return None, indices
    return (sum(scored) / len(scored)) * config.FUNCTION_SCORE_MAX, indices


# --------------------------------------------------------------------------- #
# Site scoring
# --------------------------------------------------------------------------- #
MeasuredInput = Union[dict[str, MeasuredValue], Iterable[MeasuredValue]]


def _index_measured(measured: MeasuredInput) -> dict[str, MeasuredValue]:
    if isinstance(measured, dict):
        return measured
    return {m.metric_id: m for m in measured}


def _metrics_by_function(assessment) -> list[dict]:
    mbf = getattr(assessment, "metrics_by_function", None)
    if mbf is not None:
        return mbf
    return assessment.get("metricsByFunction", [])


def score_site(assessment, measured: MeasuredInput) -> tuple[dict, dict[str, FunctionResult]]:
    """Score a site: measured values -> function scores -> outcome rollup.

    ``assessment``: a :class:`deep.assessments.LoadedAssessment` or the raw
    assessment dict. ``measured``: an iterable of :class:`MeasuredValue` or a
    ``{metricId: MeasuredValue}`` map. Returns ``(result_dict, function_results)``
    where ``result_dict`` is the same shape SFARI's report / rollup rail consumes
    and ``function_results`` maps each functionId to a :class:`FunctionResult`.
    """
    measured_by_id = _index_measured(measured)
    function_scores: dict[str, float] = {}
    function_results: dict[str, FunctionResult] = {}
    for fn in _metrics_by_function(assessment):
        fid = fn["functionId"]
        fn_metrics = fn.get("metrics", [])
        score, indices = function_index(fn_metrics, measured_by_id)
        warnings = {
            m["metricId"]: metric_warning(measured_by_id.get(m["metricId"]), m)
            for m in fn_metrics
        }
        function_results[fid] = FunctionResult(
            function_id=fid, metric_indices=indices, score=score, na=score is None,
            metric_warnings=warnings,
        )
        if score is not None:
            function_scores[fid] = score
    return scoring.score_assessment(function_scores), function_results
