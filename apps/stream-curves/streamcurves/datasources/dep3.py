"""Port of app/helpers/data_sources.R — 3DEP EPQS section (point elevation, metres).

Endpoint (keyless): https://epqs.nationalmap.gov/v1/json
"""

from __future__ import annotations

import math
from typing import Any

from . import _MISS, _as_float, _cache_get, _cache_set, _get_json, _or

EPQS_URL = "https://epqs.nationalmap.gov/v1/json"


def parse_epqs(j: Any) -> float:
    """Pure parser: EPQS JSON -> elevation (m) as float, NaN when absent."""
    j = j if isinstance(j, dict) else {}
    v = _or(j.get("value"), j.get("elevation"))
    return _as_float(v)


def epqs_elev(lon: Any, lat: Any) -> float:
    """Point elevation in metres; NaN on any failure. Cached per point (a cached
    NaN is reused, not refetched — R stores NA_real_ in .ds_cache)."""
    lon = _as_float(lon)
    lat = _as_float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return math.nan
    key = "epqs:%.6f:%.6f" % (lon, lat)
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    try:
        j = _get_json(EPQS_URL, params={"x": lon, "y": lat, "units": "Meters", "wkid": 4326})
        val = parse_epqs(j)
    except Exception:
        val = math.nan
    _cache_set(key, val)
    return val
