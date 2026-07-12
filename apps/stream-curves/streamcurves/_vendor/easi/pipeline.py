"""Analysis orchestration, split into staged steps for the workflow UI.

The StreamStats-style UI runs these as two sequential ``extended_task``s so it can
show staged feedback ("Delineating watershed…", then "Computing metrics…"):

  1. ``delineate_only(lat, lon, reach_ft)`` — snap (already done client-side) ->
     watershed + upstream reach. Returns the delineation + the ctx inputs.
  2. ``assess_only(ctx_inputs, metric_ids, sources, overrides)`` — run the selected
     metric adapters and score.

``run_analysis`` chains both as a one-shot (kept for scripts/tests). All are pure
async contracts invoked from a worker thread (no reactive access).
"""
from __future__ import annotations

from typing import Optional

import anyio

from . import assessment, delineation
from .metrics.base import AnalysisContext

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT


def _error(msg: str, lat: float, lon: float, reach_ft: float) -> dict:
    return {"status": "error", "message": msg,
            "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}


async def delineate_only(lat: float, lon: float,
                         reach_length_ft: float = DEFAULT_REACH_FT,
                         comid: Optional[int] = None) -> dict:
    """Snap -> upstream watershed + reach (no metrics).

    When ``comid`` is given (the user clicked an NHD flowline vector), delineation
    uses it directly; otherwise the point is snapped server-side. Returns a
    JSON-serializable dict with the delineation, map overlays, and the
    ``ctx_inputs`` needed to assess later; or ``{"status": "error", ...}``.
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
            "huc12": None,  # filled in after assess (the HUC12 pull lives there)
            "drainage_area_sqkm": d.drainage_area_sqkm,
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


def _ctx_from_inputs(ci: dict) -> AnalysisContext:
    return AnalysisContext(
        lat=ci["lat"], lon=ci["lon"], comid=ci["comid"], huc8=ci.get("huc8"),
        watershed_geojson=ci.get("watershed_geojson"),
        reach_geojson=ci.get("reach_geojson"),
        drainage_area_sqkm=ci.get("drainage_area_sqkm"), slope=ci.get("slope"),
        fcode=ci.get("fcode"), stream_order=ci.get("stream_order"),
        sinuosity=ci.get("sinuosity"))


async def assess_only(ctx_inputs: dict,
                      metric_ids: Optional[list[str]] = None,
                      sources: Optional[dict[str, str]] = None,
                      overrides: Optional[dict[str, str]] = None,
                      progress: Optional[dict] = None) -> dict:
    """Run the selected metric adapters on a prior delineation.

    ``progress`` (a shared ``{"done","total"}`` dict) is updated as adapters
    finish so the UI can show live "X/N metrics computed" feedback.
    """
    ctx = _ctx_from_inputs(ctx_inputs)
    report = await assessment.assess(ctx, metric_ids=metric_ids, sources=sources,
                                     overrides=overrides, progress=progress)
    return {"status": "ok", "report": report, "huc12": ctx.huc12}


async def run_analysis(lat: float, lon: float,
                       reach_length_ft: float = DEFAULT_REACH_FT,
                       overrides: Optional[dict[str, str]] = None) -> dict:
    """One-shot delineate + assess (kept for scripts/tests)."""
    d = await delineate_only(lat, lon, reach_length_ft)
    if d.get("status") != "ok":
        return d
    a = await assess_only(d.pop("ctx_inputs"), overrides=overrides)
    d["delineation"]["huc12"] = a.get("huc12")
    d["report"] = a["report"]
    return d
