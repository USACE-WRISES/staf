"""Staged analysis orchestration (SFARI).

Two stages so the UI can show staged feedback:

  1. ``delineate_from_engine(record, lat, lon, reach_ft)`` builds the
     delineation result from a STAF site engine record: the exact watershed,
     the engine's assessment reach, and the site attributes. Every site goes
     this way (2026-09-05); there is no NHDPlus V2 basin lookup.
  2. ``pull_evidence_only(ctx_inputs, ...)`` pulls desktop evidence for the
     supportable metrics.

Pure contracts invoked from a worker thread (no reactive access).
"""
from __future__ import annotations

from typing import Optional

from . import delineation, engine_prefill

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT

# ``delineation["watershedBasis"]`` vocabulary: one value since 2026-09-05.
# Sessions saved earlier may carry "nhdplus-v2-basin" or
# "nhdplus-v2-basin-of-surrogate"; the report still labels those.
BASIS_SITE_ENGINE = "site-engine"                # the exact watershed (STAF site engine)

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


def delineate_from_engine(record: dict, lat: float, lon: float,
                          reach_length_ft: float = DEFAULT_REACH_FT) -> dict:
    """The delineation result from a STAF site engine record.

    The exact watershed is THE watershed, the engine reach is the assessment
    reach, and the site is identified by its NHDPlusID (a COMID rides along
    only when the engine reports one). Pure (no I/O); the record must be
    ``status == "ok"``.
    """
    site = record.get("site") or {}
    ws = record.get("watershed") or {}
    reach = record.get("reach") or {}
    reachcode = site.get("reachcode") or ""
    warnings = list(ws.get("warnings") or []) + list(reach.get("warnings") or [])
    stripped = engine_prefill.strip_geometry(record)
    delin = {
        "comid": site.get("comid"),
        "nhdplus_id": site.get("nhdplusId"),
        "gnis_name": site.get("gnisName") or "(unnamed stream)",
        "network": "nhdplus-hr",
        "huc8": reachcode[:8] if reachcode else None,
        "huc12": None,
        "drainage_area_sqkm": ws.get("areaSqkm") if ws.get("areaSqkm") is not None
        else site.get("drainageAreaSqkm"),
        "slope": site.get("slope"),
        "stream_order": site.get("streamOrder"),
        "sinuosity": site.get("sinuosity"),
        "fcode": site.get("fcode"),
        "snapped_lat": site.get("snapLat"),
        "snapped_lon": site.get("snapLon"),
        "watershed_area_sqkm": ws.get("areaSqkm"),
        "reach_length_ft": reach.get("lengthFt"),
        "warnings": warnings,
    }
    ctx_inputs = {
        "lat": site.get("snapLat") if site.get("snapLat") is not None else lat,
        "lon": site.get("snapLon") if site.get("snapLon") is not None else lon,
        "comid": site.get("comid"), "huc8": delin["huc8"],
        "watershed_geojson": ws.get("polygon"), "reach_geojson": reach.get("geometry"),
        "drainage_area_sqkm": delin["drainage_area_sqkm"], "slope": site.get("slope"),
        "fcode": site.get("fcode"), "stream_order": site.get("streamOrder"),
        "sinuosity": site.get("sinuosity"),
        "watershedBasis": BASIS_SITE_ENGINE, "site_engine": stripped,
    }
    return {
        "status": "ok",
        "watershedBasis": BASIS_SITE_ENGINE,
        "siteEngine": stripped,
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": delin,
        "watershed_geojson": ws.get("polygon"),
        "reach_geojson": reach.get("geometry"),
        "ctx_inputs": ctx_inputs,
    }


async def pull_evidence_only(ctx_inputs: dict, *, progress: Optional[dict] = None,
                             engine: Optional[dict] = None) -> dict:
    """Pull desktop GIS evidence for the supportable metrics on a prior delineation.

    ``engine`` is the app's site-engine state (``{"status", "record", "reason"}``);
    None runs the engine inline. Returns ``{"status": "ok", "evidence":
    {metricId: EvidenceResult-dict}}``. Never raises; individual sources degrade
    to ``status='unavailable'``.
    """
    from . import evidence
    ev = await evidence.pull(ctx_inputs, progress=progress, engine=engine)
    return {"status": "ok", "evidence": ev}
