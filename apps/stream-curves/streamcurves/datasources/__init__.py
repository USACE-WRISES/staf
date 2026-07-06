"""Port of app/helpers/data_sources.R — shared plumbing + per-service re-exports.

Keyless public data-source clients for the import wizard. Design rules (same as
the R helper):

- every network call is wrapped so it NEVER raises (failure -> None / NaN /
  empty frame);
- parsing is split into pure ``parse_*`` functions so they unit-test offline;
- per-point results are memoised in-session (``_DS_CACHE``, port of R's
  ``.ds_cache``).

Service modules:

- :mod:`.nldi`        NLDI lat/lon -> NHDPlus COMID
- :mod:`.streamcat`   EPA StreamCAT watershed metrics + catalog
- :mod:`.streamstats` USGS StreamStats basin characteristics
- :mod:`.mmw`         Model My Watershed (gated by ``MMW_API_KEY``)
- :mod:`.dep3`        3DEP EPQS point elevation

The shared request plumbing below is the port of R's ``sc_req()``: one
``requests.Session`` with a StreamCurves user agent, 30 s timeout and retry on
transient statuses (429/500/502/503/504), mirroring httr2's exponential retry.
"""

from __future__ import annotations

import math
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# NOTE(parity): R uses "StreamCurves/import-wizard (R; +https://streamcurves.local)"
# (and "(R)" for MMW); the Python port advertises itself accordingly.
_UA = "StreamCurves/import-wizard (Python)"

#: transient statuses retried by R's sc_req()/mmw_req() (we have seen 503s)
_TRANSIENT_STATUSES = (429, 500, 502, 503, 504)

# ── session (port of sc_req) ─────────────────────────────────────────────────

_SESSION: requests.Session | None = None


def _build_session() -> requests.Session:
    """One retrying Session shared by every fetcher (httr2 req_retry analogue)."""
    retry = Retry(
        # NOTE(parity): httr2 max_tries = 3 means 3 attempts total (2 retries);
        # urllib3 total=3 allows up to 3 retries. Kept per the port spec.
        total=3,
        status_forcelist=list(_TRANSIENT_STATUSES),
        backoff_factor=1.0,  # ~exponential, like httr2's default backoff
        allowed_methods=None,  # httr2 retries POSTs too
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = _UA
    return s


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _build_session()
    return _SESSION


def _request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict | None = None,
    timeout: float = 30,
) -> requests.Response:
    """Perform a request; raises on HTTP/network errors (like httr2::req_perform).

    Public fetchers wrap this in try/except so they themselves never raise.
    """
    resp = _session().request(
        method, url, params=params, data=data, json=json_body, headers=headers, timeout=timeout
    )
    resp.raise_for_status()
    return resp


def _get_json(url: str, params: dict | None = None, timeout: float = 30) -> Any:
    return _request("GET", url, params=params, timeout=timeout).json()


def _get_text(url: str, params: dict | None = None, timeout: float = 30) -> str:
    return _request("GET", url, params=params, timeout=timeout).text


def _post_json(
    url: str,
    *,
    params: dict | None = None,
    data: Any = None,
    json_body: Any = None,
    headers: dict | None = None,
    timeout: float = 30,
) -> Any:
    return _request(
        "POST", url, params=params, data=data, json_body=json_body, headers=headers, timeout=timeout
    ).json()


# ── in-session memo cache (port of .ds_cache) ────────────────────────────────

_MISS = object()  # sentinel: distinguishes "not cached" from a cached None/NaN

_DS_CACHE: dict[str, Any] = {}


def clear_ds_cache() -> None:
    """Port of R clear_ds_cache()."""
    _DS_CACHE.clear()


def _cache_get(key: str) -> Any:
    """Return the cached value or the ``_MISS`` sentinel.

    A cached None/NaN IS returned (never refetched) — same as R, where failed
    lookups are stored as NA (or wrapped in a list for the NULL-valued MMW
    entries) so they short-circuit the NULL cache-miss check.
    """
    return _DS_CACHE.get(key, _MISS)


def _cache_set(key: str, value: Any) -> Any:
    _DS_CACHE[key] = value
    return value


# ── small R-semantics helpers ────────────────────────────────────────────────


def _or(x: Any, default: Any) -> Any:
    """R ``%||%`` (NULL-coalescing): ``x`` unless it is None."""
    return x if x is not None else default


def _as_float(x: Any) -> float:
    """R ``suppressWarnings(as.numeric(x))`` — NaN when not coercible."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _as_int(x: Any) -> int | None:
    """R ``suppressWarnings(as.integer(x))`` — parse as number, truncate toward
    zero; None (NA) when not coercible/finite."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return int(v)


# ── re-exports (public API mirrors app/helpers/data_sources.R) ───────────────
# These imports must stay below the plumbing definitions: the submodules import
# the plumbing from this (partially initialised) package.

from .nldi import (  # noqa: E402
    NLDI_POSITION_URL,
    nldi_comid,
    nldi_comids,
    parse_nldi_comid,
)
from .streamcat import (  # noqa: E402
    STREAMCAT_MIRROR,
    STREAMCAT_PRIMARY,
    parse_streamcat_catalog,
    parse_streamcat_csv,
    parse_streamcat_json,
    streamcat_catalog,
    streamcat_metrics,
)
from .mmw import (  # noqa: E402
    MMW_BASE,
    mmw_analyze_geom,
    mmw_available,
    mmw_core_metrics,
    mmw_delineate,
    mmw_extract,
    mmw_poll,
    mmw_site_metrics,
    mmw_token,
)
from .streamstats import (  # noqa: E402
    SS_DELINEATE,
    SS_HYDRO,
    parse_ss_bc_meta,
    parse_ss_bcs,
    ss_basin_characteristics,
    ss_core_bcs,
    ss_state_bcs,
)
from .dep3 import (  # noqa: E402
    EPQS_URL,
    epqs_elev,
    parse_epqs,
)

__all__ = [
    "clear_ds_cache",
    # nldi
    "NLDI_POSITION_URL",
    "parse_nldi_comid",
    "nldi_comid",
    "nldi_comids",
    # streamcat
    "STREAMCAT_PRIMARY",
    "STREAMCAT_MIRROR",
    "parse_streamcat_csv",
    "parse_streamcat_json",
    "streamcat_metrics",
    "parse_streamcat_catalog",
    "streamcat_catalog",
    # mmw
    "MMW_BASE",
    "mmw_token",
    "mmw_available",
    "mmw_poll",
    "mmw_delineate",
    "mmw_analyze_geom",
    "mmw_core_metrics",
    "mmw_extract",
    "mmw_site_metrics",
    # streamstats
    "SS_HYDRO",
    "SS_DELINEATE",
    "ss_core_bcs",
    "parse_ss_bc_meta",
    "ss_state_bcs",
    "parse_ss_bcs",
    "ss_basin_characteristics",
    # epqs
    "EPQS_URL",
    "parse_epqs",
    "epqs_elev",
]
