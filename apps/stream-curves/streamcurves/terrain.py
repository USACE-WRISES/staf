"""Port of R/16_terrain_3dep.R.

USGS 3DEP terrain sampling + NLDI reach lookup for the cross-section tool, via
REST only (requests + json) — no rasterio/GDAL. Uses the shared plumbing and
``parse_nldi_comid`` from :mod:`streamcurves.datasources`.

- 3DEP getSamples : station/elevation profile along a transect polyline (JSON)
- NLDI            : snap a point -> COMID -> the local NHD flowline (reach geometry)
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from .datasources import (
    _MISS,
    _as_float,
    _as_int,
    _cache_get,
    _cache_set,
    _get_json,
    _or,
    _post_json,
    parse_nldi_comid,
)

# haversine_m also serves as a re-export: tests (and any callers of the
# pre-geo.py API) reach it as terrain.haversine_m.
from .geo import haversine_m, spherical_polygon_area_m2


THREEDEP_IMAGESERVER = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
)
NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"

# ── 3DEP getSamples ──────────────────────────────────────────────────────────


def _sample_value(s: dict) -> float:
    """3DEP sample value -> float; "NoData" / missing -> NaN."""
    v = s.get("value")
    if v is None or str(v) == "NoData":
        return math.nan
    return _as_float(v)


def parse_3dep_samples(j: Any, stations_m=None) -> dict | None:
    """Parse a 3DEP getSamples JSON response into station/elevation pairs.

    Drops "NoData" samples; aligns to ``stations_m`` when lengths match, else
    rebuilds the station axis from sample locations. None if < 5 valid points.
    Returns ``{"stations": ndarray, "elevs": ndarray, "resolution_m": float}``.

    NOTE(fix vs R): getSamples echoes sample locations in the *request* SR —
    lon/lat degrees (wkid 4326), not metres. The R helper takes raw Euclidean
    distances over those coordinates, so whenever the service returns a
    different sample count than requested the station axis collapses to
    degree-scale (~1e-3) values and the derived bankfull/floodprone widths
    round to 0. Geographic locations are measured with haversine here instead;
    projected (metre) locations keep the Euclidean path.
    """
    samples = j.get("samples") if isinstance(j, dict) else None
    if not samples:
        return None
    elev = np.array([_sample_value(s) for s in samples], dtype=float)
    m = elev.size
    res_m = _as_float(samples[0].get("resolution"))
    st = None
    if stations_m is not None:
        st = np.asarray(stations_m, dtype=float)
        if st.size != m:
            st = None
    if st is None:
        try:
            locs = np.array(
                [
                    [
                        _as_float((_or(s.get("location"), {})).get("x")),
                        _as_float((_or(s.get("location"), {})).get("y")),
                    ]
                    for s in samples
                ],
                dtype=float,
            )
        except Exception:
            locs = None
        if locs is not None and locs.shape[0] == m and bool(np.all(np.isfinite(locs))):
            x, y = locs[:, 0], locs[:, 1]
            if bool(np.all(np.abs(x) <= 360)) and bool(np.all(np.abs(y) <= 90)):
                d = np.array(
                    [
                        haversine_m(x[i], y[i], x[i + 1], y[i + 1])
                        for i in range(m - 1)
                    ],
                    dtype=float,
                )
            else:
                d = np.sqrt(np.diff(x) ** 2 + np.diff(y) ** 2)
            st = np.concatenate(([0.0], np.cumsum(d)))
        else:
            st = np.arange(m, dtype=float)  # R: seq_len(m) - 1
    ok = np.isfinite(elev)
    if int(ok.sum()) < 5:
        return None
    return {"stations": st[ok], "elevs": elev[ok], "resolution_m": res_m}


def _round10(x: float) -> float:
    """jsonlite::toJSON(..., digits = 10) analogue for coordinate payloads."""
    return round(float(x), 10)


def sample_transect_3dep(pts_lonlat, stations_m=None) -> dict | None:
    """Sample elevations along a transect (n x 2 lon/lat array) from 3DEP
    getSamples. POST (handles long geometries). Returns
    ``{"stations", "elevs", "resolution_m"}`` or None."""
    pts = np.asarray(pts_lonlat, dtype=float)
    if pts.ndim != 2 or pts.shape[0] < 2:
        return None
    geom = {
        "paths": [[[_round10(x), _round10(y)] for x, y in pts[:, :2]]],
        "spatialReference": {"wkid": 4326},
    }
    try:
        res = _post_json(
            THREEDEP_IMAGESERVER + "/getSamples",
            data={
                "geometryType": "esriGeometryPolyline",
                "geometry": json.dumps(geom, separators=(",", ":")),
                "sampleCount": int(pts.shape[0]),
                "returnFirstValueOnly": "true",
                "interpolation": "RSP_BilinearInterpolation",
                "f": "json",
            },
        )
    except Exception:
        res = None
    if res is None:
        return None
    return parse_3dep_samples(res, stations_m)


def sample_multipoint_3dep(pts_lonlat) -> dict | None:
    """Fallback: sample arbitrary points (multipoint). Order not guaranteed, so
    the caller must supply/keep its own station axis. Returns
    ``{"elevs": ndarray, "resolution_m": float}`` or None."""
    pts = np.asarray(pts_lonlat, dtype=float)
    if pts.ndim != 2 or not pts.shape[0]:
        return None
    geom = {
        "points": [[_round10(pts[i, 0]), _round10(pts[i, 1])] for i in range(pts.shape[0])],
        "spatialReference": {"wkid": 4326},
    }
    try:
        res = _post_json(
            THREEDEP_IMAGESERVER + "/getSamples",
            data={
                "geometryType": "esriGeometryMultipoint",
                "geometry": json.dumps(geom, separators=(",", ":")),
                "returnFirstValueOnly": "true",
                "interpolation": "RSP_BilinearInterpolation",
                "f": "json",
            },
        )
    except Exception:
        res = None
    if res is None or not isinstance(res, dict) or res.get("samples") is None:
        return None
    samples = res["samples"]
    elev = np.array([_sample_value(s) for s in samples], dtype=float)
    # NOTE(parity): like R (samples[[1]]$resolution), an empty samples array
    # raises here (IndexError vs R's "subscript out of bounds").
    return {"elevs": elev, "resolution_m": _as_float(samples[0].get("resolution"))}


# ── NLDI reach ───────────────────────────────────────────────────────────────


def _geojson_features(j: Any) -> list | None:
    """R: j$features %||% (if (!is.null(j$geometry)) list(j) else NULL)."""
    if not isinstance(j, dict):
        return None
    feats = j.get("features")
    if feats is None:
        feats = [j] if j.get("geometry") is not None else None
    return feats


def parse_nldi_flowline_coords(j: Any) -> np.ndarray | None:
    """Merge NLDI flowline GeoJSON (FeatureCollection or single Feature;
    LineString / MultiLineString) into an ordered (n x 2) lon/lat array
    (columns: lon, lat). None if none."""
    feats = _geojson_features(j)
    if not feats:
        return None
    segs: list = []
    for f in feats:
        g = (_or(f, {})).get("geometry")
        if g is None or g.get("type") is None:
            continue
        if g["type"] == "LineString":
            segs.append(g.get("coordinates"))
        elif g["type"] == "MultiLineString":
            for ls in _or(g.get("coordinates"), []):
                segs.append(ls)
    if not segs:
        return None
    pts = [[float(pt[0]), float(pt[1])] for seg in segs for pt in _or(seg, [])]
    if not pts:
        return None
    return np.array(pts, dtype=float)


def parse_nldi_basin_ring(j: Any) -> np.ndarray | None:
    """Parse an NLDI basin GeoJSON into the outer ring ((n x 2) lon/lat array)
    of its polygon."""
    feats = _geojson_features(j)
    if not feats:
        return None
    g = (_or(feats[0], {})).get("geometry")
    if g is None or g.get("type") is None:
        return None
    if g["type"] == "Polygon":
        coords = g["coordinates"][0]  # like R g$coordinates[[1]], raises when malformed
    elif g["type"] == "MultiPolygon":
        coords = g["coordinates"][0][0]
    else:
        coords = None
    if coords is None:
        return None
    return np.array([[float(pt[0]), float(pt[1])] for pt in coords], dtype=float)


def nldi_basin_sqkm(comid: Any) -> float:
    """Drainage area (sq km) for a COMID from its NLDI basin polygon (spherical
    area). Cached per COMID; NaN on failure."""
    comid = _as_int(comid)
    if comid is None:
        return math.nan
    key = "basin:%d" % comid
    hit = _cache_get(key)
    if hit is not _MISS:
        return hit
    try:
        j = _get_json(
            "%s/comid/%s/basin" % (NLDI_BASE, comid),
            params={"f": "json", "simplified": "true"},
        )
        ring = parse_nldi_basin_ring(j)
        val = math.nan if ring is None else spherical_polygon_area_m2(ring[:, 0], ring[:, 1]) / 1e6
    except Exception:
        val = math.nan
    _cache_set(key, val)
    return val


def nldi_basin_sqkm_many(comids, progress=None) -> np.ndarray:
    """Drainage areas (sq km) for many COMIDs, aligned to input order."""
    comids = list(comids)
    out = np.zeros(len(comids), dtype=float)
    for i, c in enumerate(comids):
        out[i] = nldi_basin_sqkm(c)
        if callable(progress):
            progress(i + 1, len(comids))
    return out


def nldi_reach(lat: Any, lon: Any, length_ft: float = 1000) -> dict | None:
    """Snap a point to the NHD network and return its reach geometry + identity.

    Returns ``{"coords_lonlat": ndarray, "comid": int, "da_sqkm": float,
    "gnis_name": str | None, "snap_lonlat": (lon, lat)}`` or None. Drainage
    area is best-effort (NLDI often omits it; the site table supplies it from
    StreamCAT). Uses the COMID's own flowline geometry as the reach.

    NOTE(parity): ``length_ft`` is accepted but unused, exactly like the R
    function.
    """
    lon = _as_float(lon)
    lat = _as_float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    try:
        pos = _get_json(
            NLDI_BASE + "/comid/position",
            params={"coords": "POINT(%f %f)" % (lon, lat), "f": "json"},
        )
        comid = parse_nldi_comid(pos)
        if comid is None:
            return None
        feat = _get_json("%s/comid/%s" % (NLDI_BASE, comid), params={"f": "json"})
        coords = parse_nldi_flowline_coords(feat)
        if coords is None:
            return None
        # NOTE(parity): like R feat$features[[1]], a bare-Feature response (no
        # "features" key) errors here and the whole lookup returns None.
        props = _or(feat["features"][0].get("properties"), {})
        da_sqkm = _as_float(_or(props.get("totdasqkm"), props.get("TotDASqKM")))
        gnis = _or(props.get("gnis_name"), props.get("GNIS_NAME"))  # None == R NA
        return {
            "coords_lonlat": coords,
            "comid": comid,
            "da_sqkm": da_sqkm,
            "gnis_name": gnis,
            "snap_lonlat": (lon, lat),
        }
    except Exception:
        return None
