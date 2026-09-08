"""Assemble measured values for scoring.

Field entry and desktop auto-compute meet here: every value carries an
``origin`` ("field" | "desktop"), a ``source`` label, a ``basis`` (which engine
or layer produced a desktop value: ``site-engine`` | ``streamcat`` | ``nlcd``
| ``3dep``), and the ``engine`` flag the scoring layer's train/serve pairing
rule reads.
"""
from __future__ import annotations

from typing import Optional

from .models import MeasuredValue


def measured_from_state(state: dict) -> dict:
    """``{metricId: {value, na, note, origin, source, engine}}`` -> ``{metricId: MeasuredValue}``.

    The ``engine`` flag survives only on desktop-origin values (the
    :meth:`MeasuredValue.from_dict` rule): a user edit stamps ``origin="field"``
    and clears it, so an edited value never carries engine provenance it no
    longer has. This is the path the running app scores through, so the
    pairing rule can only fire if the flag is kept here.
    """
    out: dict[str, MeasuredValue] = {}
    for mid, rc in (state or {}).items():
        rc = rc or {}
        origin = rc.get("origin", "field")
        out[mid] = MeasuredValue(
            metric_id=mid,
            value=rc.get("value"),
            na=bool(rc.get("na", False)),
            note=rc.get("note", ""),
            origin=origin,
            source=rc.get("source", ""),
            stratum=rc.get("stratum"),
            engine=bool(rc.get("engine", False)) and origin == "desktop",
        )
    return out


def compute_metrics_only(ctx_inputs: dict, metric_ids, *, assessment=None,
                         engine_record: Optional[dict] = None) -> dict:
    """Desktop-compute the auto-derivable metrics for a delineated site.

    Returns ``{metricId: {value, na, note, origin, source, engine, basis}}``
    entries (the shape the app's measured-values state uses) for every
    ``metricId`` that has a desktop adapter and yields a value.

    ``assessment`` (a LoadedAssessment or raw bundle) gates the site engine
    metric by metric: an engine adapter may supply a value only for a curve
    fitted on engine predictors (its own ``predictorSource`` stamp; the
    bundle-level stamp is the default for ids the bundle does not describe)
    or when the pairing mode is ``label``; every other adapter keeps the
    StreamCat/NLCD source its curve was trained on, and the scoring layer's
    pairing rule backstops any state that slips past. ``engine_record`` (or
    ``ctx_inputs["site_engine"]``) is the ok record the app already computed
    for the site; the adapters never run the engine themselves. The site
    anchor and watershed basis in ``ctx_inputs`` label COMID-keyed values on
    a stream outside NHDPlus V2. Never raises; the heavy datasource imports
    are lazy so this module stays importable without the geospatial stack.
    """
    from . import assessments, curves
    from .metrics import computed
    from .metrics.base import AnalysisContext

    ctx = AnalysisContext.from_inputs(ctx_inputs)
    ctx.extras["site_anchor"] = ctx_inputs.get("siteAnchor") or {}
    ctx.extras["watershed_basis"] = ctx_inputs.get("watershedBasis") or ""
    pre = engine_record or ctx_inputs.get("site_engine")
    if isinstance(pre, dict) and pre.get("status") == "ok":
        ctx.extras["site_engine_prefetched"] = pre
    if assessment is not None:
        label_mode = curves.ENGINE_PAIRING_MODE == "label"
        ctx.extras["allow_engine"] = (
            assessments.predictor_source_of(assessment) != "streamcat" or label_mode)
        ctx.extras["allow_engine_by_id"] = {
            mid: (ok or label_mode)
            for mid, ok in assessments.engine_gate_by_metric(assessment).items()}
    out: dict[str, dict] = {}
    for mid, cv in computed.compute_for(metric_ids, ctx).items():
        out[mid] = {"value": cv.value, "na": False, "note": "",
                    "origin": "desktop", "source": cv.source,
                    "engine": bool(getattr(cv, "engine", False)),
                    "basis": getattr(cv, "basis", "") or ""}
    return out
