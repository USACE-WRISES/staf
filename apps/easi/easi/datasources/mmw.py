"""Model My Watershed (MMW) watershed-delineation client.

A thin REST client for MMW's point-precise split-catchment delineation
(NHDPlus v2.1 + TauDEM), used by ``scripts/compare_watersheds.py`` to compare
against EASI's NLDI/COMID reach-outlet basin (``easi.delineation``). Not wired
into the app — it exists so we can evaluate MMW before deciding whether to offer
it as a selectable high-resolution delineation source.

Protocol (token-authenticated; see https://modelmywatershed.org/api/docs/):
  POST {_ANALYZE_URL}  body {"location":[lat,lon], "dataSource":..., "snappingOn":..., "simplify":...}
       header Authorization: Token <key>            -> {"job": "<uuid>", "status": "started"}
  GET  {_JOB_URL}      poll until status == "complete" -> result.{watershed, input_pt}

``dataSource``: "nhd" = CONUS High-Res (default here), "drb" = Delaware 10 m,
"tdx" = TDX global basins. Rate limits: 90 req/min, 15000/day.

The API key is read from ``$MMW_API_KEY`` first, else a gitignored
``scripts/.mmw_api_key`` file — never hardcoded, never logged. Following the
datasource convention, this module NEVER raises to the caller: it returns a
tuple ending in a ``warnings`` list and degrades to ``None`` geometry on any
failure (missing key, HTTP error, job failure/timeout, off-network point).

Areas are computed from the returned geometry in EPSG:5070 (matching
``easi.delineation._largest_polygon``) rather than from MMW's area property, so
A-vs-B comparisons use one identical area method.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

_BASE = "https://modelmywatershed.org/api"
_ANALYZE_URL = f"{_BASE}/watershed/"
_JOB_URL = f"{_BASE}/jobs/{{uuid}}/"

_KEY_ENV = "MMW_API_KEY"
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_KEY_FILE = os.path.join(_REPO_ROOT, "scripts", ".mmw_api_key")  # gitignored

CRS_WGS84 = 4326
CRS_ALBERS = 5070  # USGS CONUS Albers Equal Area (metres) — area math
_GEOJSON_GEOM_TYPES = {"Polygon", "MultiPolygon", "Point", "MultiPoint",
                       "LineString", "MultiLineString", "GeometryCollection"}


# --------------------------------------------------------------------------- #
# key handling (never logged)
# --------------------------------------------------------------------------- #
def _api_key() -> Optional[str]:
    """Return the MMW token from ``$MMW_API_KEY`` or the gitignored key file."""
    env = os.environ.get(_KEY_ENV)
    if env and env.strip():
        return env.strip()
    try:
        with open(_KEY_FILE, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        return key or None
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# REST steps (each best-effort; never raises)
# --------------------------------------------------------------------------- #
def _start_job(lat: float, lon: float, data_source: str, snapping: bool,
               simplify: float, key: str, timeout: float) -> tuple[Optional[str], list[str]]:
    """POST the delineation request; return (job_uuid, warnings)."""
    headers = {"Authorization": f"Token {key}", "Content-Type": "application/json"}
    body = {"location": [lat, lon], "dataSource": data_source,
            "snappingOn": bool(snapping), "simplify": simplify}
    try:
        r = requests.post(_ANALYZE_URL, json=body, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return None, [f"MMW watershed POST failed: {exc}"]
    if r.status_code not in (200, 201, 202):
        return None, [f"MMW watershed POST HTTP {r.status_code}"]
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return None, [f"MMW watershed POST response not JSON: {exc}"]
    job = data.get("job") or data.get("job_uuid") or data.get("id")
    if not job:
        return None, [f"MMW watershed POST returned no job id (keys: {list(data)[:6]})"]
    return str(job), []


def _poll_job(uuid: str, key: str, *, timeout: float, max_wait: float,
              interval: float) -> tuple[Optional[dict], list[str]]:
    """Poll the job until it completes; return (result_dict, warnings).

    ``max_wait`` bounds the *modeled* budget (sum of inter-poll delays), so tests
    that no-op ``time.sleep`` still terminate deterministically. Backoff grows the
    delay 1.5x up to 10 s. ``failed``/timeout -> (None, warnings), never raises.
    """
    url = _JOB_URL.format(uuid=uuid)
    headers = {"Authorization": f"Token {key}"}
    warnings: list[str] = []
    waited, delay = 0.0, interval
    while waited < max_wait:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                status = str(data.get("status") or "").lower()
                if status in ("complete", "completed", "success"):
                    return data.get("result"), warnings
                if status in ("failed", "error"):
                    detail = data.get("error") or data.get("status") or "unknown"
                    return None, warnings + [f"MMW job failed: {detail}"]
            else:
                warnings.append(f"MMW job poll HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"MMW job poll error: {exc}")
        time.sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 10.0)
    return None, warnings + [f"MMW job did not complete within {max_wait:.0f}s budget"]


# --------------------------------------------------------------------------- #
# result extraction (geometry helpers use a local geopandas import)
# --------------------------------------------------------------------------- #
def _as_feature_collection(obj) -> Optional[dict]:
    """Normalize a GeoJSON FeatureCollection / Feature / geometry / feature list."""
    if not obj:
        return None
    if isinstance(obj, list):
        return {"type": "FeatureCollection", "features": obj}
    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "FeatureCollection":
            return obj
        if t == "Feature":
            return {"type": "FeatureCollection", "features": [obj]}
        if t in _GEOJSON_GEOM_TYPES:
            return {"type": "FeatureCollection",
                    "features": [{"type": "Feature", "properties": {}, "geometry": obj}]}
        if obj.get("features"):
            return {"type": "FeatureCollection", "features": obj["features"]}
    return None


def _area_sqkm_5070(fc: dict) -> Optional[float]:
    """Total polygon area (km²) of a FeatureCollection, reprojected to EPSG:5070."""
    try:
        import geopandas as gpd
        g = gpd.GeoDataFrame.from_features(fc["features"], crs=CRS_WGS84)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        if g.empty:
            return None
        return float(g.to_crs(CRS_ALBERS).geometry.area.sum()) / 1e6
    except Exception:  # noqa: BLE001 - area is best-effort
        return None


def _extract(result) -> tuple[Optional[dict], Optional[float], Optional[dict], list[str]]:
    """Pull (watershed FC, area_sqkm, input_pt FC, warnings) from a job result."""
    if not isinstance(result, dict):
        return None, None, None, ["MMW job result missing"]
    fc = _as_feature_collection(result.get("watershed"))
    pt = _as_feature_collection(result.get("input_pt"))
    if not fc or not fc.get("features"):
        return None, None, pt, ["MMW result had no watershed geometry"]
    return fc, _area_sqkm_5070(fc), pt, []


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def delineate_watershed_mmw(
    lat: float, lon: float, *,
    data_source: str = "nhd", snapping: bool = True, simplify: float = 0.0,
    timeout: float = 30.0, max_wait: float = 120.0, poll_interval: float = 2.0,
) -> tuple[Optional[dict], Optional[float], Optional[dict], list[str]]:
    """Delineate the watershed for a point via MMW.

    Returns ``(watershed_geojson, area_sqkm, snapped_pt_geojson, warnings)`` —
    geometry in EPSG:4326, area in km² (EPSG:5070). Mirrors
    ``easi.delineation.delineate_watershed``'s shape plus MMW's snapped point.
    Never raises; on any failure the geometry/area are ``None`` and ``warnings``
    explains why.
    """
    key = _api_key()
    if not key:
        return None, None, None, [f"{_KEY_ENV} not set; skipping MMW delineation"]
    job, w1 = _start_job(lat, lon, data_source, snapping, simplify, key, timeout)
    if not job:
        return None, None, None, w1
    result, w2 = _poll_job(job, key, timeout=timeout, max_wait=max_wait, interval=poll_interval)
    if result is None:
        return None, None, None, w1 + w2
    fc, area, pt, w3 = _extract(result)
    return fc, area, pt, w1 + w2 + w3
