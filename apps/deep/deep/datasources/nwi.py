"""USFWS National Wetlands Inventory (NWI) — wetland features near a reach.

Sums NWI wetland polygon area within a small bbox around the reach point (a
screening proxy for wetland/floodplain storage and lateral floodplain features).
The service is the USFWS Wetlands Mapper layer hosted by USGS WIM (the old
www.fws.gov/wetlands address answers 404 since at least 2026-09-23).
Best-effort and light (attribute-only query, no geometry); returns None on any
failure so the StreamCat watershed-wetland % stays the primary evidence.
"""
from __future__ import annotations

from typing import Optional

import requests

_URL = ("https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/"
        "Wetlands/MapServer/0/query")
# The layer joins the NWI code table, so its fields come back table-qualified;
# the bare names are read as a fallback.
_ACRES = "Wetlands.ACRES"
_TYPE = "Wetlands.WETLAND_TYPE"


def wetlands_near(lat: float, lon: float, deg: float = 0.02,
                  timeout: float = 12.0) -> Optional[dict]:
    """Return ``{acres, count, types}`` of NWI wetlands near the point, or None.

    ``deg`` ~0.02 is roughly a 1.4 mi box around the reach — enough to capture
    adjacent floodplain wetlands without a heavy pull.
    """
    env = f"{lon-deg:.5f},{lat-deg:.5f},{lon+deg:.5f},{lat+deg:.5f}"
    params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
              "spatialRel": "esriSpatialRelIntersects", "outFields": f"{_ACRES},{_TYPE}",
              "returnGeometry": "false", "f": "json"}
    try:
        r = requests.get(_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        feats = r.json().get("features", [])
    except Exception:  # noqa: BLE001
        return None
    acres = 0.0
    types: dict[str, int] = {}
    for f in feats:
        a = f.get("attributes", {})
        try:
            acres += float(a.get(_ACRES, a.get("ACRES")) or 0.0)
        except (TypeError, ValueError):
            pass
        t = a.get(_TYPE, a.get("WETLAND_TYPE"))
        if t:
            types[t] = types.get(t, 0) + 1
    return {"acres": round(acres, 1), "count": len(feats), "types": types}
