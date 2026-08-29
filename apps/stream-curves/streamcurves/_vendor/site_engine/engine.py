"""``compute_site`` — the engine's single public entry point.

Layer A (site context, watershed, reach) is assembled here; Layer B metric
computation plugs into ``metrics/`` and fills ``record["metrics"]``. Never
raises: every failure lands in the record with a reason, matching the STAF
datasource resilience style. Deterministic for fixed inputs + engine version.
"""
from __future__ import annotations

from typing import Optional

from . import context as _context
from . import delineate as _delineate
from . import provenance
from . import reach as _reach


def compute_site(lat: float, lon: float, config: Optional[dict] = None) -> dict:
    """Compute the SiteComputation record for a point on any NHD HR stream.

    ``config`` keys (all optional): ``reachLengthFt``, ``snapTolFt``,
    ``maxHops``, ``maxReaches``, ``includeGeometry``. See
    ``provenance.DEFAULT_CONFIG`` for defaults and ``provenance.base_record``
    for the record shape.
    """
    cfg = provenance.resolve_config(config)
    record = provenance.base_record(lat, lon, cfg)
    try:
        ctx = _context.site_context(lat, lon, snap_tol_ft=cfg["snapTolFt"])
        if ctx["status"] != "ok":
            record["reason"] = ctx["reason"]
            return record
        record["site"] = ctx["site"]
        anchor = ctx["anchor"]

        ws = _delineate.delineate_watershed(
            anchor, max_hops=cfg["maxHops"], max_reaches=cfg["maxReaches"])
        tree_geoms = ws.pop("treeFlowlines", [])
        record["watershed"] = ws
        if ws["status"] == "refused":
            record["status"] = "refused"
            record["reason"] = ws["reason"]
            return record
        if ws["status"] != "ok":
            record["reason"] = ws["reason"] or "delineation failed"
            return record

        reach_gj, actual_ft, r_warns = _reach.derive_reach(
            anchor, ctx["site"]["snapLat"], ctx["site"]["snapLon"],
            cfg["reachLengthFt"])
        record["reach"] = {"lengthFt": actual_ft, "geometry": reach_gj,
                           "warnings": r_warns}

        # Layer B: metric computation over the watershed + riparian buffer.
        from . import metrics as _metrics
        record["metrics"] = _metrics.compute_all(
            record, tree_geoms=tree_geoms)

        if not cfg["includeGeometry"]:
            ws["polygon"] = None
            record["reach"]["geometry"] = None
        record["status"] = "ok"
        return record
    except Exception as exc:  # noqa: BLE001 - the contract is never-raise
        record["reason"] = f"engine error: {exc}"
        return record
