"""Assemble measured values for scoring.

Phase 2 is field-entry only: every value comes from the user's numeric input.
Phase 3 will add desktop auto-compute here (borrowing EASI's StreamCat / NLCD /
3DEP adapters) to prefill values for the ~15-20 desktop-computable metrics; the
UI already carries an ``origin`` ("field" | "desktop") + ``source`` per value so
that addition is drop-in.
"""
from __future__ import annotations

from .models import MeasuredValue


def measured_from_state(state: dict) -> dict:
    """``{metricId: {value, na, note, origin, source}}`` -> ``{metricId: MeasuredValue}``."""
    out: dict[str, MeasuredValue] = {}
    for mid, rc in (state or {}).items():
        rc = rc or {}
        out[mid] = MeasuredValue(
            metric_id=mid,
            value=rc.get("value"),
            na=bool(rc.get("na", False)),
            note=rc.get("note", ""),
            origin=rc.get("origin", "field"),
            source=rc.get("source", ""),
            stratum=rc.get("stratum"),
        )
    return out


def compute_metrics_only(ctx_inputs: dict, metric_ids) -> dict:
    """Desktop-compute the auto-derivable metrics for a delineated site.

    Returns ``{metricId: {value, na, note, origin, source}}`` entries (the shape
    the app's measured-values state uses) for every ``metricId`` that has a
    desktop adapter and yields a value. Never raises; the heavy datasource
    imports are lazy so this module stays importable without the geospatial stack.
    """
    from .metrics import computed
    from .metrics.base import AnalysisContext

    ctx = AnalysisContext.from_inputs(ctx_inputs)
    out: dict[str, dict] = {}
    for mid, cv in computed.compute_for(metric_ids, ctx).items():
        out[mid] = {"value": cv.value, "na": False, "note": "",
                    "origin": "desktop", "source": cv.source}
    return out
