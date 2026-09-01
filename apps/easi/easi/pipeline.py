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

from . import assessment, delineation, routing
from .metrics.base import AnalysisContext

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT


def _error(msg: str, lat: float, lon: float, reach_ft: float, *,
           code: str = "delineation_failed", retryable: bool = True) -> dict:
    return {"status": "error", "code": code, "retryable": retryable, "message": msg,
            "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}


async def delineate_only(lat: float, lon: float,
                         reach_length_ft: float = DEFAULT_REACH_FT,
                         comid: Optional[int] = None,
                         anchor: Optional[dict] = None,
                         snap_tolerance_ft: float = routing.HR_SNAP_TOL_FT,
                         *, watershed_engine: str = routing.POLICY_AUTO,
                         progress: Optional[dict] = None) -> dict:
    """Snap -> upstream watershed + reach (no metrics).

    When ``comid`` is given (the user clicked an NHD flowline vector), delineation
    uses it directly; otherwise the point runs through ``routing.resolve_anchor``,
    which either lands on the covered V2 network (identical to the historical
    server-side snap) or routes an HR-only stream to its covered downstream
    surrogate — or refuses when the substitution exceeds the published
    drainage-area-ratio bound. ``anchor`` is a pre-resolved siteAnchor payload
    (the UI resolves at click time so the banner can render before delineation).
    Every successful result carries ``siteAnchor``; a routing refusal returns
    ``{"status": "error", "code": "surrogate_da_ratio_exceeded" | ...}`` with the
    partial anchor attached. Returns a JSON-serializable dict with the
    delineation, map overlays, and the ``ctx_inputs`` needed to assess later; or
    ``{"status": "error", ...}``. ``watershed_engine`` is the policy for
    streams outside the StreamCat lookup network (``routing.POLICY_AUTO`` or
    ``routing.POLICY_STREAMCAT_LEGACY``); ``progress`` is an optional shared
    dict the engine run updates for the UI.
    """
    if watershed_engine not in routing.WATERSHED_ENGINE_POLICIES:
        return _error(f"unknown watershed engine policy {watershed_engine!r}",
                      lat, lon, reach_length_ft, code="invalid_request",
                      retryable=False)
    site_anchor = anchor
    if comid is None and site_anchor is None:
        try:
            resolved = await anyio.to_thread.run_sync(
                lambda: routing.resolve_anchor(lat, lon,
                                               snap_tol_ft=snap_tolerance_ft))
        except ImportError as exc:        # ModuleNotFoundError is a subclass
            return _error(f"geospatial engine dependency missing: {exc}", lat,
                          lon, reach_length_ft, code="engine_dependency_missing",
                          retryable=False)
        if resolved.get("error") == "snap_service_error":
            return _error("Could not reach the NHD snap service: "
                          f"{resolved.get('detail')}", lat, lon, reach_length_ft,
                          code="snap_service_error", retryable=True)
        if resolved.get("error") == "no_stream_found":
            return _error("No NHD stream found near this point. Click on or "
                          "near a mapped stream (CONUS only).", lat, lon,
                          reach_length_ft, code="no_stream_found",
                          retryable=False)
        if resolved.get("refused"):
            # A policy refusal, not a failure: the routing worked and the
            # answer is "EASI declines to score here". Deterministic, so never
            # retryable; the partial anchor rides along for the UI and exports.
            err = _error(resolved["message"], lat, lon, reach_length_ft,
                         code=resolved["code"], retryable=False)
            err["anchor"] = resolved.get("anchor")
            return err
        site_anchor = resolved["anchor"]

    run_snap_lat, run_snap_lon = lat, lon
    if comid is None and site_anchor is not None:
        scored = site_anchor.get("scoredReach") or {}
        comid = scored.get("comid")
        if scored.get("snapLat") is not None:
            run_snap_lat, run_snap_lon = scored["snapLat"], scored["snapLon"]

    try:
        d = await anyio.to_thread.run_sync(
            lambda: delineation.run_delineation(
                lat, lon, reach_length_ft,
                comid=comid, snapped_lat=run_snap_lat, snapped_lon=run_snap_lon))
    except ImportError as exc:            # ModuleNotFoundError is a subclass
        # A missing geospatial dependency is a deployment fault, not a transient
        # outage, so retrying only doubles the wall-clock of a run that cannot
        # succeed.
        return _error(f"geospatial engine dependency missing: {exc}", lat, lon,
                      reach_length_ft, code="engine_dependency_missing",
                      retryable=False)
    except Exception as exc:  # pragma: no cover - network guard
        return _error(f"delineation failed: {exc}", lat, lon, reach_length_ft)

    if d.comid is None:
        if d.snap_error:
            # Every snap endpoint errored. This is an outage, not a statement
            # about the geometry, so it is worth retrying and must not be
            # reported as "no stream here".
            return _error(f"Could not reach the NHD snap service: {d.snap_error}",
                          lat, lon, reach_length_ft,
                          code="snap_service_error", retryable=True)
        # The service answered and found nothing. That is a permanent fact about
        # the geometry, so a second attempt only costs the caller a backoff.
        return _error("No NHD stream found near this point. Click on or near a "
                      "mapped stream (CONUS only).", lat, lon, reach_length_ft,
                      code="no_stream_found", retryable=False)

    if site_anchor is None:
        # Direct-COMID callers (the V2 vector click, batch rows with a comid
        # column) get the covered-network anchor synthesized so every result
        # carries siteAnchor provenance.
        site_anchor = routing.v2_anchor(d.comid, lat, lon)
    scored = site_anchor.setdefault("scoredReach", {})
    if scored.get("gnisName") is None:
        scored["gnisName"] = d.gnis_name
    if scored.get("drainageAreaSqkm") is None:
        scored["drainageAreaSqkm"] = d.drainage_area_sqkm
    if scored.get("snapLat") is None:
        scored["snapLat"], scored["snapLon"] = d.snapped_lat, d.snapped_lon

    # Phase 2 re-anchoring: for a routed site, reach-scale inputs come from the
    # true clicked HR stream (attrs + a reach trimmed on the HR mainstem) while
    # the watershed and its COMID-keyed sources stay on the surrogate. Covered
    # clicks never enter this branch.
    reanchor: dict = {}
    if site_anchor.get("anchorKind") == "hrSurrogate":
        reanchor = await anyio.to_thread.run_sync(
            lambda: routing.reanchor_inputs(site_anchor, reach_length_ft))
        d.warnings.extend(reanchor.pop("_warnings", []) or [])

    ctx_inputs = {
        "lat": d.snapped_lat or lat, "lon": d.snapped_lon or lon, "comid": d.comid,
        "huc8": d.huc8, "watershed_geojson": d.watershed_geojson,
        "reach_geojson": d.reach_geojson, "drainage_area_sqkm": d.drainage_area_sqkm,
        "slope": d.slope, "fcode": d.fcode, "stream_order": d.stream_order,
        "sinuosity": d.sinuosity, "siteAnchor": site_anchor,
        "watershedPolicy": watershed_engine,
    }
    out_reach = d.reach_geojson
    out_reach_len = d.reach_length_ft
    if reanchor:
        for k in ("lat", "lon", "slope", "sinuosity", "fcode", "stream_order",
                  "drainage_area_sqkm", "huc8", "reach_geojson"):
            if k in reanchor:
                ctx_inputs[k] = reanchor[k]
        out_reach = reanchor.get("reach_geojson")
        out_reach_len = reanchor.get("reach_length_ft")
    return {
        "status": "ok",
        "siteAnchor": site_anchor,
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
            "reach_length_ft": out_reach_len,
            "warnings": d.warnings,
        },
        "watershed_geojson": d.watershed_geojson,
        "reach_geojson": out_reach,
        "ctx_inputs": ctx_inputs,
    }


def _ctx_from_inputs(ci: dict) -> AnalysisContext:
    ctx = AnalysisContext(
        lat=ci["lat"], lon=ci["lon"], comid=ci["comid"], huc8=ci.get("huc8"),
        watershed_geojson=ci.get("watershed_geojson"),
        reach_geojson=ci.get("reach_geojson"),
        drainage_area_sqkm=ci.get("drainage_area_sqkm"), slope=ci.get("slope"),
        fcode=ci.get("fcode"), stream_order=ci.get("stream_order"),
        sinuosity=ci.get("sinuosity"))
    if ci.get("siteAnchor"):
        ctx.extras["siteAnchor"] = ci["siteAnchor"]
    if ci.get("watershedEngine"):
        ctx.extras["watershedEngine"] = ci["watershedEngine"]
    return ctx


async def assess_only(ctx_inputs: dict,
                      metric_ids: Optional[list[str]] = None,
                      sources: Optional[dict[str, str]] = None,
                      overrides: Optional[dict[str, str]] = None,
                      prefetch: bool = True,
                      progress: Optional[dict] = None) -> dict:
    """Run the selected metric adapters on a prior delineation.

    ``prefetch`` (default True) has the multi-source metrics compute every source
    variant up front (no extra network) so the single-site UI can swap sources
    instantly; the batch path passes ``prefetch=False`` (its report is read-only).
    ``progress`` (a shared ``{"done","total"}`` dict) is updated as adapters
    finish so the UI can show live "X/N metrics computed" feedback.
    """
    ctx = _ctx_from_inputs(ctx_inputs)
    report = await assessment.assess(ctx, metric_ids=metric_ids, sources=sources,
                                     overrides=overrides, prefetch=prefetch,
                                     progress=progress)
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
