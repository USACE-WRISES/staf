"""Staged analysis orchestration (SFARI).

Mirrors EASI's two-stage workflow so the UI can show staged feedback:

  1. ``delineate_only(lat, lon, reach_ft)`` — snap -> upstream watershed + reach
     (copied verbatim from EASI; the delineation engine is method-agnostic).
  2. ``pull_evidence_only(ctx_inputs, ...)`` — pull desktop GIS evidence for the
     supportable metrics (added in Phase 3).

All are pure async contracts invoked from a worker thread (no reactive access).
"""
from __future__ import annotations

from typing import Optional

import anyio

from . import delineation

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT

# The cross-section (Manning) producer in app.py writes evidence entries stamped with
# this exact source string; it is the ONLY thing that distinguishes an attached
# cross-section entry from an automatically pulled one (there is no origin flag).
XS_MANNING_SOURCE = "Native cross-section hydraulics (Manning)"


def merge_pulled_evidence(existing: dict, pulled: dict) -> dict:
    """Merge a fresh automatic evidence pull into the current evidence dict.

    Automatic entries from ``pulled`` replace/add (including new ``unavailable``
    results), while any existing entry produced by the cross-section tool (source ==
    :data:`XS_MANNING_SOURCE`) is preserved. This lets the desktop pull re-run
    (Retry) without wiping attached cross-section hydraulics. Pure: mutates neither
    argument. On first run ``existing`` has no Manning entries, so the result equals
    ``pulled`` and first-run behavior is unchanged.
    """
    merged = dict(pulled or {})
    for mid, ev in (existing or {}).items():
        if isinstance(ev, dict) and ev.get("source") == XS_MANNING_SOURCE:
            merged[mid] = ev
    return merged


def _error(msg: str, lat: float, lon: float, reach_ft: float) -> dict:
    return {"status": "error", "message": msg,
            "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}


async def delineate_only(lat: float, lon: float,
                         reach_length_ft: float = DEFAULT_REACH_FT,
                         comid: Optional[int] = None) -> dict:
    """Snap -> upstream watershed + reach (no evidence).

    When ``comid`` is given (the user clicked an NHD flowline vector), delineation
    uses it directly; otherwise the point is snapped server-side. Returns a
    JSON-serializable dict with the delineation, map overlays, and the
    ``ctx_inputs`` needed for the later evidence pull; or ``{"status": "error", ...}``.
    """
    try:
        d = await anyio.to_thread.run_sync(
            lambda: delineation.run_delineation(
                lat, lon, reach_length_ft,
                comid=comid, snapped_lat=lat, snapped_lon=lon))
    except Exception as exc:  # pragma: no cover - network guard
        return _error(f"delineation failed: {exc}", lat, lon, reach_length_ft)

    if d.comid is None:
        return _error("No NHD stream found near this point. Click on or near a "
                      "mapped stream (CONUS only).", lat, lon, reach_length_ft)

    ctx_inputs = {
        "lat": d.snapped_lat or lat, "lon": d.snapped_lon or lon, "comid": d.comid,
        "huc8": d.huc8, "watershed_geojson": d.watershed_geojson,
        "reach_geojson": d.reach_geojson, "drainage_area_sqkm": d.drainage_area_sqkm,
        "slope": d.slope, "fcode": d.fcode, "stream_order": d.stream_order,
        "sinuosity": d.sinuosity,
    }
    return {
        "status": "ok",
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": {
            "comid": d.comid,
            "gnis_name": d.gnis_name or "(unnamed reach)",
            "huc8": d.huc8,
            "huc12": None,
            "drainage_area_sqkm": d.drainage_area_sqkm,
            "slope": d.slope,
            "stream_order": d.stream_order,
            "sinuosity": d.sinuosity,
            "fcode": d.fcode,
            "snapped_lat": d.snapped_lat,
            "snapped_lon": d.snapped_lon,
            "watershed_area_sqkm": round(d.watershed_area_sqkm, 2)
            if d.watershed_area_sqkm else None,
            "reach_length_ft": d.reach_length_ft,
            "warnings": d.warnings,
        },
        "watershed_geojson": d.watershed_geojson,
        "reach_geojson": d.reach_geojson,
        "ctx_inputs": ctx_inputs,
    }


async def pull_evidence_only(ctx_inputs: dict, *, progress: Optional[dict] = None) -> dict:
    """Pull desktop GIS evidence for the supportable metrics on a prior delineation.

    Returns ``{"status": "ok", "evidence": {metricId: EvidenceResult-dict}}``. Never
    raises — individual sources degrade to ``status='unavailable'``.
    """
    from . import evidence
    ev = await evidence.pull(ctx_inputs, progress=progress)
    return {"status": "ok", "evidence": ev}
