"""Deterministic anchoring policy: any CONUS point -> the reach EASI scores.

EASI's scoring network is NHDPlus V2 (the StreamCat universe). The map shows
the full NHDPlus HR network, so a click can land on a stream the scoring
network does not carry. This module decides, deterministically, what gets
scored:

  * ``v2Direct`` — the click resolved to a V2 flowline within the published
    snap tolerance. Everything behaves exactly as it always has.
  * ``hrSurrogate`` — the click sits on an HR-only stream. The point is routed
    to the nearest covered downstream V2 reach with an NLDI hydrolocation
    raindrop trace, and the substitution is labeled (surrogate reach, routed
    distance, drainage-area ratio) in the UI, the report, and every export.
  * refusal — when the surrogate drains more than ``DA_RATIO_MAX`` times the
    clicked stream's area, EASI declines to score. A documented refusal is
    more defensible than a deterministic wrong answer.

The policy is fixed by the framework: no user-facing fork ever chooses between
methods, so the same click produces the same result for every user. Routing
deliberately uses ONLY the hydrolocation raindrop endpoint — the
``feature_byloc`` nearest-position service answers a different question
("which line is closest"), and falling back to it would make the routed reach
depend on which service happened to be up. An outage therefore surfaces as a
retryable error, never as a different answer.

Sync functions, no Shiny imports; callers run them on worker threads. The
anchor payload contract (camelCase, ``anchorSchemaVersion`` 1) is documented
in the coverage plan and asserted by ``tests/test_routing.py``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import time

from typing import Any, Optional

from . import delineation
from .datasources import nhd_hr

ANCHOR_SCHEMA_VERSION = 1

# Published policy constants. DA_RATIO_MAX is provisional until the derivation
# study (scripts/derive_da_ratio_threshold.py) lands its empirical value; it is
# stamped into every anchor payload so a later change is visible in provenance.
DA_RATIO_MAX = 10.0
HR_SNAP_TOL_FT = 150.0
ROUTING_METHOD = "nldi-hydrolocation-raindrop"

# Watershed engine policy for streams outside the StreamCat lookup network.
#   auto             the STAF site engine computes the exact watershed for the
#                    eight watershed metrics; only COMID-keyed evidence rides
#                    the labeled nearest covered reach, within the DA-ratio
#                    bound, and the assessment completes either way.
#   streamcat-legacy the pre-2026-09 behavior: every metric rides the nearest
#                    covered reach and the site is refused past the bound.
#                    StreamCurves pins its reference screen to this value so
#                    published screening replays stay byte-identical.
POLICY_AUTO = "auto"
POLICY_STREAMCAT_LEGACY = "streamcat-legacy"
WATERSHED_ENGINE_POLICIES = (POLICY_AUTO, POLICY_STREAMCAT_LEGACY)

# Half-box (degrees) for the click-time HR probe around an off-network point;
# mirrors the app's click-refetch box.
HR_PROBE_HALF_DEG = 0.012


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
# The NLDI flowtrace process runs the same raindrop algorithm as
# linked-data/hydrolocation on a separate route; it is the fallback when the
# hydrolocation route fails. The catchment lookup at comid/position is NOT a
# fallback here: its answer can differ (see the module docstring).
FLOWTRACE_URL = ("https://api.water.usgs.gov/nldi/pygeoapi/processes/"
                 "nldi-flowtrace/execution")
_HYDROLOCATION_RETRY_S = 1.5


def _parse_flowtrace(data) -> dict:
    """The ``_hydrolocation_snap`` shape from a flowtrace answer: the Point
    feature that carries a COMID is the intersection with the network. No
    features is a clean no-stream answer; features without such a point are
    an unexpected shape, reported as an error rather than read as a stream."""
    feats = (data or {}).get("features") if isinstance(data, dict) else None
    if not feats:
        return {}
    for f in feats:
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}
        raw = props.get("comid", props.get("identifier"))
        if geom.get("type") != "Point" or raw is None:
            continue
        try:
            comid = int(raw)
        except (TypeError, ValueError):
            continue
        out: dict[str, Any] = {"comid": comid}
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            try:
                out["snap_lon"], out["snap_lat"] = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                pass
        return out
    return {"error": "flowtrace: unexpected response shape"}


def _flowtrace_snap(lat: float, lon: float, *, timeout: float = 60.0) -> dict:
    """One flowtrace execution for a point; the ``_hydrolocation_snap`` shape."""
    import requests  # noqa: PLC0415

    body = {"inputs": [{"id": "lat", "value": f"{lat:.6f}", "type": "text/plain"},
                       {"id": "lon", "value": f"{lon:.6f}", "type": "text/plain"},
                       {"id": "direction", "value": "none", "type": "text/plain"}]}
    try:
        r = requests.post(FLOWTRACE_URL, params={"f": "json"}, json=body, timeout=timeout)
        if r.status_code != 200:
            return {"error": f"flowtrace: HTTP {r.status_code}"}
        return _parse_flowtrace(r.json())
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return {"error": f"flowtrace: {exc}"}


def _hydrolocation_snap(lat: float, lon: float) -> dict:
    """NLDI hydrolocation raindrop for one point.

    Returns ``{"comid", "snap_lat", "snap_lon"}`` (snap coords may be absent),
    ``{}`` for a clean no-stream answer, or ``{"error": ...}`` when the service
    failed. The hydrolocation route gets one retry after a short pause, then
    the flowtrace process (the same algorithm on a separate route) answers;
    the error names every attempt. Raises only ImportError (a missing
    geospatial dependency is a deployment fault the pipeline classifies
    separately, not an outage).
    """
    from pynhd import NLDI  # noqa: PLC0415 - ImportError must propagate

    from .batch import diagnostics

    errors: list[str] = []
    frame = None
    for attempt in range(2):
        try:
            frame = NLDI().comid_byloc((lon, lat))
            break
        except Exception as exc:  # pragma: no cover - network guard
            diagnostics.record_exception("nldi_snap[hydrolocation]", exc)
            errors.append(f"hydrolocation: {exc}")
            if attempt == 0:
                time.sleep(_HYDROLOCATION_RETRY_S)
    if frame is None:
        fallback = _flowtrace_snap(lat, lon)
        if "error" not in fallback:
            return fallback
        errors.append(fallback["error"])
        return {"error": "; ".join(errors)}
    comid = delineation._comid_from_frame(frame)
    if comid is None:
        return {}
    out: dict[str, Any] = {"comid": comid}
    try:
        geom = frame.iloc[0].geometry
        if geom is not None and geom.geom_type == "Point":
            out["snap_lon"], out["snap_lat"] = float(geom.x), float(geom.y)
    except Exception:  # noqa: BLE001 - snap coords are best-effort
        pass
    return out


def _distance_ft(lat1: float, lon1: float, lat2: float, lon2: float
                 ) -> Optional[float]:
    """Straight-line distance in feet (EPSG:5070), or None. Never raises."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        pts = gpd.GeoSeries([Point(lon1, lat1), Point(lon2, lat2)],
                            crs=delineation.CRS_WGS84).to_crs(delineation.CRS_ALBERS)
        return round(float(pts.iloc[0].distance(pts.iloc[1]))
                     * delineation.FT_PER_M, 1)
    except Exception:  # noqa: BLE001
        return None


def _payload(kind: str, clicked_lat: float, clicked_lon: float, *,
             clicked_stream: Optional[dict] = None,
             scored_reach: Optional[dict] = None,
             routing: Optional[dict] = None,
             notes: Optional[list[str]] = None) -> dict:
    p: dict[str, Any] = {
        "anchorSchemaVersion": ANCHOR_SCHEMA_VERSION,
        "anchorKind": kind,
        "clickedPoint": {"lat": round(float(clicked_lat), 6),
                         "lon": round(float(clicked_lon), 6)},
        "scoredReach": scored_reach or {},
        "notes": notes or [],
    }
    if clicked_stream is not None:
        p["clickedStream"] = clicked_stream
    if routing is not None:
        p["routing"] = routing
    return p


def v2_anchor(comid: int, clicked_lat: float, clicked_lon: float,
              snap_lat: Optional[float] = None, snap_lon: Optional[float] = None,
              snap_dist_ft: Optional[float] = None) -> dict:
    """The covered-network anchor payload (the click was on a V2 flowline).

    ``gnisName``/``drainageAreaSqkm`` are filled in by the pipeline after
    delineation runs (it fetches the flowline attributes anyway).
    """
    scored = {"network": "nhdplus-v2", "comid": int(comid),
              "gnisName": None, "drainageAreaSqkm": None,
              "snapLat": snap_lat, "snapLon": snap_lon,
              "snapDistFt": snap_dist_ft}
    return _payload("v2Direct", clicked_lat, clicked_lon, scored_reach=scored)


# --------------------------------------------------------------------------- #
# the policy
# --------------------------------------------------------------------------- #
def route_from_hr(clicked_lat: float, clicked_lon: float,
                  hr_snap: tuple[float, float, float, Optional[int]], *,
                  policy: str = POLICY_AUTO) -> dict:
    """Route an HR-only stream click to the covered network.

    ``hr_snap`` is ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` from
    ``nhd_hr.nearest_point_on_hr_lines``. Returns one of:

      * ``{"anchor": payload}`` — routed; ready to delineate. Under the
        ``auto`` policy this is the answer even past the DA-ratio bound: the
        payload's ``routing`` then carries ``declined``, ``declineCode`` and
        ``declineMessage`` and the assessment withholds COMID-keyed evidence.
      * ``{"refused": True, "code", "message", "anchor"}`` — the
        ``streamcat-legacy`` policy's refusal past the bound
      * ``{"error": "snap_service_error", "detail"}`` — retryable outage
      * ``{"error": "no_stream_found"}`` — the raindrop found no V2 reach

    Deterministic: the raindrop starts at the HR snap point (a point on the
    channel), a fixed endpoint order, and fixed published constants.
    """
    hr_lat, hr_lon, hr_dist_ft, nhdplusid = hr_snap
    if nhdplusid is None:
        return {"error": "no_stream_found"}

    # The HR reach attributes and the NLDI raindrop do not depend on each
    # other, so they run side by side (one round trip instead of two on the
    # click path); the fabric attributes need the raindrop's COMID and follow.
    with ThreadPoolExecutor(max_workers=2) as pool:
        rec_future = pool.submit(nhd_hr.hr_flowline_by_id, nhdplusid)
        snap_future = pool.submit(_hydrolocation_snap, hr_lat, hr_lon)
        rec = rec_future.result()
        snap = snap_future.result()
    clicked_stream: dict[str, Any] = {
        "network": "nhdplus-hr", "nhdplusId": int(nhdplusid),
        "gnisName": None, "reachcode": None, "drainageAreaSqkm": None,
        "slope": None, "fcode": None, "streamOrder": None, "vpuid": None,
        "snapLat": hr_lat, "snapLon": hr_lon,
        "snapDistFt": round(hr_dist_ft, 1) if hr_dist_ft is not None else None,
    }
    if rec is not None:
        clicked_stream.update({
            "gnisName": rec["gnis_name"], "reachcode": rec["reachcode"],
            "drainageAreaSqkm": rec["totdasqkm"], "slope": rec["slope"],
            "fcode": rec["fcode"], "streamOrder": rec["stream_order"],
            "vpuid": rec["vpuid"],
        })

    if snap.get("error"):
        return {"error": "snap_service_error", "detail": snap["error"]}
    comid = snap.get("comid")
    if comid is None:
        return {"error": "no_stream_found"}

    attrs = delineation.flowline_attrs(comid)
    scored = {
        "network": "nhdplus-v2", "comid": int(comid),
        "gnisName": attrs.get("gnis_name"),
        "drainageAreaSqkm": attrs.get("drainage_area_sqkm"),
        "snapLat": snap.get("snap_lat"), "snapLon": snap.get("snap_lon"),
        "snapDistFt": None,
    }
    routed_ft = None
    if snap.get("snap_lat") is not None:
        routed_ft = _distance_ft(hr_lat, hr_lon,
                                 snap["snap_lat"], snap["snap_lon"])

    clicked_da = clicked_stream["drainageAreaSqkm"]
    surrogate_da = scored["drainageAreaSqkm"]
    da_ratio = None
    if clicked_da and surrogate_da:
        da_ratio = round(surrogate_da / clicked_da, 2)

    routing = {"method": ROUTING_METHOD, "routedDistanceFt": routed_ft,
               "daRatio": da_ratio, "daRatioLimit": DA_RATIO_MAX,
               "declined": False}
    legacy = policy == POLICY_STREAMCAT_LEGACY
    notes = ([("Scored at the nearest downstream reach of the covered network.")]
             if legacy else
             [("COMID-keyed evidence describes the nearest downstream reach of "
               "the covered network.")])
    anchor = _payload("hrSurrogate", clicked_lat, clicked_lon,
                      clicked_stream=clicked_stream, scored_reach=scored,
                      routing=routing, notes=notes)
    limit = int(DA_RATIO_MAX) if float(DA_RATIO_MAX).is_integer() else DA_RATIO_MAX

    # Never guess past the bound. Missing drainage area on either side means
    # the ratio bound cannot be checked. Under the legacy policy the site is
    # refused with the reason; under auto the routing is declined and only
    # the COMID-keyed evidence is withheld (the exact watershed still comes
    # from the site engine).
    if da_ratio is None:
        routing["declined"] = True
        routing["declineCode"] = "surrogate_da_unavailable"
        routing["declineMessage"] = (
            "Drainage area is unavailable for the clicked stream or the "
            "nearest covered reach, so the substitution limit cannot be "
            "checked. Reach-keyed evidence (low flow, substrate, biological "
            "integrity) is unavailable here.")
        if legacy:
            return {"refused": True, "code": "surrogate_da_unavailable",
                    "message": ("EASI can't check this stream against its "
                                "substitution limit. Drainage area is unavailable "
                                "for the clicked stream or the nearest covered "
                                "reach. Choose a nearby larger stream."),
                    "anchor": anchor}
        return {"anchor": anchor}
    if da_ratio > DA_RATIO_MAX:
        routing["declined"] = True
        routing["declineCode"] = "surrogate_da_ratio_exceeded"
        routing["declineMessage"] = (
            f"The nearest covered reach drains {da_ratio} times the area of "
            f"the clicked stream (limit {limit}). Reach-keyed evidence (low "
            "flow, substrate, biological integrity) is unavailable here.")
        if legacy:
            return {"refused": True, "code": "surrogate_da_ratio_exceeded",
                    "message": (f"EASI can't score this stream. The nearest stream "
                                f"in the scoring network drains {da_ratio} times "
                                f"the area of the stream you clicked (limit "
                                f"{limit}). Choose a larger stream, or use SFARI "
                                f"or DEEP for this site."),
                    "anchor": anchor}
        return {"anchor": anchor}
    return {"anchor": anchor}


def reanchor_inputs(anchor: Optional[dict], reach_length_ft: float) -> dict:
    """Phase 2 re-anchoring: ctx overrides that source reach-scale inputs from
    the true clicked HR stream while watershed metrics stay on the surrogate.

    Returns ``{}`` unless the anchor is an HR surrogate. On success returns the
    ctx overrides (``lat``/``lon`` at the clicked point; ``slope``,
    ``sinuosity``, ``fcode``, ``stream_order``, ``drainage_area_sqkm``,
    ``huc8`` from the HR reach; ``reach_geojson``/``reach_length_ft`` trimmed
    on the HR mainstem) plus ``_warnings``. A field the HR network cannot
    supply is set to None — the clicked stream's value is unknown, not equal
    to the surrogate's — and the per-metric machinery degrades honestly.
    Stamps ``anchor["reanchored"] = {"applied", "warnings"}`` so labels can
    tell whether re-anchoring actually happened. Never raises.
    """
    if (anchor or {}).get("anchorKind") != "hrSurrogate":
        return {}
    clicked = anchor.get("clickedStream") or {}
    nid = clicked.get("nhdplusId")
    state = anchor.setdefault("reanchored", {"applied": False, "warnings": []})
    try:
        if not nid or clicked.get("snapLat") is None:
            state["warnings"].append("clicked HR reach id or snap point missing")
            return {}
        attrs = nhd_hr.hr_attrs(int(nid))
        if attrs.get("_hr_error"):
            state["warnings"].append(str(attrs["_hr_error"]))
            return {}
        out: dict[str, Any] = {
            "lat": clicked["snapLat"], "lon": clicked["snapLon"],
            "slope": attrs.get("slope"), "sinuosity": attrs.get("sinuosity"),
            "fcode": attrs.get("fcode"), "stream_order": attrs.get("stream_order"),
            "drainage_area_sqkm": attrs.get("drainage_area_sqkm"),
        }
        if attrs.get("huc8"):
            out["huc8"] = attrs["huc8"]
        reach, actual_ft, warns = nhd_hr.derive_reach_hr(
            int(nid), clicked["snapLat"], clicked["snapLon"], reach_length_ft)
        out["reach_geojson"] = reach
        out["reach_length_ft"] = actual_ft
        out["_warnings"] = list(warns or [])
        state["applied"] = True
        state["warnings"] = list(warns or [])
        return out
    except Exception as exc:  # noqa: BLE001 - degrade to Phase 1 behavior
        state["warnings"].append(f"re-anchoring failed: {exc}")
        return {}


def resolve_anchor(lat: float, lon: float, *,
                   snap_tol_ft: float = HR_SNAP_TOL_FT,
                   policy: str = POLICY_AUTO) -> dict:
    """The engine path for a bare coordinate (batch sites, typed coordinates).

    Fixed order, deterministic by construction:
      1. NLDI hydrolocation. A snap within ``snap_tol_ft`` of the input (or a
         comid with no snap geometry to measure) is the covered network —
         numerically identical to the historical behavior for on-network
         points (same endpoint, same comid, same snapped coordinates).
      2. Otherwise, an HR flowline within ``snap_tol_ft`` identifies which
         stream the point is on, and that stream routes via ``route_from_hr``
         (the DA-ratio policy decides; a same-stream match rides through with
         a ratio near 1).
      3. Otherwise, a raindrop comid with NO mapped line near the point is the
         wide-river / imprecise-coordinate case: no different stream is
         identifiable, so the reach the point drains to is accepted exactly as
         it always was, with a transparency note in the anchor.
      4. Otherwise there is no stream here: ``no_stream_found``.

    Step 3 preserves the historical batch behavior for legitimate mainstem
    points whose coordinates sit farther than ``snap_tol_ft`` from the mapped
    centerline (wide rivers): the pre-routing engine accepted every raindrop
    resolution without a distance test, and refusing those points would turn
    working sites into failures. Returns the same shapes as ``route_from_hr``.
    """
    snap = _hydrolocation_snap(lat, lon)
    if snap.get("error"):
        return {"error": "snap_service_error", "detail": snap["error"]}
    comid = snap.get("comid")
    dist = None
    if comid is not None:
        if snap.get("snap_lat") is not None:
            dist = _distance_ft(lat, lon, snap["snap_lat"], snap["snap_lon"])
        if dist is None or dist <= snap_tol_ft:
            return {"anchor": v2_anchor(
                comid, lat, lon, snap.get("snap_lat"), snap.get("snap_lon"),
                dist)}

    hr_fc = nhd_hr.hr_flowlines_in_bbox(
        lon - HR_PROBE_HALF_DEG, lat - HR_PROBE_HALF_DEG,
        lon + HR_PROBE_HALF_DEG, lat + HR_PROBE_HALF_DEG)
    hr_snap = nhd_hr.nearest_point_on_hr_lines(hr_fc, lat, lon)
    if hr_snap is not None and hr_snap[2] <= snap_tol_ft:
        return route_from_hr(lat, lon, hr_snap, policy=policy)
    if comid is not None:
        anchor = v2_anchor(comid, lat, lon, snap.get("snap_lat"),
                           snap.get("snap_lon"), dist)
        anchor["notes"].append(
            "Coordinates were not within the snap tolerance of a mapped "
            "stream line. The point was resolved to the reach it drains to.")
        return {"anchor": anchor}
    return {"error": "no_stream_found"}
