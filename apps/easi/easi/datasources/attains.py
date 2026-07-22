"""EPA ATTAINS assessed-water lookup with deterministic nearest-unit selection."""
from __future__ import annotations

import math
import requests

_BASE = "https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer"
_FIELDS = (
    "assessmentunitidentifier,assessmentunitname,overallstatus,isimpaired,ircategory")
_ASSESSED_LAYERS = (0, 1, 2)  # points, lines, and areas; layer 3 is catchment association


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _shape(esri_geometry: dict):
    from shapely.geometry import (LineString, MultiLineString, MultiPoint, Point,
                                  Polygon)

    if not esri_geometry:
        return None
    if "x" in esri_geometry and "y" in esri_geometry:
        return Point(esri_geometry["x"], esri_geometry["y"])
    if esri_geometry.get("points"):
        points = esri_geometry["points"]
        return MultiPoint(points) if points else None
    if esri_geometry.get("paths"):
        lines = [LineString(path) for path in esri_geometry["paths"] if len(path) >= 2]
        return lines[0] if len(lines) == 1 else MultiLineString(lines)
    if esri_geometry.get("rings"):
        rings = [ring for ring in esri_geometry["rings"] if len(ring) >= 4]
        return Polygon(rings[0], rings[1:]) if rings else None
    return None


def _distance_to_geometry_m(lat: float, lon: float, geometry: dict) -> float | None:
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    geom = _shape(geometry)
    if geom is None or geom.is_empty:
        return None
    point = Point(lon, lat)
    if geom.covers(point):
        return 0.0
    nearest = nearest_points(point, geom)[1]
    return _haversine_m(lat, lon, nearest.y, nearest.x)


def _request(layer: int, lat: float, lon: float, buffer_m: float,
             timeout: float) -> list[dict] | None:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": _FIELDS,
        "returnGeometry": "true",
        "f": "json",
    }
    if buffer_m:
        params["distance"] = float(buffer_m)
        params["units"] = "esriSRUnit_Meter"
    try:
        response = requests.get(f"{_BASE}/{layer}/query", params=params, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json().get("features") or []
    except Exception:  # noqa: BLE001 - external service degrades gracefully
        return None


def _record(feature: dict, *, distance_m: float, match_type: str,
            source_layer: int | None = None) -> dict:
    attrs = feature.get("attributes") or {}
    return {
        "assessment_unit": attrs.get("assessmentunitidentifier"),
        "assessment_name": attrs.get("assessmentunitname"),
        "overallstatus": attrs.get("overallstatus"),
        "isimpaired": attrs.get("isimpaired"),
        "ircategory": attrs.get("ircategory"),
        "distance_m": round(float(distance_m), 1),
        "match_type": match_type,
        "source_layer": source_layer,
    }


def impairment_at_point(lat: float, lon: float, timeout: float = 8.0) -> dict:
    """Actual assessed point, line, or area intersecting the selected point."""
    candidates = []
    for layer in _ASSESSED_LAYERS:
        for feature in _request(layer, lat, lon, 0.0, timeout) or []:
            candidates.append((layer, feature))
    if not candidates:
        return {}
    layer, feature = min(
        candidates,
        key=lambda item: (
            str((item[1].get("attributes") or {}).get("assessmentunitidentifier") or ""),
            item[0],
        ))
    return _record(
        feature, distance_m=0.0, match_type="intersect", source_layer=layer)


def impairment_near_point(lat: float, lon: float, buffer_m: float = 2000.0,
                          timeout: float = 8.0) -> dict:
    """Actually nearest assessed line within ``buffer_m`` metres.

    Candidate order and impairment status never influence selection.
    """
    candidates = []
    for layer in _ASSESSED_LAYERS:
        for feature in _request(layer, lat, lon, buffer_m, timeout) or []:
            distance = _distance_to_geometry_m(lat, lon, feature.get("geometry") or {})
            if distance is not None and distance <= buffer_m:
                candidates.append((distance, layer, feature))
    if not candidates:
        return {}
    distance, layer, feature = min(
        candidates,
        key=lambda item: (
            item[0],
            str((item[2].get("attributes") or {}).get("assessmentunitidentifier") or ""),
            item[1],
        ))
    return _record(
        feature, distance_m=distance, match_type="nearby", source_layer=layer)
