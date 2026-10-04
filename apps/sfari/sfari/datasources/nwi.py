"""USFWS National Wetlands Inventory (NWI) — wetland features near a reach.

Sums NWI wetland polygon area within a small bbox around the reach point (a
screening proxy for wetland/floodplain storage and lateral floodplain features).
The service is the USFWS Wetlands Mapper layer hosted by USGS WIM (the old
www.fws.gov/wetlands address answers 404 since at least 2026-09-23).
Best-effort and light (attribute-only query, no geometry); returns None on any
failure so the StreamCat watershed-wetland % stays the primary evidence.

Where the STAF data bundle holds the assessment reach, ``wetlands_along_reach``
answers instead (owner decision D3, 2026-10-01): NWI wetland area within 150 m of
the reach centerline, from strips precomputed along every HR flowline, by NWI
system. ``wetlands_near`` (the box) answers wherever the bundle cannot.
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


M2_PER_ACRE = 4046.8564224


def wetlands_along_reach(record: Optional[dict]) -> Optional[dict]:
    """``{acres, stripAcres, pctOfStrip, bySystem, flowlines, halfWidthM}``: NWI wetland area
    within 150 m of the STAF site engine's assessment reach (``record``: the engine record, its
    ``site.nhdplusId`` and ``reach.geometry``), from the STAF data bundle, or None (no record, no
    reach geometry, or the bundle off or not holding the reach). Never raises."""
    try:
        from .._vendor.site_engine import bundle
        nid = ((record or {}).get("site") or {}).get("nhdplusId")
        geom = ((record or {}).get("reach") or {}).get("geometry")
        got = bundle.reach_wetlands(int(nid), geom) if nid and geom else None
        if not got or not got.get("stripM2"):
            return None
        acres = got["wetlandM2Total"] / M2_PER_ACRE
        return {"acres": round(acres, 1), "stripAcres": round(got["stripM2"] / M2_PER_ACRE, 1),
                "pctOfStrip": round(100.0 * got["wetlandM2Total"] / got["stripM2"], 1),
                "bySystem": dict((k, round(v / M2_PER_ACRE, 2)) for k, v in got["wetlandM2"].items()),
                "flowlines": len(got["flowlines"]), "halfWidthM": bundle.NWI_STRIP_HALF_WIDTH_M}
    except Exception:  # noqa: BLE001
        return None


def wetlands_for_reach(lat: float, lon: float, record: Optional[dict]) -> Optional[dict]:
    """``wetlands_along_reach`` where the bundle answers, else ``wetlands_near``."""
    return wetlands_along_reach(record) or wetlands_near(lat, lon)


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
