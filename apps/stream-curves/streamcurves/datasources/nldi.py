"""Port of app/helpers/data_sources.R — NLDI section (lat/lon -> NHDPlus COMID).

Endpoint (keyless): https://api.water.usgs.gov/nldi/linked-data/comid/position
"""

from __future__ import annotations

import math
from typing import Any, Callable

from . import _MISS, _as_float, _as_int, _cache_get, _cache_set, _get_json, _or

NLDI_POSITION_URL = "https://api.water.usgs.gov/nldi/linked-data/comid/position"


def parse_nldi_comid(j: Any) -> int | None:
    """Pure parser: NLDI position FeatureCollection -> COMID (int) or None (NA)."""
    feats = j.get("features") if isinstance(j, dict) else None
    if not feats:
        return None
    props = _or(feats[0].get("properties"), {})
    cid = _or(_or(props.get("comid"), props.get("COMID")), props.get("identifier"))
    if cid is None:
        return None
    return _as_int(cid)


def nldi_comid(lon: Any, lat: Any) -> int | None:
    """COMID at a point; None on any failure. Cached per point (a cached miss is
    reused, not refetched — R stores NA_integer_ in .ds_cache)."""
    lon = _as_float(lon)
    lat = _as_float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    key = "comid:%.6f:%.6f" % (lon, lat)
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    try:
        j = _get_json(
            NLDI_POSITION_URL,
            params={"coords": "POINT(%f %f)" % (lon, lat), "f": "json"},
        )
        val = parse_nldi_comid(j)
    except Exception:
        val = None
    _cache_set(key, val)
    return val


def nldi_comids(lon, lat, progress: Callable[[int, int], Any] | None = None) -> list[int | None]:
    """COMIDs for many points; returns a list aligned to (lon, lat)."""
    lon = list(lon)
    lat = list(lat)
    n = len(lon)
    out: list[int | None] = []
    for i in range(n):
        out.append(nldi_comid(lon[i], lat[i]))
        if callable(progress):
            progress(i + 1, n)
    return out
