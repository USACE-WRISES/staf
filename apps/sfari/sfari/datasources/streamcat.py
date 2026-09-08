"""EPA StreamCat REST client — per-COMID watershed/catchment landscape metrics.

Workhorse for ~12 EASI metrics. Primary host api.epa.gov; legacy mirror
java.epa.gov is tried on failure. Both can be intermittently unavailable, so
this client retries with backoff and NEVER raises to the caller — it returns a
dict (possibly empty) and metric adapters fall back (e.g. to NLCD) when a value
is missing. Results are cached in-process by (comid, names, aoi).

Verified field naming: metric base names carry an NLCD-year suffix and an
area-of-interest suffix, e.g. name='pctimp2019' & areaOfInterest='watershed'
returns column 'pctimp2019ws'. See plan.md for the confirmed name list.
"""
from __future__ import annotations

import time
from functools import lru_cache

import requests

_PRIMARY = "https://api.epa.gov/StreamCat/streams/metrics"
_MIRROR = "https://java.epa.gov/StreamCAT/metrics"
_AOI_SUFFIX = {"watershed": "ws", "catchment": "cat",
               "riparian_watershed": "wsrp100", "riparian_catchment": "catrp100"}


def _request(url: str, params: dict, timeout: float, retries: int = 2) -> dict | None:
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - resilience by design
            last = repr(exc)
        time.sleep(0.6 * (attempt + 1))
    return None


@lru_cache(maxsize=256)
def _fetch(comid: int, names: tuple[str, ...], aoi: str, timeout: float) -> tuple:
    """Cached low-level fetch -> tuple of (col, value) pairs (hashable)."""
    params = {"name": ",".join(names), "areaOfInterest": aoi, "comid": str(comid)}
    data = _request(_PRIMARY, params, timeout)
    if data is None:
        data = _request(_MIRROR, params, timeout)
    if not data:
        return tuple()
    # Response shape: {"items": [ {comid, <metric cols...>}, ... ]} (api.epa.gov)
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return tuple()
    row = items[0]
    return tuple((k.lower(), v) for k, v in row.items())


def metrics_by_comid(comid: int, base_names: list[str], aoi: str = "watershed",
                     timeout: float = 25.0) -> dict[str, float]:
    """Return {column_name: value} for the requested StreamCat metrics.

    Never raises. Returns {} if StreamCat is unavailable. Column names are the
    base name + aoi suffix (e.g. 'pctimp2019' -> 'pctimp2019ws').
    """
    if comid is None or not base_names:
        return {}
    pairs = _fetch(int(comid), tuple(sorted(base_names)), aoi, timeout)
    out: dict[str, float] = {}
    for k, v in pairs:
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = v  # keep non-numeric (e.g. categorical predicted condition)
    return out


def suffix(aoi: str) -> str:
    return _AOI_SUFFIX.get(aoi, "ws")
