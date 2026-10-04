"""USACE National Inventory of Dams (NID) — barriers near a reach (no token).

Counts dams within a proximity buffer of the reach (a simple proximity proxy for
'barriers within ~1 mile up/downstream'; a full network trace is a later
refinement). Never raises — returns None on failure.
"""
from __future__ import annotations

from typing import Optional

import requests

_URL = ("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
        "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")
MILE_DEG = 1.0 / 69.0  # ~degrees per mile (lat); good enough for a screening buffer


def barriers_near(lat: float, lon: float, miles: float = 1.0,
                  timeout: float = 10.0) -> Optional[list[dict]]:
    """Return list of nearby dams [{name, storage, height}] or None on failure.

    Short fail-fast ``timeout`` (interactive screening flow): a slow NID FeatureServer
    should mark the metric unavailable quickly rather than stall the report.
    """
    from .._vendor.site_engine import bundle
    found = bundle.lookups()
    if found is not None:
        # the STAF data bundle's NID snapshot (the same FeatureServer's dams), same box
        points, tables = found
        try:
            return points.nid_box(tables, lat, lon, miles)
        except Exception:  # noqa: BLE001 - the service answers instead
            pass
    dx = MILE_DEG * miles
    params = {
        "geometry": f"{lon-dx},{lat-dx},{lon+dx},{lat+dx}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "NAME,NID_STORAGE,DAM_HEIGHT",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        r = requests.get(_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        feats = r.json().get("features", [])
        return [{"name": f["attributes"].get("NAME"),
                 "storage": f["attributes"].get("NID_STORAGE"),
                 "height": f["attributes"].get("DAM_HEIGHT")} for f in feats]
    except Exception:  # noqa: BLE001
        return None
