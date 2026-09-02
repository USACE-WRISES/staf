"""Shared site anchoring: any CONUS point -> the reach a consumer scores.

The consuming apps show the full NHDPlus HR network, so a click can land on a
stream the NHDPlus V2 network (the StreamCat lookup engine's universe) does
not carry. This module classifies a point deterministically, the same way
EASI's ``easi/routing.py`` does (a parity test guards that when the EASI
source tree is present):

  * ``v2Direct``: the point resolved to a V2 flowline within the published
    snap tolerance. COMID-keyed evidence describes the clicked stream.
  * ``hrSurrogate``: the point sits on an HR-only stream. The nearest covered
    downstream V2 reach is found with an NLDI hydrolocation raindrop trace and
    recorded with the routed distance and the drainage-area ratio. The exact
    watershed still comes from the engine; only COMID-keyed evidence rides
    the covered reach, labeled (``naming.anchor_label``).
  * Past ``DA_RATIO_MAX`` (or with a drainage area unknown on either side) the
    routing is ``declined`` with a code and a plain message. Unlike EASI's
    policy this module never refuses a site: the consumer decides what a
    declined routing withholds.

Routing deliberately uses ONLY the hydrolocation raindrop algorithm, on its
own route or the flowtrace process route (never the
nearest-position service, which answers a different question), so an outage
surfaces as a retryable error, never as a different answer. Pure ``requests``
(no pynhd): the request is formatted exactly as pynhd formats it so both
paths get the same answer. Never raises.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from . import hr
from .geometry import CRS_ALBERS, CRS_WGS84, FT_PER_M, nearest_point_on_records

ANCHOR_SCHEMA_VERSION = 1
DA_RATIO_MAX = 10.0
HR_SNAP_TOL_FT = 150.0
HR_PROBE_HALF_DEG = 0.012
ROUTING_METHOD = "nldi-hydrolocation-raindrop"
DECLINE_DA_UNAVAILABLE = "surrogate_da_unavailable"
DECLINE_DA_RATIO = "surrogate_da_ratio_exceeded"

NLDI_HYDROLOCATION_URL = "https://api.water.usgs.gov/nldi/linked-data/hydrolocation"
# The flowtrace process runs the same raindrop algorithm on a separate route:
# the fallback when the hydrolocation route fails. The catchment position
# lookup is never a fallback here (its answer can differ from the raindrop's).
NLDI_FLOWTRACE_URL = ("https://api.water.usgs.gov/nldi/pygeoapi/processes/"
                      "nldi-flowtrace/execution")
_HYDROLOCATION_RETRY_S = 1.5
# NHDPlus V2 attributes from the USGS fabric API (OGC API Features), the
# successor of the WaterData WFS EASI's delineation also reads.
V2_ITEMS_URL = ("https://api.water.usgs.gov/fabric/pygeoapi/collections/"
                "nhdflowline_network/items")
_V2_FIELDS = "comid,gnis_name,totdasqkm,reachcode,slope,fcode,streamorde"
_OFF_LINE_NOTE = ("Coordinates were not within the snap tolerance of a mapped "
                  "stream line. The point was resolved to the reach it drains to.")
_ROUTED_NOTE = ("COMID-keyed evidence describes the nearest downstream reach "
                "of the covered network.")


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _get_json(url: str, params: dict, timeout: float, retries: int = 1
              ) -> tuple[Optional[dict], Optional[str]]:
    """``(payload, None)`` on a 200 JSON answer, else ``(None, reason)``."""
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json(), None
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - resilience by design
            last = repr(exc)
        time.sleep(0.5 * (attempt + 1))
    return None, last or "request failed"


def _int(value: Any) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _post_json(url: str, params: dict, body: dict, timeout: float
               ) -> tuple[Optional[dict], Optional[str]]:
    """``(payload, None)`` on a 200 JSON answer to a POST, else ``(None, reason)``."""
    try:
        r = requests.post(url, params=params, json=body, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        data = r.json()
        return (data, None) if isinstance(data, dict) else (None, "not a JSON object")
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return None, str(exc)


def parse_flowtrace(data) -> dict:
    """The ``hydrolocation_snap`` shape from a flowtrace answer: the Point
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
        comid = _int(raw)
        if comid is None:
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


def flowtrace_snap(lat: float, lon: float, *, timeout: float = 60.0) -> dict:
    """One flowtrace execution for a point; the ``hydrolocation_snap`` shape."""
    body = {"inputs": [{"id": "lat", "value": f"{lat:.6f}", "type": "text/plain"},
                       {"id": "lon", "value": f"{lon:.6f}", "type": "text/plain"},
                       {"id": "direction", "value": "none", "type": "text/plain"}]}
    data, err = _post_json(NLDI_FLOWTRACE_URL, {"f": "json"}, body, timeout)
    if err:
        return {"error": f"flowtrace: {err}"}
    return parse_flowtrace(data)


def hydrolocation_snap(lat: float, lon: float) -> dict:
    """NLDI hydrolocation raindrop for one point.

    Returns ``{"comid", "snap_lat", "snap_lon"}`` (snap coords may be absent),
    ``{}`` for a clean no-stream answer, or ``{"error": ...}`` when the
    service failed. Keeps only ``source == "indexed"`` features, as pynhd's
    ``comid_byloc`` does. The hydrolocation route gets one retry after a
    short pause, then the flowtrace process answers; the error names every
    attempt.
    """
    params = {"coords": f"POINT({lon:.6f} {lat:.6f})"}
    data, err = _get_json(NLDI_HYDROLOCATION_URL, params, timeout=30.0, retries=1)
    if err:
        time.sleep(_HYDROLOCATION_RETRY_S)
        data, err2 = _get_json(NLDI_HYDROLOCATION_URL, params, timeout=30.0, retries=0)
        if err2:
            fallback = flowtrace_snap(lat, lon)
            if "error" not in fallback:
                return fallback
            return {"error": f"hydrolocation: {err}; hydrolocation: {err2}; "
                             f"{fallback['error']}"}
    feats = [f for f in (data or {}).get("features") or []
             if (f.get("properties") or {}).get("source") == "indexed"]
    if not feats:
        return {}
    props = feats[0].get("properties") or {}
    comid = _int(props.get("comid"))
    if comid is None:
        comid = _int(props.get("identifier"))
    if comid is None:
        return {}
    out: dict[str, Any] = {"comid": comid}
    geom = feats[0].get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None
    if coords and len(coords) >= 2:
        try:
            out["snap_lon"], out["snap_lat"] = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            pass
    return out


def v2_flowline_attrs(comid: int) -> dict:
    """NHDPlus V2 attributes for a COMID from the same fabric API collection
    EASI's delineation reads (``nhdflowline_network``). Returns the
    ``delineation.flowline_attrs`` keys, or ``{"error": ...}`` when the
    service failed (no alternate source: the ratio must come from the same
    numbers EASI uses). Never raises."""
    params = {"comid": int(comid), "limit": 1, "properties": _V2_FIELDS, "f": "json"}
    data, err = _get_json(V2_ITEMS_URL, params, timeout=60.0, retries=2)
    if err:
        data, err = _get_json(V2_ITEMS_URL, params, timeout=120.0, retries=1)
    if err:
        return {"error": f"attrs: {err}"}
    out: dict[str, Any] = {"gnis_name": None, "drainage_area_sqkm": None,
                           "huc8": None, "slope": None, "fcode": None,
                           "stream_order": None}
    feats = (data or {}).get("features") or []
    if not feats:
        return out
    props = feats[0].get("properties") or {}
    name = props.get("gnis_name")
    out["gnis_name"] = (str(name).strip() or None) if name else None
    try:
        da = props.get("totdasqkm")
        out["drainage_area_sqkm"] = float(da) if da is not None else None
    except (TypeError, ValueError):
        pass
    if props.get("reachcode"):
        out["huc8"] = str(props["reachcode"])[:8]
    try:
        s = props.get("slope")
        if s is not None:
            s = float(s)
            out["slope"] = s if s >= 0 else None
    except (TypeError, ValueError):
        pass
    out["fcode"] = _int(props.get("fcode"))
    out["stream_order"] = _int(props.get("streamorde"))
    return out


def hr_snap(lat: float, lon: float, *, half_deg: float = HR_PROBE_HALF_DEG
            ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Nearest HR flowline to the point: ``(snap_lat, snap_lon, dist_ft,
    nhdplusid)`` or None (same math as EASI's HR snap)."""
    records = hr.flowlines_in_bbox(lon - half_deg, lat - half_deg,
                                   lon + half_deg, lat + half_deg)
    return nearest_point_on_records(records, lat, lon)


def distance_ft(lat1: float, lon1: float, lat2: float, lon2: float
                ) -> Optional[float]:
    """Straight-line distance in feet (EPSG:5070), or None. Never raises."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        pts = gpd.GeoSeries([Point(lon1, lat1), Point(lon2, lat2)],
                            crs=CRS_WGS84).to_crs(CRS_ALBERS)
        return round(float(pts.iloc[0].distance(pts.iloc[1])) * FT_PER_M, 1)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# payloads
# --------------------------------------------------------------------------- #
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
    """The covered-network anchor (the point is on a V2 flowline). Name and
    drainage area are filled by the consumer's delineation, which reads the
    flowline attributes anyway."""
    scored = {"network": "nhdplus-v2", "comid": int(comid),
              "gnisName": None, "drainageAreaSqkm": None,
              "snapLat": snap_lat, "snapLon": snap_lon,
              "snapDistFt": snap_dist_ft}
    return _payload("v2Direct", clicked_lat, clicked_lon, scored_reach=scored)


def route_from_hr(clicked_lat: float, clicked_lon: float,
                  hr_hit: tuple[float, float, float, Optional[int]], *,
                  da_ratio_max: float = DA_RATIO_MAX) -> dict:
    """Anchor an HR-only stream click on the covered network.

    ``hr_hit`` is ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` from
    ``hr_snap``. Returns ``{"anchor": payload}`` (declined routing is recorded
    on ``payload["routing"]``), ``{"error": "snap_service_error", "detail"}``
    (retryable outage), or ``{"error": "no_stream_found"}``.
    """
    hr_lat, hr_lon, hr_dist_ft, nhdplusid = hr_hit
    if nhdplusid is None:
        return {"error": "no_stream_found"}
    rec = hr.flowline_by_id(nhdplusid)
    clicked_stream: dict[str, Any] = {
        "network": "nhdplus-hr", "nhdplusId": int(nhdplusid),
        "gnisName": None, "reachcode": None, "drainageAreaSqkm": None,
        "slope": None, "fcode": None, "streamOrder": None, "vpuid": None,
        "snapLat": hr_lat, "snapLon": hr_lon,
        "snapDistFt": round(hr_dist_ft, 1) if hr_dist_ft is not None else None,
    }
    if rec is not None:
        clicked_stream.update({
            "gnisName": rec.get("gnis_name"), "reachcode": rec.get("reachcode"),
            "drainageAreaSqkm": rec.get("totdasqkm"), "slope": rec.get("slope"),
            "fcode": rec.get("fcode"), "streamOrder": rec.get("stream_order"),
            "vpuid": rec.get("vpuid"),
        })

    snap = hydrolocation_snap(hr_lat, hr_lon)
    if snap.get("error"):
        return {"error": "snap_service_error", "detail": snap["error"]}
    comid = snap.get("comid")
    if comid is None:
        return {"error": "no_stream_found"}

    attrs = v2_flowline_attrs(comid)
    attrs_error = attrs.get("error")
    if attrs_error:
        attrs = {}
    scored = {
        "network": "nhdplus-v2", "comid": int(comid),
        "gnisName": attrs.get("gnis_name"),
        "drainageAreaSqkm": attrs.get("drainage_area_sqkm"),
        "snapLat": snap.get("snap_lat"), "snapLon": snap.get("snap_lon"),
        "snapDistFt": None,
    }
    routed_ft = None
    if snap.get("snap_lat") is not None:
        routed_ft = distance_ft(hr_lat, hr_lon, snap["snap_lat"], snap["snap_lon"])

    clicked_da = clicked_stream["drainageAreaSqkm"]
    surrogate_da = scored["drainageAreaSqkm"]
    da_ratio = None
    if clicked_da and surrogate_da:
        da_ratio = round(surrogate_da / clicked_da, 2)

    routing: dict[str, Any] = {
        "method": ROUTING_METHOD, "routedDistanceFt": routed_ft,
        "daRatio": da_ratio, "daRatioLimit": da_ratio_max, "declined": False}
    if attrs_error:
        routing["attrsError"] = attrs_error
    anchor = _payload("hrSurrogate", clicked_lat, clicked_lon,
                      clicked_stream=clicked_stream, scored_reach=scored,
                      routing=routing, notes=[_ROUTED_NOTE])
    limit = (int(da_ratio_max) if float(da_ratio_max).is_integer()
             else da_ratio_max)
    if da_ratio is None:
        routing["declined"] = True
        routing["declineCode"] = DECLINE_DA_UNAVAILABLE
        routing["declineMessage"] = (
            "Drainage area is unavailable for the clicked stream or the "
            "nearest covered reach, so the substitution limit cannot be "
            "checked. COMID-keyed evidence is unavailable here.")
    elif da_ratio > da_ratio_max:
        routing["declined"] = True
        routing["declineCode"] = DECLINE_DA_RATIO
        routing["declineMessage"] = (
            f"The nearest covered reach drains {da_ratio} times the area of "
            f"the clicked stream (limit {limit}). COMID-keyed evidence is "
            "unavailable here.")
    return {"anchor": anchor}


# --------------------------------------------------------------------------- #
# the policy
# --------------------------------------------------------------------------- #
def classify(lat: float, lon: float, *, snap_tol_ft: float = HR_SNAP_TOL_FT,
             da_ratio_max: float = DA_RATIO_MAX) -> dict:
    """Anchor a bare coordinate (typed coordinates, batch rows).

    Fixed order, deterministic by construction (the same four steps as
    EASI's ``routing.resolve_anchor``):
      1. NLDI hydrolocation. A snap within ``snap_tol_ft`` (or a comid with
         no snap geometry to measure) is the covered network.
      2. Otherwise an HR flowline within ``snap_tol_ft`` identifies the stream
         the point is on, and ``route_from_hr`` records the covered reach.
      3. Otherwise a raindrop comid with NO mapped line near the point is the
         wide-river or imprecise-coordinate case: the reach the point drains
         to is accepted, with a transparency note.
      4. Otherwise there is no stream here: ``no_stream_found``.
    """
    snap = hydrolocation_snap(lat, lon)
    if snap.get("error"):
        return {"error": "snap_service_error", "detail": snap["error"]}
    comid = snap.get("comid")
    dist = None
    if comid is not None:
        if snap.get("snap_lat") is not None:
            dist = distance_ft(lat, lon, snap["snap_lat"], snap["snap_lon"])
        if dist is None or dist <= snap_tol_ft:
            return {"anchor": v2_anchor(comid, lat, lon, snap.get("snap_lat"),
                                        snap.get("snap_lon"), dist)}
    hit = hr_snap(lat, lon)
    if hit is not None and hit[2] <= snap_tol_ft:
        return route_from_hr(lat, lon, hit, da_ratio_max=da_ratio_max)
    if comid is not None:
        anchor = v2_anchor(comid, lat, lon, snap.get("snap_lat"),
                           snap.get("snap_lon"), dist)
        anchor["notes"].append(_OFF_LINE_NOTE)
        return {"anchor": anchor}
    return {"error": "no_stream_found"}


def classify_click(lat: float, lon: float, *,
                   v2_hit: Optional[tuple] = None,
                   hr_hit: Optional[tuple] = None,
                   snap_tol_ft: float = HR_SNAP_TOL_FT,
                   da_ratio_max: float = DA_RATIO_MAX) -> dict:
    """Anchor a map click the app already snapped against its drawn layers.

    ``v2_hit``/``hr_hit`` are ``(snap_lat, snap_lon, dist_ft, id)`` tuples
    from the app's own nearest-line snaps (V2 first, then HR). A V2 line
    within tolerance is the covered network with no service call at all; an
    HR line within tolerance routes through ``route_from_hr``.
    """
    if v2_hit is not None and v2_hit[3] is not None and v2_hit[2] <= snap_tol_ft:
        return {"anchor": v2_anchor(v2_hit[3], lat, lon, v2_hit[0], v2_hit[1],
                                    round(float(v2_hit[2]), 1))}
    if hr_hit is not None and hr_hit[3] is not None and hr_hit[2] <= snap_tol_ft:
        return route_from_hr(lat, lon, hr_hit, da_ratio_max=da_ratio_max)
    return {"error": "no_stream_found"}
