"""EPA ATTAINS impairment status (gispub ArcGIS MapServer, no API key).

Layer 3 = Assessment Unit Catchment Associations (NHDPlus catchments) — exact
point query. Layer 1 = Assessment Lines (stream AUs) — used for a buffered
"nearby assessed waters" query when the reach's catchment isn't associated, so
unassessed reaches still get regulatory context. Never raises — returns {} on
failure or when nothing matches.
"""
from __future__ import annotations

import requests

_BASE = ("https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/"
         "MapServer")
_FIELDS = ("assessmentunitidentifier,assessmentunitname,overallstatus,"
           "isimpaired,ircategory")


def _query(layer: int, lat: float, lon: float, buffer_m: float, timeout: float) -> dict:
    """Point (optionally buffered) ATTAINS query on a gispub layer.

    Returns {assessment_unit, assessment_name, overallstatus, isimpaired,
    ircategory} for the matched AU (an impaired one preferred when several match),
    or {} on no match / failure.
    """
    params = {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": _FIELDS,
        "returnGeometry": "false", "f": "json",
    }
    if buffer_m:
        params["distance"] = buffer_m
        params["units"] = "esriSRUnit_Meter"
    try:
        r = requests.get(f"{_BASE}/{layer}/query", params=params, timeout=timeout)
        if r.status_code != 200:
            return {}
        feats = r.json().get("features") or []
        if not feats:
            return {}
        a = feats[0].get("attributes", {})
        for f in feats:                       # prefer an impaired AU (conservative flag)
            at = f.get("attributes", {})
            if str(at.get("isimpaired", "")).upper() == "Y":
                a = at
                break
        return {
            "assessment_unit": a.get("assessmentunitidentifier"),
            "assessment_name": a.get("assessmentunitname"),
            "overallstatus": a.get("overallstatus"),
            "isimpaired": a.get("isimpaired"),
            "ircategory": a.get("ircategory"),
        }
    except Exception:  # noqa: BLE001 - resilience by design
        return {}


def impairment_at_point(lat: float, lon: float, timeout: float = 8.0) -> dict:
    """Assessment unit whose NHDPlus catchment covers the point (exact), or {}.

    Short fail-fast ``timeout``: this is the first of a sequential chain (point ->
    nearby -> modeled surrogate) in an interactive flow, so a stalled gispub service
    should drop through to the fallback quickly instead of blocking the report.
    """
    return _query(3, lat, lon, 0.0, timeout)


def impairment_near_point(lat: float, lon: float, buffer_m: float = 2000.0,
                          timeout: float = 8.0) -> dict:
    """Nearest assessed stream AU within ``buffer_m`` metres (impaired preferred), or {}.

    Keyless fallback for reaches not in an assessed catchment — buffers the point
    against the Assessment Lines layer so nearby 303(d)/305(b) waters still give
    regulatory context (watershed-scale, not reach-specific).
    """
    return _query(1, lat, lon, buffer_m, timeout)
