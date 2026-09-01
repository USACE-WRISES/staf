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

from . import assessment, delineation, routing, watershed
from .metrics.base import AnalysisContext

DEFAULT_REACH_FT = delineation.DEFAULT_REACH_FT

# ``delineation["watershed_source"]`` vocabulary.
WATERSHED_V2_BASIN = "nhdplus-v2-basin"      # the StreamCat lookup engine's basin
WATERSHED_SITE_ENGINE = "site-engine"        # the exact watershed (STAF site engine)
WATERSHED_NOT_CALCULATED = "not-calculated"  # engine failed or refused: no polygon


def _engine_progress(progress: Optional[dict]):
    """Adapter from the engine's progress events to the UI's shared dict."""
    if progress is None:
        return None

    def _cb(event: dict) -> None:
        progress["stage"] = event.get("stage")
        progress["reaches"] = event.get("reaches")
        progress["hops"] = event.get("hops")
        progress["family"] = event.get("family")
    return _cb


def _engine_summary(block: dict) -> dict:
    """The compact engine block for the delineation result, exports and the
    UI (no record, no polygon)."""
    return {k: block.get(k) for k in
            ("engine", "engineVersion", "status", "reason", "nReaches",
             "nHops", "areaSqkm", "vaaAreaSqkm", "areaAgreement")}


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
                                               snap_tol_ft=snap_tolerance_ft,
                                               policy=watershed_engine))
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
    routed = site_anchor.get("anchorKind") == "hrSurrogate"
    if routed:
        reanchor = await anyio.to_thread.run_sync(
            lambda: routing.reanchor_inputs(site_anchor, reach_length_ft))
        d.warnings.extend(reanchor.pop("_warnings", []) or [])

    # The exact watershed (STAF site engine) for a routed site under the auto
    # policy. The legacy policy never runs the engine: every metric rides the
    # surrogate's NLDI basin, exactly as before.
    engine_block: Optional[dict] = None
    if routed and watershed_engine == routing.POLICY_AUTO:
        cb = _engine_progress(progress)
        engine_block = await anyio.to_thread.run_sync(
            lambda: watershed.compute_exact_watershed(site_anchor, progress=cb))

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

    out_watershed = d.watershed_geojson
    watershed_area = round(d.watershed_area_sqkm, 2) if d.watershed_area_sqkm else None
    watershed_source = WATERSHED_V2_BASIN
    delineation_da = d.drainage_area_sqkm
    gnis_name = d.gnis_name or "(unnamed reach)"
    engine_summary = None
    if engine_block is not None:
        polygon = engine_block.pop("polygon", None)
        engine_summary = _engine_summary(engine_block)
        ctx_inputs["watershedEngine"] = engine_block
        clicked = site_anchor.get("clickedStream") or {}
        gnis_name = clicked.get("gnisName") or "(unnamed stream)"
        delineation_da = ctx_inputs.get("drainage_area_sqkm")
        if engine_block.get("status") == "ok":
            ctx_inputs["watershed_geojson"] = polygon
            out_watershed = polygon
            watershed_area = engine_block.get("areaSqkm")
            watershed_source = WATERSHED_SITE_ENGINE
        else:
            # No silent proxy: the surrogate's basin is neither drawn nor scored.
            ctx_inputs["watershed_geojson"] = None
            out_watershed = None
            watershed_area = None
            watershed_source = WATERSHED_NOT_CALCULATED
            d.warnings.append(watershed.GUIDANCE_UNAVAILABLE.format(
                reason=engine_block.get("reason") or engine_block.get("status")))
    return {
        "status": "ok",
        "siteAnchor": site_anchor,
        "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_length_ft},
        "delineation": {
            "comid": d.comid,
            "gnis_name": gnis_name,
            "huc8": d.huc8,
            "huc12": None,  # filled in after assess (the HUC12 pull lives there)
            "drainage_area_sqkm": delineation_da,
            "snapped_lat": d.snapped_lat,
            "snapped_lon": d.snapped_lon,
            "watershed_area_sqkm": watershed_area,
            "watershed_source": watershed_source,
            "watershed_engine": engine_summary,
            "reach_length_ft": out_reach_len,
            "warnings": d.warnings,
        },
        "watershed_geojson": out_watershed,
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
