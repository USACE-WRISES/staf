"""Staged analysis orchestration (SFARI).

Two stages so the UI can show staged feedback:

  1. ``delineate_from_engine(record, lat, lon, reach_ft)`` builds the
     delineation result from a STAF site engine record: the HR reach watershed,
     the engine's assessment reach, and the site attributes. Every site goes
     this way (2026-09-05); there is no NHDPlus V2 basin lookup.
     ``delineate_without_watershed`` is the continuation the assessor can
     choose when the engine failed: no polygon, the StreamCat reach's basin as
     the labeled basis (2026-09-07).
  2. ``pull_evidence_only(ctx_inputs, ...)`` pulls desktop evidence for the
     supportable metrics.

Pure contracts invoked from a worker thread (no reactive access).
"""
from __future__ import annotations

from typing import Optional

from . import delineation, engine_prefill

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT

# ``delineation["watershedBasis"]`` vocabulary. The site engine's watershed is
# the normal case; the two V2 values are the no-watershed continuation after
# the engine failed (2026-09-07), when the StreamCat lookup engine's basin is
# the only one the values can describe.
BASIS_SITE_ENGINE = "site-engine"                # the HR reach watershed (STAF site engine)
BASIS_V2_BASIN = "nhdplus-v2-basin"              # the covered reach's own basin
BASIS_SURROGATE_BASIN = "nhdplus-v2-basin-of-surrogate"   # the nearest StreamCat reach's basin

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

    The HR reach watershed is THE watershed, the engine reach is the assessment
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
        # the published NHDPlus HR drainage area of the snapped reach (what the
        # Basin pane calls Drainage area in every app); the engine polygon is
        # watershed_area_sqkm below, and the two agree within the engine's
        # validation band
        "drainage_area_sqkm": site.get("drainageAreaSqkm") if site.get("drainageAreaSqkm") is not None
        else ws.get("areaSqkm"),
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


def delineate_without_watershed(anchor: dict, lat: float, lon: float, hr_hit,
                                reach_length_ft: float = DEFAULT_REACH_FT,
                                engine: Optional[dict] = None) -> dict:
    """The delineation result when the STAF site engine failed or refused and
    the assessor chose to continue with the StreamCat lookup engine.

    No polygon and no reach geometry: the site is the HR reach the click
    snapped to (``hr_hit`` and the anchor's ``clickedStream``), the watershed
    basis is the NHDPlus V2 basin of the StreamCat reach (the reach itself on
    a covered stream, the nearest StreamCat reach downstream otherwise), and
    every watershed value the pull produces is labeled with that reach. The
    failed engine state rides along so the report can say why. Pure.
    """
    from . import comid_anchor
    anchor = anchor or {}
    clicked = anchor.get("clickedStream") or {}
    scored = anchor.get("scoredReach") or {}
    routed = comid_anchor.is_routed(anchor)
    hr = tuple(hr_hit) if hr_hit else (None, None, None, None)
    snap_lat = clicked.get("snapLat") if clicked.get("snapLat") is not None else hr[0]
    snap_lon = clicked.get("snapLon") if clicked.get("snapLon") is not None else hr[1]
    nhdplus_id = clicked.get("nhdplusId") if clicked.get("nhdplusId") is not None else hr[3]
    # the site is the clicked HR stream when routed, else the covered reach
    # itself (whose attributes comid_anchor.resolve filled from the fabric API;
    # until 2026-09-07 a covered continuation read them from the absent
    # clickedStream and lost them all)
    site = clicked if routed else scored
    reachcode = str(site.get("reachcode") or "")
    comid = comid_anchor.comid(anchor)
    delin = {
        "comid": comid,
        "nhdplus_id": nhdplus_id,
        "gnis_name": site.get("gnisName") or "(unnamed stream)",
        "network": "nhdplus-hr" if nhdplus_id is not None else "nhdplus-v2",
        "huc8": reachcode[:8] if reachcode else None,
        "huc12": None,
        "drainage_area_sqkm": site.get("drainageAreaSqkm"),
        "slope": site.get("slope"),
        "stream_order": site.get("streamOrder"),
        "sinuosity": None,
        "fcode": site.get("fcode"),
        "snapped_lat": snap_lat if snap_lat is not None else lat,
        "snapped_lon": snap_lon if snap_lon is not None else lon,
        "watershed_area_sqkm": None,
        "reach_length_ft": reach_length_ft,
        "warnings": ["the STAF site engine could not compute the HR reach watershed; "
                     "watershed values describe the StreamCat reach's NHDPlus V2 basin"],
    }
    basis = BASIS_SURROGATE_BASIN if routed else BASIS_V2_BASIN
    ctx_inputs = {
        "lat": delin["snapped_lat"], "lon": delin["snapped_lon"],
        "comid": comid, "huc8": delin["huc8"],
        "watershed_geojson": None, "reach_geojson": None,
        "drainage_area_sqkm": delin["drainage_area_sqkm"], "slope": delin["slope"],
        "fcode": delin["fcode"], "stream_order": delin["stream_order"],
        "sinuosity": None, "watershedBasis": basis, "siteAnchor": anchor,
    }
    return {
        "status": "ok",
        "watershedBasis": basis,
        "siteAnchor": anchor,
        "siteEngine": dict(engine) if engine else None,
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": delin,
        "watershed_geojson": None,
        "reach_geojson": None,
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
