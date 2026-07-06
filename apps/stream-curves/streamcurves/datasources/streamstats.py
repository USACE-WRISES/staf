"""Port of app/helpers/data_sources.R — USGS StreamStats section.

New ss-delineate / ss-hydro services (keyless, synchronous):

- https://streamstats.usgs.gov/ss-hydro/v1/basin-characteristics/{STATE}
- https://streamstats.usgs.gov/ss-hydro/v1/basin-characteristics/calculate-using-ssdelineate/
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from . import _MISS, _as_float, _cache_get, _cache_set, _get_json, _or, _post_json

SS_HYDRO = "https://streamstats.usgs.gov/ss-hydro/v1"
SS_DELINEATE = "https://streamstats.usgs.gov/ss-delineate/v1"


def ss_core_bcs() -> dict[str, str]:
    """Recommended core basin characteristics (code -> human label)."""
    return {
        "DRNAREA": "Drainage area (sq mi)",
        "PRECIP": "Mean annual precipitation (in)",
        "FOREST": "Forest (%)",
        "LC11DEV": "Developed land (%)",
        "LC11IMP": "Impervious (%)",
        "BSLDEM10ff": "Mean basin slope (ft/ft)",
    }


def _as_chr(v: Any) -> str | None:
    """R as.character(): None (NA) stays None, everything else stringified."""
    return None if v is None else str(v)


def parse_ss_bc_meta(j: Any) -> pd.DataFrame | None:
    """Available basin-characteristic codes for a state -> DataFrame(code, name,
    unit) or None."""
    if not j:
        return None
    rows = [
        {
            "code": _as_chr(_or(x.get("code"), None)),
            "name": _as_chr(_or(x.get("name"), None)),
            "unit": _as_chr(_or(x.get("unit"), "")),
        }
        for x in j
    ]
    return pd.DataFrame(rows, columns=["code", "name", "unit"])


def ss_state_bcs(state: Any) -> pd.DataFrame | None:
    """Basin-characteristic metadata for a 2-letter state code; None on failure."""
    if state is None:
        return None
    state = str(state).upper()
    if not state:
        return None
    try:
        j = _get_json("%s/basin-characteristics/%s" % (SS_HYDRO, state))
        return parse_ss_bc_meta(j)
    except Exception:
        return None


def parse_ss_bcs(j: Any) -> dict[str, float]:
    """Parse the calculate-using-ssdelineate result -> {code: value}, keeping
    only successful BCs ("Local SSZonal successful"); the rest become NaN.

    NOTE(parity): duplicate codes collapse dict-style (last wins) whereas R's
    named vector keeps duplicates (and later name-indexing takes the first);
    real payloads have unique codes.
    """
    if not j:
        return {}
    out: dict[str, float] = {}
    for x in j:
        code = _as_chr(_or(x.get("code"), None))
        val = _as_float(x.get("value"))
        ok = str(_or(x.get("msg"), "")) == "Local SSZonal successful"
        out[code] = val if ok else math.nan
    return out


def ss_basin_characteristics(lat: Any, lon: Any, state: Any, codes) -> dict[str, float]:
    """Basin characteristics for a point (delineate + compute, one sync call).
    ``state`` is a 2-letter code; ``codes`` are BC codes. Returns a dict aligned
    to ``codes`` (NaN for unsupported state / failed BC). Never raises; cached."""
    lon = _as_float(lon)
    lat = _as_float(lat)
    state = str(state).upper() if state is not None else ""
    codes = list(codes)
    na_out = {c: math.nan for c in codes}
    if not (math.isfinite(lon) and math.isfinite(lat)) or not state or not codes:
        return na_out
    key = "ssbc:%s:%.5f:%.5f:%s" % (state, lon, lat, ",".join(sorted(codes)))
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    try:
        j = _post_json(
            SS_HYDRO + "/basin-characteristics/calculate-using-ssdelineate/",
            params={"region": state, "lat": lat, "lon": lon, "BCs": ",".join(codes)},
            # empty body required — a query-only POST returns HTTP 411 (Length Required)
            data=b"",
            headers={"Content-Type": "application/json"},
        )
        val = parse_ss_bcs(j)
    except Exception:
        val = None
    if val is None:
        val = dict(na_out)
    # align to the requested codes: missing -> NaN, extras dropped, order kept
    out = {c: val.get(c, math.nan) for c in codes}
    _cache_set(key, out)
    return out
