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
        return f"value {xf} is below the curve domain [{lo}, {hi}]; score clamped to the endpoint"
    if xf > hi:
        return f"value {xf} is above the curve domain [{lo}, {hi}]; score clamped to the endpoint"
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


def metric_index(measured: Optional[MeasuredValue], metric_spec: dict) -> Optional[float]:
    """Index (0-1) for one metric's measured value, or ``None`` if unscored.

    Returns ``None`` when the value is missing / Not Applicable, or when the
    metric carries no curve points (so it drops out of its function's mean). For a
    multi-stratum metric the measured value's ``stratum`` selects the curve layer.
    """
    if measured is None or not measured.is_scored:
        return None
    points = active_points(metric_spec, getattr(measured, "stratum", None))
    return interp_curve(points, float(measured.value))


def metric_warning(measured: Optional[MeasuredValue], metric_spec: dict) -> Optional[str]:
    """Endpoint-clamp advisory for one metric's measured value, or ``None``.

    Mirrors :func:`metric_index`: ``None`` when the value is missing / Not
    Applicable, otherwise flags when the value sits outside the active curve's
    x-domain (where the interpolated index is clamped to an endpoint). The
    measured value's ``stratum`` selects the curve layer, exactly as for scoring.
    """
    if measured is None or not measured.is_scored:
        return None
    points = active_points(metric_spec, getattr(measured, "stratum", None))
    return domain_warning(points, float(measured.value))


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
