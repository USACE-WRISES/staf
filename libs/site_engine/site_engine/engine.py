"""``compute_site``: the engine's single public entry point.

Layer A (site context, watershed, reach) is assembled here; Layer B metric
computation plugs into ``metrics/`` and fills ``record["metrics"]``. Never
raises: every failure lands in the record with a reason, matching the STAF
datasource resilience style. Deterministic for fixed inputs + engine version;
the optional ``progress`` callback receives stage events that never enter the
record.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import context as _context
from . import delineate as _delineate
from . import provenance
from . import reach as _reach
from .progress import notify


def compute_site(lat: float, lon: float, config: Optional[dict] = None, *,
                 progress: Optional[Callable[[dict], Any]] = None) -> dict:
    """Compute the SiteComputation record for a point on any NHD HR stream.

    ``config`` keys (all optional): ``reachLengthFt``, ``snapTolFt``,
    ``maxHops``, ``maxReaches``, ``includeGeometry``, ``metricFamilies``,
    ``landcoverBaseline``. See ``provenance.DEFAULT_CONFIG`` (and
    ``INTERACTIVE_CONFIG`` for the web-app budget) and ``provenance.base_record``
    for the record shape. ``progress`` receives dict events
    (``{"stage", "hops", "reaches", "family"}``); see ``progress.py``.
    """
    cfg = provenance.resolve_config(config)
    record = provenance.base_record(lat, lon, cfg)
    try:
        notify(progress, stage="site")
        ctx = _context.site_context(lat, lon, snap_tol_ft=cfg["snapTolFt"])
        if ctx["status"] != "ok":
            record["reason"] = ctx["reason"]
            return record
        record["site"] = ctx["site"]
        anchor = ctx["anchor"]

        notify(progress, stage="walk", hops=0, reaches=1)
        ws = _delineate.delineate_watershed(
            anchor, max_hops=cfg["maxHops"], max_reaches=cfg["maxReaches"],
            progress=progress)
        tree_geoms = ws.pop("treeFlowlines", [])
        record["watershed"] = ws
        if ws["status"] == "refused":
            record["status"] = "refused"
            record["reason"] = ws["reason"]
            return record
        if ws["status"] != "ok":
            record["reason"] = ws["reason"] or "delineation failed"
            return record

        notify(progress, stage="reach")
        reach_gj, actual_ft, r_warns = _reach.derive_reach(
            anchor, ctx["site"]["snapLat"], ctx["site"]["snapLon"],
            cfg["reachLengthFt"])
        record["reach"] = {"lengthFt": actual_ft, "geometry": reach_gj,
                           "warnings": r_warns}

        # Layer B: metric computation over the watershed + riparian buffer.
        notify(progress, stage="metrics")
        from . import metrics as _metrics
        record["metrics"] = _metrics.compute_all(
            record, tree_geoms=tree_geoms, families=cfg["metricFamilies"],
            progress=progress)

        if not cfg["includeGeometry"]:
            ws["polygon"] = None
            record["reach"]["geometry"] = None
        record["status"] = "ok"
        notify(progress, stage="done")
        return record
    except Exception as exc:  # noqa: BLE001 - the contract is never-raise
        record["reason"] = f"engine error: {exc}"
        return record
