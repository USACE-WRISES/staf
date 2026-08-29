"""NHDPlus HR service client for the site engine.

Flowline attributes/geometry, upstream-tree parent queries (``dnhydroseq``),
and catchment polygons, all against the NHDPlus_HR MapServer. Self-contained
(the engine is vendored into apps that must not import ``libs/`` at runtime);
``parse_feature`` keeps identical semantics to EASI's
``easi/datasources/nhd_hr.py`` and a parity test guards that when the EASI
source tree is present.

Style contract shared with the STAF datasources: helpers never raise — they
return ``None`` (or empty lists) on failure, and callers degrade with recorded
reasons. Results are cached in-process where re-use is likely.
"""
from __future__ import annotations

import functools
import time
from typing import Any, Iterable, Optional

import requests

_BASE = "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer"
FLOWLINE_QUERY_URL = f"{_BASE}/3/query"
CATCHMENT_QUERY_URL = f"{_BASE}/10/query"
SLOPE_SENTINEL = -9998.0

_ATTR_FIELDS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm",
                "slope", "fcode", "ftype", "streamorde", "hydroseq",
                "uphydroseq", "dnhydroseq", "vpuid", "innetwork", "qama")
# Batched IN-clause size: keeps the GET URL well under server limits.
_CHUNK = 40


def _request(url: str, params: dict, timeout: float, retries: int = 1
             ) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "error" not in data:
                    return data
        except Exception:  # noqa: BLE001 - resilience by design
            pass
        time.sleep(0.5 * (attempt + 1))
    return None


def _exceeded(payload: Optional[dict]) -> bool:
    if not payload:
        return False
    if payload.get("exceededTransferLimit"):
        return True
    props = payload.get("properties")
    return bool(isinstance(props, dict) and props.get("exceededTransferLimit"))


def _int_id(value: Any) -> Optional[int]:
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def parse_feature(feature: Optional[dict]) -> Optional[dict]:
    """Typed attribute dict from one HR GeoJSON feature, or None.

    Sentinel guards: slope None when negative (covers -9998); drainage area
    None when non-positive. Semantics parity-tested against EASI's copy.
    """
    if not feature:
        return None
    props = feature.get("properties") or {}
    nid = _int_id(props.get("nhdplusid"))
    if nid is None:
        return None

    def _f(key):
        v = props.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _i(key):
        v = props.get(key)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    slope = _f("slope")
    da = _f("totdasqkm")
    name = props.get("gnis_name")
    return {
        "nhdplusid": nid,
        "gnis_name": (str(name).strip() or None) if name else None,
        "reachcode": str(props["reachcode"]) if props.get("reachcode") else None,
        "lengthkm": _f("lengthkm"),
        "totdasqkm": da if da is not None and da > 0 else None,
        "slope": slope if slope is not None and slope >= 0 else None,
        "fcode": _i("fcode"),
        "ftype": _i("ftype"),
        "stream_order": _i("streamorde"),
        "hydroseq": _int_id(props.get("hydroseq")),
        "uphydroseq": _int_id(props.get("uphydroseq")),
        "dnhydroseq": _int_id(props.get("dnhydroseq")),
        "vpuid": str(props["vpuid"]) if props.get("vpuid") else None,
        # Engine extra (not in the EASI copy): EROM mean-annual flow, cfs.
        "qama": (_f("qama") if _f("qama") is not None and _f("qama") >= 0
                 else None),
        "geometry": feature.get("geometry"),
    }


def _round_bbox(west, south, east, north, ndigits=3):
    return (round(west, ndigits), round(south, ndigits),
            round(east, ndigits), round(north, ndigits))


@functools.lru_cache(maxsize=32)
def _fetch_bbox(west: float, south: float, east: float, north: float
                ) -> Optional[tuple]:
    data = _request(FLOWLINE_QUERY_URL, {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "where": "innetwork=1",
        "outFields": ",".join(_ATTR_FIELDS), "returnGeometry": "true",
        "outSR": "4326", "f": "geojson"}, timeout=30.0)
    if data is None or _exceeded(data):
        return None
    recs = [parse_feature(f) for f in data.get("features") or []]
    return tuple(r for r in recs if r and r.get("geometry")) or None


def flowlines_in_bbox(west: float, south: float, east: float, north: float,
                      *, max_area_deg2: float = 0.02) -> list[dict]:
    """Parsed HR flowline records (attrs + geometry) for a bbox; [] on failure."""
    west, east = min(west, east), max(west, east)
    south, north = min(south, north), max(south, north)
    if west == east or south == north:
        return []
    if (east - west) * (north - south) > max_area_deg2:
        return []
    recs = _fetch_bbox(*_round_bbox(west, south, east, north))
    return list(recs) if recs else []


def flowline_by_id(nhdplusid: int, timeout: float = 30.0) -> Optional[dict]:
    nid = _int_id(nhdplusid)
    if nid is None:
        return None
    data = _request(FLOWLINE_QUERY_URL, {
        "where": f"nhdplusid = {nid}", "outFields": ",".join(_ATTR_FIELDS),
        "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
        timeout=timeout, retries=2)
    feats = (data or {}).get("features") or []
    return parse_feature(feats[0]) if feats else None


def feature_by_hydroseq(hydroseq: int, timeout: float = 30.0) -> Optional[dict]:
    hs = _int_id(hydroseq)
    if hs is None:
        return None
    data = _request(FLOWLINE_QUERY_URL, {
        "where": f"hydroseq = {hs}", "outFields": ",".join(_ATTR_FIELDS),
        "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
        timeout=timeout)
    feats = (data or {}).get("features") or []
    if len(feats) != 1:
        return None
    return parse_feature(feats[0])


def _chunks(values: Iterable[int], size: int = _CHUNK):
    buf: list[int] = []
    for v in values:
        buf.append(int(v))
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def _chunk_query(url: str, params: dict, timeout: float,
                 escalated_timeout: float) -> Optional[dict]:
    """One batched chunk with a second, longer-timeout pass before giving up.

    The 2026-08-29 acceptance panel lost 5 of 14 sites to single chunk
    timeouts on multi-hop walks while every completed union agreed exactly
    with the published area, so failures here are worth real patience. A
    chunk that exhausts both passes still fails the whole call — the caller's
    invariant is a complete tree or None, never a silently partial one.
    """
    data = _request(url, params, timeout=timeout, retries=2)
    if data is None:
        data = _request(url, params, timeout=escalated_timeout, retries=1)
    if data is None or _exceeded(data):
        return None
    return data


def parents_by_dnhydroseq(hydroseqs: list[int], *, with_geometry: bool = True,
                          timeout: float = 60.0,
                          escalated_timeout: float = 120.0
                          ) -> Optional[list[dict]]:
    """All reaches whose downstream hydroseq is in ``hydroseqs`` (one BFS level).

    Includes tributaries and divergences, which is what the upstream TREE walk
    needs. Returns None on any chunk failure (the caller must treat the tree
    as incomplete, never silently partial).
    """
    out: list[dict] = []
    for chunk in _chunks(hydroseqs):
        where = "dnhydroseq IN (" + ",".join(str(x) for x in chunk) + ")"
        data = _chunk_query(FLOWLINE_QUERY_URL, {
            "where": where, "outFields": ",".join(_ATTR_FIELDS),
            "returnGeometry": str(with_geometry).lower(), "outSR": "4326",
            "f": "geojson"}, timeout, escalated_timeout)
        if data is None:
            return None
        for f in data.get("features") or []:
            rec = parse_feature(f)
            if rec:
                out.append(rec)
    return out


def catchments_by_ids(nhdplusids: list[int], timeout: float = 90.0,
                      escalated_timeout: float = 180.0
                      ) -> Optional[list[dict]]:
    """Catchment polygons for the given reach ids.

    Returns ``[{"nhdplusid", "areasqkm", "geometry"}]`` or None on any chunk
    failure. A reach with no catchment simply has no row (zero-area sliver
    reaches exist in the HR fabric).
    """
    out: list[dict] = []
    for chunk in _chunks(nhdplusids):
        where = "nhdplusid IN (" + ",".join(str(x) for x in chunk) + ")"
        data = _chunk_query(CATCHMENT_QUERY_URL, {
            "where": where, "outFields": "nhdplusid,areasqkm",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson"},
            timeout, escalated_timeout)
        if data is None:
            return None
        for f in data.get("features") or []:
            props = f.get("properties") or {}
            nid = _int_id(props.get("nhdplusid"))
            if nid is None or not f.get("geometry"):
                continue
            try:
                area = float(props.get("areasqkm"))
            except (TypeError, ValueError):
                area = None
            out.append({"nhdplusid": nid, "areasqkm": area,
                        "geometry": f["geometry"]})
    return out
