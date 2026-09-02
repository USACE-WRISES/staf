"""Staged analysis orchestration (DEEP).

  1. ``delineate_only(lat, lon, reach_ft)``: snap -> upstream watershed + reach
     (reused verbatim from SFARI/EASI; the delineation engine is method-agnostic).
     ``delineate_from_engine`` builds the same result shape from a STAF site
     engine record for a stream outside NHDPlus V2 (the exact watershed).
  2. ``compute_metrics_only``: the desktop auto-compute of the derivable metrics.

All are pure async contracts invoked from a worker thread (no reactive access).
"""
from __future__ import annotations

from typing import Optional

import anyio

from . import delineation, engine_prefill

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT

# ``delineation["watershedBasis"]`` vocabulary.
BASIS_V2_BASIN = "nhdplus-v2-basin"              # the NLDI basin of the V2 reach
BASIS_SITE_ENGINE = "site-engine"                # the exact watershed (STAF site engine)
BASIS_SURROGATE_BASIN = "nhdplus-v2-basin-of-surrogate"   # the labeled fallback


def _error(msg: str, lat: float, lon: float, reach_ft: float) -> dict:
    return {"status": "error", "message": msg,
            "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}


async def delineate_only(lat: float, lon: float,
                         reach_length_ft: float = DEFAULT_REACH_FT,
                         comid: Optional[int] = None,
                         anchor: Optional[dict] = None) -> dict:
    """Snap -> upstream watershed + reach (no evidence).

    When ``comid`` is given (the user clicked an NHD flowline vector), delineation
    uses it directly; otherwise the point is snapped server-side. ``anchor`` is
    a siteAnchor payload from ``hr_site`` (a covered click's ``v2Direct`` anchor
    or, for the labeled fallback on an HR-only site, its ``hrSurrogate`` anchor);
    absent, a covered anchor is synthesized. Returns a JSON-serializable dict
    with the delineation, map overlays, and the ``ctx_inputs`` needed for the
    later desktop compute; or ``{"status": "error", ...}``.
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

    if anchor is None:
        from . import hr_site
        anchor = hr_site.v2_anchor(d.comid, lat, lon, d.snapped_lat, d.snapped_lon)
    scored = anchor.setdefault("scoredReach", {})
    if scored.get("gnisName") is None:
        scored["gnisName"] = d.gnis_name
    if scored.get("drainageAreaSqkm") is None:
        scored["drainageAreaSqkm"] = d.drainage_area_sqkm
    basis = (BASIS_SURROGATE_BASIN if anchor.get("anchorKind") == "hrSurrogate"
             else BASIS_V2_BASIN)

    ctx_inputs = {
        "lat": d.snapped_lat or lat, "lon": d.snapped_lon or lon, "comid": d.comid,
        "huc8": d.huc8, "watershed_geojson": d.watershed_geojson,
        "reach_geojson": d.reach_geojson, "drainage_area_sqkm": d.drainage_area_sqkm,
        "slope": d.slope, "fcode": d.fcode, "stream_order": d.stream_order,
        "sinuosity": d.sinuosity, "siteAnchor": anchor, "watershedBasis": basis,
    }
    return {
        "status": "ok",
        "siteAnchor": anchor,
        "watershedBasis": basis,
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": {
            "comid": d.comid,
            "gnis_name": d.gnis_name or "(unnamed reach)",
            "network": "nhdplus-v2",
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


def delineate_from_engine(record: dict, anchor: dict, lat: float, lon: float,
                          reach_length_ft: float = DEFAULT_REACH_FT) -> dict:
    """The ``delineate_only`` result shape from a STAF site engine record.

    For a stream outside NHDPlus V2: the exact watershed is THE watershed, the
    engine reach is the assessment reach (3DEP runs on it), and the COMID is
    the nearest covered reach from the anchor (None when the routing was
    declined), which keys the labeled StreamCat values. Pure (no I/O); the
    record must be ``status == "ok"``.
    """
    site = record.get("site") or {}
    ws = record.get("watershed") or {}
    reach = record.get("reach") or {}
    from . import hr_site
    comid = None if hr_site.declined(anchor) else (anchor.get("scoredReach") or {}).get("comid")
    reachcode = site.get("reachcode") or ""
    warnings = list(ws.get("warnings") or []) + list(reach.get("warnings") or [])
    stripped = engine_prefill.strip_geometry(record)
    delin = {
        "comid": comid,
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
        "comid": comid, "huc8": delin["huc8"],
        "watershed_geojson": ws.get("polygon"), "reach_geojson": reach.get("geometry"),
        "drainage_area_sqkm": delin["drainage_area_sqkm"], "slope": site.get("slope"),
        "fcode": site.get("fcode"), "stream_order": site.get("streamOrder"),
        "sinuosity": site.get("sinuosity"), "siteAnchor": anchor,
        "watershedBasis": BASIS_SITE_ENGINE, "site_engine": stripped,
    }
    return {
        "status": "ok",
        "siteAnchor": anchor,
        "watershedBasis": BASIS_SITE_ENGINE,
        "siteEngine": stripped,
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": delin,
        "watershed_geojson": ws.get("polygon"),
        "reach_geojson": reach.get("geometry"),
        "ctx_inputs": ctx_inputs,
    }


async def compute_metrics_only(ctx_inputs: dict, metric_ids: list,
                               assessment=None, engine_record: Optional[dict] = None) -> dict:
    """Desktop-compute the auto-derivable metrics on a worker thread.

    Reuses EASI's StreamCat / NLCD / 3DEP code via :func:`deep.measure.compute_metrics_only`.
    ``assessment`` (the loaded bundle) gates the vendored site engine: engine
    adapters supply exact-watershed values only when the bundle's curves were
    fitted on engine predictors (its ``predictorSource``) or the pairing mode
    is ``label``. ``engine_record`` is a record the app already computed for
    the site, so the adapters never run the engine twice. Returns
    ``{metricId: measured-value-entry}``; never raises.
    """
    from . import measure
    return await anyio.to_thread.run_sync(
        lambda: measure.compute_metrics_only(ctx_inputs, metric_ids,
                                             assessment=assessment,
                                             engine_record=engine_record))
