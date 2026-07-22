"""USACE National Inventory of Dams proximity query."""
from __future__ import annotations

import math
from typing import Optional

import requests

_URL = (
    "https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
    "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def barriers_near(lat: float, lon: float, miles: float = 1.0,
                  timeout: float = 10.0) -> Optional[list[dict]]:
    """Mapped NID dams within the requested geodesic radius, or ``None`` on failure."""
    radius_m = float(miles) * 1609.344
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "distance": radius_m,
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NAME,NID_STORAGE,DAM_HEIGHT",
        "returnGeometry": "true",
        "f": "json",
    }
    try:
        response = requests.get(_URL, params=params, timeout=timeout)
        if response.status_code != 200:
            return None
        output = []
        for feature in response.json().get("features", []):
            geom = feature.get("geometry") or {}
            if geom.get("x") is None or geom.get("y") is None:
                continue
            distance = _distance_m(lat, lon, float(geom["y"]), float(geom["x"]))
            if distance > radius_m:
                continue
            attrs = feature.get("attributes") or {}
            output.append({
                "name": attrs.get("NAME"),
                "storage": attrs.get("NID_STORAGE"),
                "height": attrs.get("DAM_HEIGHT"),
                "distance_m": round(distance, 1),
            })
        return sorted(output, key=lambda x: (x["distance_m"], str(x.get("name") or "")))
    except Exception:  # noqa: BLE001 - external service degrades gracefully
        return None
