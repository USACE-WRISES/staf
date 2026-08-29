"""Census TIGERweb roads — road features near a reach (road-crossing proxy).

Counts TIGER road features intersecting a small bbox around the reach point — a
light proxy for road-density / road-stream-crossing pressure, used as a fallback
when StreamCat ``rddens`` is unavailable for the COMID. Best-effort; returns None
on failure. Uses ``returnCountOnly`` so the responses are tiny.

TIGERweb splits roads across scale-banded layers, so three feature layers are
summed: 2 (Primary Roads), 6 (Secondary Roads), 8 (Local Roads). Local roads
dominate the count almost everywhere; querying only the primary layer reads
zero in most rural areas. A failed layer fails the whole count (None), never a
silently partial sum.
"""
from __future__ import annotations

from typing import Optional

import requests

_BASE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
         "Transportation/MapServer")
# (layer id, label): primary + secondary + local covers the road network.
ROAD_LAYERS = ((2, "primary"), (6, "secondary"), (8, "local"))


def _count_layer(layer_id: int, env: str, timeout: float) -> Optional[int]:
    params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
              "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json"}
    try:
        r = requests.get(f"{_BASE}/{layer_id}/query", params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or "error" in data:
        return None
    n = data.get("count")
    if n is None:  # some servers return {"features": [...]} even with countOnly
        feats = data.get("features")
        n = len(feats) if isinstance(feats, list) else None
    return int(n) if n is not None else None


def roads_near(lat: float, lon: float, deg: float = 0.02,
               timeout: float = 12.0) -> Optional[int]:
    """Count TIGER road features (all three road layers) within a bbox of the
    point, or None when any layer's count is unavailable."""
    env = f"{lon-deg:.5f},{lat-deg:.5f},{lon+deg:.5f},{lat+deg:.5f}"
    total = 0
    for layer_id, _label in ROAD_LAYERS:
        n = _count_layer(layer_id, env, timeout)
        if n is None:
            return None
        total += n
    return total
