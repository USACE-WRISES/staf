"""Census TIGERweb roads — road features near a reach (road-crossing proxy).

Counts TIGER road features intersecting a small bbox around the reach point — a
light proxy for road-density / road-stream-crossing pressure, used as a fallback
when StreamCat ``rddens`` is unavailable for the COMID. Best-effort; returns None
on failure. Uses ``returnCountOnly`` so the response is tiny.
"""
from __future__ import annotations

from typing import Optional

import requests

# TIGERweb Transportation MapServer, "Roads" layer (all roads).
_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/2/query"


def roads_near(lat: float, lon: float, deg: float = 0.02,
               timeout: float = 12.0) -> Optional[int]:
    """Count TIGER road features within a bbox of the point, or None on failure."""
    env = f"{lon-deg:.5f},{lat-deg:.5f},{lon+deg:.5f},{lat+deg:.5f}"
    params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
              "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json"}
    try:
        r = requests.get(_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    n = data.get("count")
    if n is None:  # some servers return {"features": [...]} even with countOnly
        feats = data.get("features")
        n = len(feats) if isinstance(feats, list) else None
    return int(n) if n is not None else None
