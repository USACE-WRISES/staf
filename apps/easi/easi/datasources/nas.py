"""USGS Nonindigenous Aquatic Species (NAS) — established taxa by HUC (no key).

The NAS API only filters spatially by HUC (8/10/12) or state/county. We query by
HUC12 (finer) for established nonindigenous taxa. Never raises — returns [] on
failure.
"""
from __future__ import annotations

from typing import Optional

import requests

_URL = "https://nas.er.usgs.gov/api/v2/occurrence/search"


def established_taxa(huc12: str | None = None, huc8: str | None = None,
                     timeout: float = 10.0) -> Optional[list[str]]:
    """Sorted list of established nonindigenous scientific names.

    Returns [] for a genuine no-records result, and None on query failure (so
    the adapter can distinguish 'none present' from 'service unavailable').

    Short fail-fast ``timeout`` (interactive screening flow): a slow NAS API should
    mark the metric unavailable quickly rather than stall the report.
    """
    params = {"status": "established", "limit": "500"}
    if huc12:
        params["huc12"] = huc12
    elif huc8:
        params["huc8"] = huc8
    else:
        return None
    try:
        r = requests.get(_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        j = r.json()
        records = j.get("results", j if isinstance(j, list) else [])
        names = {x.get("scientificName") for x in records if x.get("scientificName")}
        return sorted(n for n in names if n)
    except Exception:  # noqa: BLE001 - resilience by design
        return None
