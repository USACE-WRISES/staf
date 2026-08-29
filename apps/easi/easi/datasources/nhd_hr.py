"""NHDPlus HR flowline access — map vectors + per-reach VAAs for routing.

EASI scores on the NHDPlus V2 network (the StreamCat universe), but the map
shows the full-resolution NHDPlus HR network so every stream is visible and
clickable. A click on an HR-only stream is routed to the covered network by
``easi.routing``; this module supplies the two HR primitives that flow needs:

- ``hr_flowlines_in_bbox`` — display vectors for the visible bbox (zoom-gated
  by the app; size-guarded + cached here), geometry plus ``nhdplusid`` only.
- ``hr_flowline_by_id`` / ``hr_attrs`` — one reach's value-added attributes
  (drainage area, slope, FCode, name, sinuosity) for the routing banner and
  the Phase 2 re-anchored metrics.

Source: the NetworkNHDFlowline layer of the NHDPlus_HR MapServer
(hydro.nationalmap.gov). Field availability and per-VPU quality are verified
by ``scripts/probe_nhd_hr.py``; load-bearing use cites its output. HR ids are
``nhdplusid`` (a different id space from V2 COMIDs — never mix them). The
service stores ids as float64; they are converted with ``int(round(...))``,
which the probe asserts is stable (all observed ids sit below 2^53).

Like the other datasources: never raises — every helper returns ``None`` (or a
dict with an error note) on failure, and results are cached in-process.
"""
from __future__ import annotations

import functools
import time
from typing import Any, Optional

import requests

from ..batch import diagnostics

HR_QUERY_URL = ("https://hydro.nationalmap.gov/arcgis/rest/services/"
                "NHDPlus_HR/MapServer/3/query")
# HR VAAs use -9998 for "no value"; any negative slope is unusable either way.
SLOPE_SENTINEL = -9998.0

_ID_FIELD = "nhdplusid"
_ATTR_FIELDS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm",
                "slope", "fcode", "ftype", "streamorde", "hydroseq",
                "uphydroseq", "dnhydroseq", "vpuid", "innetwork")


def _request(params: dict, timeout: float, retries: int = 1) -> Optional[dict]:
    """GET against the HR query endpoint with light retry. Never raises.

    Failures are reported to the batch retry side channel (a no-op outside a
    batch run) so the scheduler can classify a partial result as transient.
    """
    for attempt in range(retries + 1):
        try:
            r = requests.get(HR_QUERY_URL, params=params, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                # ArcGIS reports errors inside a 200 payload.
                if isinstance(data, dict) and "error" not in data:
                    return data
                diagnostics.record_response("NHDPlus HR", 500)
            else:
                diagnostics.record_response("NHDPlus HR", r.status_code)
        except Exception as exc:  # noqa: BLE001 - resilience by design
            diagnostics.record_exception("NHDPlus HR", exc)
        time.sleep(0.5 * (attempt + 1))
    return None


def _exceeded(payload: Optional[dict]) -> bool:
    """True when the service truncated the result (2000-record cap)."""
    if not payload:
        return False
    if payload.get("exceededTransferLimit"):
        return True
    props = payload.get("properties")
    return bool(isinstance(props, dict) and props.get("exceededTransferLimit"))


def _int_id(value: Any) -> Optional[int]:
    """nhdplusid/hydroseq float64 -> int, or None. Ids are always positive."""
    try:
        iv = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def _round_bbox(west, south, east, north, ndigits=3):
    return (round(west, ndigits), round(south, ndigits),
            round(east, ndigits), round(north, ndigits))


@functools.lru_cache(maxsize=64)
def _fetch_bbox(west: float, south: float, east: float, north: float) -> Optional[dict]:
    """Cached HR flowline pull for a (rounded) bbox -> id-only GeoJSON."""
    data = _request({
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "where": "innetwork=1",
        "outFields": _ID_FIELD, "returnGeometry": "true",
        "outSR": "4326", "maxAllowableOffset": "0.0001",
        "geometryPrecision": "5", "f": "geojson"}, timeout=20.0)
    if data is None or _exceeded(data):
        # A truncated layer would silently hide streams; better to draw nothing
        # (the HR raster overlay still shows the network) than a partial lie.
        return None
    feats = []
    for f in data.get("features") or []:
        geom = f.get("geometry")
        nid = _int_id((f.get("properties") or {}).get(_ID_FIELD))
        if not geom or nid is None:
            continue
        feats.append({"type": "Feature", "properties": {"nhdplusid": nid},
                      "geometry": geom})
    return {"type": "FeatureCollection", "features": feats} if feats else None


def hr_flowlines_in_bbox(west: float, south: float, east: float, north: float,
                         *, max_area_deg2: float = 0.02) -> Optional[dict]:
    """NHDPlus HR flowline vectors (EPSG:4326 FeatureCollection) for a bbox.

    Returns None for an invalid or too-large bbox, on service failure, and when
    the service truncated the result. Cached on the rounded bbox so pan jitter
    reuses the last result. Mirrors ``flowlines.flowlines_in_bbox``.
    """
    west, east = min(west, east), max(west, east)
    south, north = min(south, north), max(south, north)
    if west == east or south == north:
        return None
    if (east - west) * (north - south) > max_area_deg2:
        return None
    return _fetch_bbox(*_round_bbox(west, south, east, north))


def parse_feature(feature: Optional[dict]) -> Optional[dict]:
    """Typed attribute dict from one HR GeoJSON feature, or None.

    Applies the sentinel guards: slope is None when negative (covers -9998),
    drainage area is None when non-positive. Pure function (no I/O).
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
        "geometry": feature.get("geometry"),
    }


def hr_flowline_by_id(nhdplusid: int, timeout: float = 25.0) -> Optional[dict]:
    """One HR reach's attributes + geometry by nhdplusid, or None."""
    nid = _int_id(nhdplusid)
    if nid is None:
        return None
    data = _request({
        "where": f"nhdplusid = {nid}",
        "outFields": ",".join(_ATTR_FIELDS), "returnGeometry": "true",
        "outSR": "4326", "f": "geojson"}, timeout=timeout, retries=2)
    feats = (data or {}).get("features") or []
    return parse_feature(feats[0]) if feats else None


def _feature_by_hydroseq(hydroseq: int, timeout: float = 25.0) -> Optional[dict]:
    """One HR reach by its hydroseq (the upstream-mainstem walk), or None."""
    hs = _int_id(hydroseq)
    if hs is None:
        return None
    data = _request({
        "where": f"hydroseq = {hs}",
        "outFields": ",".join(_ATTR_FIELDS), "returnGeometry": "true",
        "outSR": "4326", "f": "geojson"}, timeout=timeout)
    feats = (data or {}).get("features") or []
    if len(feats) != 1:  # zero = headwater/top; more = data fault, stop cleanly
        return None
    return parse_feature(feats[0])


def hr_attrs(nhdplusid: int) -> dict:
    """NHDPlus HR attributes shaped like ``delineation.flowline_attrs``.

    Same seven keys (gnis_name, drainage_area_sqkm, huc8, slope, fcode,
    stream_order, sinuosity) so Phase 2 can swap the two without adapter
    changes. HR and V2 share the reach-code system, so huc8 derives the same
    way. Best-effort; never raises.
    """
    out: dict[str, Any] = {"gnis_name": None, "drainage_area_sqkm": None,
                           "huc8": None, "slope": None, "fcode": None,
                           "stream_order": None, "sinuosity": None}
    rec = hr_flowline_by_id(nhdplusid)
    if rec is None:
        out["_hr_error"] = f"no HR flowline for nhdplusid {nhdplusid}"
        return out
    out["gnis_name"] = rec["gnis_name"]
    out["drainage_area_sqkm"] = rec["totdasqkm"]
    out["huc8"] = rec["reachcode"][:8] if rec["reachcode"] else None
    out["slope"] = rec["slope"]
    out["fcode"] = rec["fcode"]
    out["stream_order"] = rec["stream_order"]
    try:
        from shapely.geometry import shape

        from .. import delineation
        if rec.get("geometry"):
            out["sinuosity"] = delineation.line_sinuosity(shape(rec["geometry"]))
    except Exception:  # noqa: BLE001 - context is best-effort
        pass
    return out


def derive_reach_hr(nhdplusid: int, lat: float, lon: float, length_ft: float
                    ) -> tuple[Optional[dict], Optional[float], list[str]]:
    """Assessment reach on the HR network, trimmed upstream of (lat, lon).

    Walks ``uphydroseq`` (the VAA's deterministic upstream-mainstem pointer) to
    collect enough mainstem geometry, then reuses the shared merge/orient/trim
    (``delineation._reach_from_lines``), so V2 and HR reaches are derived by the
    same math. Returns ``(reach_geojson, actual_ft, warnings)``; never raises.
    """
    from shapely.geometry import shape

    from .. import delineation

    warnings: list[str] = []
    try:
        rec = hr_flowline_by_id(nhdplusid)
        if rec is None or not rec.get("geometry"):
            return None, None, [f"no HR flowline geometry for nhdplusid {nhdplusid}"]
        length_km = (length_ft / delineation.FT_PER_M) / 1000.0
        own_len_km = rec.get("lengthkm") or 0.0
        # Same sizing rule as the V2 upstream navigation: clear the anchor
        # reach's own length so upstream segments are present for orientation.
        needed_km = round(max(length_km * 4, own_len_km + length_km) + 0.3, 1)
        geoms = [shape(rec["geometry"])]
        total_km = own_len_km
        up = rec.get("uphydroseq")
        for _ in range(25):                    # deterministic hop cap
            if total_km >= needed_km or not up:
                break
            nxt = _feature_by_hydroseq(up)
            if nxt is None or not nxt.get("geometry"):
                break
            geoms.append(shape(nxt["geometry"]))
            total_km += nxt.get("lengthkm") or 0.0
            up = nxt.get("uphydroseq")
        return delineation._reach_from_lines(geoms, [geoms[0]], lat, lon,
                                             length_ft, warnings)
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return None, None, [f"HR reach derivation failed: {exc}"]


def nearest_point_on_hr_lines(geojson: Optional[dict], lat: float, lon: float
                              ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Snap (lat, lon) to the nearest HR flowline in ``geojson``.

    Returns ``(snap_lat, snap_lon, distance_ft, nhdplusid)`` or None. One snap
    implementation for both networks: this delegates to
    ``flowlines.nearest_point_on_lines`` with the HR id property.
    """
    from . import flowlines
    return flowlines.nearest_point_on_lines(geojson, lat, lon,
                                            id_prop="nhdplusid")
