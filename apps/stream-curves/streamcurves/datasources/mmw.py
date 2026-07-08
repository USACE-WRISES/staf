"""Port of app/helpers/data_sources.R — Model My Watershed (WikiWatershed) section.

Gated by ``MMW_API_KEY``: the key is read from the environment (never
hardcoded). All MMW calls require the key; without it these return None so the
rest of the app is unaffected. Throttled to the account limit (90/min);
requests use a 60 s timeout and the shared retrying session.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from . import _MISS, _as_float, _cache_get, _cache_set, _or, _request

MMW_BASE = "https://modelmywatershed.org/api"

# Gitignored app-local key file (fallback when MMW_API_KEY is unset), mirroring EASI.
_KEY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", ".mmw_api_key"
)
_SQKM_TO_SQMI = 0.3861021585  # kept local to avoid a datasources -> sites import

_sleep = time.sleep  # patched in tests

_MIN_INTERVAL_S = 60.0 / 90.0  # httr2 req_throttle(rate = 90/60)
_last_request_ts = float("-inf")


def _pluck(x: Any, key: str) -> Any:
    """R ``x$key`` on JSON payloads: None unless ``x`` is a dict with the key."""
    return x.get(key) if isinstance(x, dict) else None


def mmw_token() -> str | None:
    """MMW API token from ``$MMW_API_KEY`` or the gitignored ``scripts/.mmw_api_key``
    file (env wins). Never logged."""
    env = os.environ.get("MMW_API_KEY", "")
    if env.strip():
        return env.strip()
    try:
        with open(_KEY_FILE, "r", encoding="utf-8") as fh:
            k = fh.read().strip()
        return k or None
    except OSError:
        return None


def mmw_available() -> bool:
    return mmw_token() is not None


def _throttle() -> None:
    """Keep MMW traffic under 90 requests/min (port of httr2::req_throttle)."""
    global _last_request_ts
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _last_request_ts)
    if wait > 0:
        _sleep(wait)
    _last_request_ts = time.monotonic()


def _mmw_json(method: str, path: str, json_body: Any = None) -> Any:
    """Port of mmw_req(): token auth + throttle + 60 s timeout. Raises on error
    (callers wrap)."""
    _throttle()
    resp = _request(
        method,
        MMW_BASE + path,
        json_body=json_body,
        headers={"Authorization": "Token %s" % mmw_token()},
        timeout=60,
    )
    return resp.json()


def mmw_poll(job: str, max_polls: int = 40, interval: float = 2) -> Any:
    """Poll an MMW job to completion; returns the ``result`` payload or None.
    Never raises. Sleeps ``interval`` seconds *before* each poll (like R)."""
    for _ in range(int(max_polls)):
        _sleep(interval)
        try:
            st = _mmw_json("GET", "/jobs/%s/" % job)
        except Exception:
            st = None
        if not isinstance(st, dict):
            continue
        if st.get("status") == "complete":
            return st.get("result")
        if st.get("status") == "failed":
            return None
    return None


def mmw_delineate(lat: Any, lon: Any, max_polls: int = 40) -> Any:
    """Delineate the watershed upstream of a point (rapid watershed delineation).
    Returns the watershed GeoJSON geometry (a dict) or None. Cached per point —
    a cached None is reused, not re-fetched (R wraps it in list(g = geom)).
    Requires MMW_API_KEY."""
    if not mmw_available():
        return None
    lon = _as_float(lon)
    lat = _as_float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    key = "mmwdel:%.5f:%.5f" % (lon, lat)
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    geom = None
    try:
        body = {"location": [lat, lon], "snappingOn": True, "simplify": 0, "dataSource": "nhd"}
        sub = _mmw_json("POST", "/watershed/", json_body=body)
        job = _or(_pluck(sub, "job"), _pluck(sub, "job_uuid"))
        if job is not None:
            res = mmw_poll(job, max_polls)
            geom = _pluck(_pluck(res, "watershed"), "geometry")
    except Exception:
        geom = None
    _cache_set(key, geom)
    return geom


def mmw_analyze_geom(geom: Any, category: str, max_polls: int = 40) -> Any:
    """Run an MMW analyze job for a watershed geometry + category (e.g.
    "land/2019_2019", "soil", "terrain", "climate"). Returns the survey dict
    (with ``categories``) or None. Cached per (geometry, category). Never
    raises; requires MMW_API_KEY."""
    if not mmw_available() or geom is None:
        return None
    # R: substr(jsonlite::toJSON(geom, auto_unbox = TRUE), 1, 96)
    gkey = json.dumps(geom, separators=(",", ":"))[:96]
    key = "mmwana:%s:%s" % (category, gkey)
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    survey = None
    try:
        sub = _mmw_json("POST", "/analyze/%s/" % category, json_body=geom)
        job = _or(_pluck(sub, "job"), _pluck(sub, "job_uuid"))
        res = sub if job is None else mmw_poll(job, max_polls)
        # R: res$survey %||% res
        survey = _or(_pluck(res, "survey"), res)
    except Exception:
        survey = None
    _cache_set(key, survey)
    return survey


def _geojson_area_sqkm(geom: Any) -> float:
    """Total area (sq km) of a GeoJSON Polygon/MultiPolygon via spherical polygon
    area (the same math NLDI basins use). Outer rings add, holes subtract; NaN when
    the geometry is missing/unusable."""
    if not isinstance(geom, dict):
        return math.nan
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return math.nan
    from ..geo import spherical_polygon_area_m2  # local import: geo is a leaf, no cycle
    total = 0.0
    seen = False
    for poly in polys or []:
        for i, ring in enumerate(poly or []):
            if not ring or len(ring) < 3:
                continue
            try:
                a = spherical_polygon_area_m2([p[0] for p in ring], [p[1] for p in ring])
            except (TypeError, ValueError, IndexError):
                continue
            if not math.isfinite(a):
                continue
            seen = True
            total += a if i == 0 else -a   # ring 0 = outer (+); rings 1+ = holes (-)
    return (total / 1e6) if seen else math.nan


def mmw_core_metrics() -> dict[str, dict[str, str | None]]:
    """Curated Model My Watershed metrics: code -> {label, category}."""
    return {
        "mmw_developed_pct": {"label": "Developed land (%)", "category": "land/2019_2019"},
        "mmw_forest_pct": {"label": "Forest (%)", "category": "land/2019_2019"},
        "mmw_agriculture_pct": {"label": "Agriculture (%)", "category": "land/2019_2019"},
        "mmw_wetland_pct": {"label": "Wetland (%)", "category": "land/2019_2019"},
        "mmw_soil_cd_pct": {"label": "Slow-infiltration soils C/D (%)", "category": "soil"},
        "mmw_mean_slope_pct": {"label": "Mean terrain slope (%)", "category": "terrain"},
        "mmw_mean_elev_m": {"label": "Mean elevation (m)", "category": "terrain"},
        "mmw_annual_precip_cm": {"label": "Annual precipitation (cm)", "category": "climate"},
        # geometry-derived (category None): area of the delineated watershed, not an
        # analyze survey — computed from mmw_delineate's polygon in mmw_site_metrics.
        "mmw_da_sqmi": {"label": "Drainage area (sq mi)", "category": None},
    }


def _mmw_cov_sum(categories: list, codes) -> float:
    """Sum coverage (fraction 0-1) over the given land/soil category codes;
    NaN if none present."""
    if not categories:
        return math.nan
    s = 0.0
    seen = False
    for cc in categories:
        if _or(cc.get("code"), "") in codes:
            s = s + _or(cc.get("coverage"), 0)  # like R, a non-numeric coverage raises
            seen = True
    return s if seen else math.nan


def _mmw_terrain_avg(categories: list, field: str) -> float:
    for cc in categories:
        if cc.get("type") == "average":
            return _as_float(_or(cc.get(field), None))
    return math.nan


def mmw_extract(code: str, surveys: dict[str, Any]) -> float:
    """Extract one curated metric value from the analyze surveys (dict keyed by
    category)."""
    land = _or(_pluck(surveys.get("land/2019_2019"), "categories"), [])
    soil = _or(_pluck(surveys.get("soil"), "categories"), [])
    terr = _or(_pluck(surveys.get("terrain"), "categories"), [])
    clim = _or(_pluck(surveys.get("climate"), "categories"), [])
    if code == "mmw_developed_pct":
        return 100 * _mmw_cov_sum(
            land, ("developed_open", "developed_low", "developed_med", "developed_high")
        )
    if code == "mmw_forest_pct":
        return 100 * _mmw_cov_sum(land, ("deciduous_forest", "evergreen_forest", "mixed_forest"))
    if code == "mmw_agriculture_pct":
        return 100 * _mmw_cov_sum(land, ("pasture", "cultivated_crops"))
    if code == "mmw_wetland_pct":
        return 100 * _mmw_cov_sum(land, ("woody_wetlands", "herbaceous_wetlands"))
    if code == "mmw_soil_cd_pct":
        return 100 * _mmw_cov_sum(soil, ("c", "cd", "d", "bd", "ad"))
    if code == "mmw_mean_slope_pct":
        return _mmw_terrain_avg(terr, "slope")
    if code == "mmw_mean_elev_m":
        return _mmw_terrain_avg(terr, "elevation")
    if code == "mmw_annual_precip_cm":
        if not clim:
            return math.nan
        return sum(_as_float(_or(cc.get("ppt"), 0)) for cc in clim)
    return math.nan


def mmw_site_metrics(lat: Any, lon: Any, codes, max_polls: int = 40) -> dict[str, float]:
    """Pull curated MMW metrics for a site: delineate the watershed, run only
    the analyze categories needed by ``codes``, and extract. Returns a dict
    aligned to ``codes`` (NaN on any failure / no key). Never raises."""
    codes = list(codes)
    na_out = {cd: math.nan for cd in codes}
    if not mmw_available() or not codes:
        return na_out
    geom = mmw_delineate(lat, lon, max_polls=max_polls)
    if geom is None:
        return na_out
    meta = mmw_core_metrics()
    cats_needed: list[str] = []
    for cd in codes:
        cat = (_or(meta.get(cd), {})).get("category")
        if cat is not None and cat not in cats_needed:
            cats_needed.append(cat)
    surveys: dict[str, Any] = {}
    for categ in cats_needed:
        surveys[categ] = mmw_analyze_geom(geom, categ, max_polls=max_polls)
    out: dict[str, float] = {}
    for cd in codes:
        if cd == "mmw_da_sqmi":
            # geometry-derived: area of the delineated watershed (sq km -> sq mi)
            out[cd] = _as_float(_geojson_area_sqkm(geom) * _SQKM_TO_SQMI)
        else:
            out[cd] = _as_float(mmw_extract(cd, surveys))
    return out
