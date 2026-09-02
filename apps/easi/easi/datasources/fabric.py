"""NHDPlus V2 flowlines from the USGS fabric API (OGC API Features).

Replaces pynhd's ``WaterData("nhdflowline_network")`` reads of the GeoServer
WFS at ``api.water.usgs.gov/geoserver/wmadata/ows``, which USGS is retiring in
favor of ``api.water.usgs.gov/fabric/pygeoapi`` (the same NHDPlus V2
hydrography as GeoJSON with stable collection ids). Two reads: the flowlines
in a bounding box (the map's V2 layer and click snapping) and one COMID's
attributes and geometry (drainage area, name, reach code, slope, fcode,
stream order). NLDI basins and navigation stay on pynhd.

Never raises. ``_get`` retries with a short backoff and returns None when the
service does not answer; callers degrade with recorded reasons.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

ITEMS_URL = ("https://api.water.usgs.gov/fabric/pygeoapi/collections/"
             "nhdflowline_network/items")
# pygeoapi's default page is 10 items; the map asks for the whole viewport.
BBOX_LIMIT = 1000
ATTR_PROPERTIES = "comid,gnis_name,reachcode,totdasqkm,slope,fcode,streamorde,lengthkm"
BBOX_PROPERTIES = "comid,gnis_name"
_BACKOFF_S = (1.0, 3.0)


def _get(params: dict, *, timeout: float, retries: int = 2) -> Optional[dict]:
    """A 200 FeatureCollection as a dict, else None after ``retries`` more tries."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(ITEMS_URL, params={**params, "f": "json"}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                    return data
        except Exception:  # noqa: BLE001 - resilience by design
            pass
        if attempt < retries:
            time.sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
    return None


def features_in_bbox(west: float, south: float, east: float, north: float, *,
                     limit: int = BBOX_LIMIT, timeout: float = 60.0
                     ) -> Optional[list[dict]]:
    """GeoJSON features (comid, gnis_name, geometry) in the bbox, or None on failure."""
    data = _get({"bbox": f"{west},{south},{east},{north}", "limit": int(limit),
                 "properties": BBOX_PROPERTIES}, timeout=timeout)
    if data is None:
        return None
    return [f for f in data.get("features") or [] if isinstance(f, dict)]


def feature_by_comid(comid: int, *, timeout: float = 60.0) -> Optional[dict]:
    """The COMID's feature (attributes + geometry), ``{}`` when the COMID is
    unknown, or None when the service did not answer."""
    data = _get({"comid": int(comid), "limit": 1, "properties": ATTR_PROPERTIES},
                timeout=timeout)
    if data is None:
        return None
    feats = data.get("features") or []
    return feats[0] if feats and isinstance(feats[0], dict) else {}


def _float(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    try:
        return None if v is None else int(float(v))
    except (TypeError, ValueError):
        return None


def attrs_from_feature(feature: Optional[dict]) -> dict[str, Any]:
    """The ``delineation.flowline_attrs`` keys (minus sinuosity) from a feature."""
    out: dict[str, Any] = {"gnis_name": None, "drainage_area_sqkm": None, "huc8": None,
                           "slope": None, "fcode": None, "stream_order": None}
    props = (feature or {}).get("properties") or {}
    name = props.get("gnis_name")
    out["gnis_name"] = (str(name).strip() or None) if name else None
    out["drainage_area_sqkm"] = _float(props.get("totdasqkm"))
    rc = props.get("reachcode")
    out["huc8"] = str(rc)[:8] if rc else None
    slope = _float(props.get("slope"))
    out["slope"] = slope if slope is not None and slope >= 0 else None
    out["fcode"] = _int(props.get("fcode"))
    out["stream_order"] = _int(props.get("streamorde"))
    return out
