"""Any NHD stream: the HR display layer, click classification, and anchoring.

DEEP draws the NHDPlus V2 network (clickable, StreamCat data available) and,
under it, the full high-resolution NHD from the vendored STAF site engine's
HR client. A click on a V2 line is a covered site. A click on an HR-only
line is anchored with the engine's shared classification: the exact
watershed comes from the engine and any COMID-keyed evidence (StreamCat,
NRSA) is labeled with the nearest covered reach downstream, or withheld past
the drainage-area bound. Everything here is a thin adapter over the vendored
engine; the same file lives in SFARI. Never raises.
"""
from __future__ import annotations

from typing import Optional

from . import engine_prefill

HR_PROBE_HALF_DEG = 0.012


def _engine():
    from deep._vendor.site_engine import anchor, geometry, hr
    return anchor, geometry, hr


def hr_available() -> bool:
    return engine_prefill.site_engine_available()


def hr_records_to_geojson(records: list[dict]) -> Optional[dict]:
    """The engine's parsed HR records as an id-only FeatureCollection."""
    feats = [{"type": "Feature", "properties": {"nhdplusid": r.get("nhdplusid")},
              "geometry": r.get("geometry")} for r in (records or []) if r.get("geometry")]
    return {"type": "FeatureCollection", "features": feats} if feats else None


def hr_flowlines_fc(west: float, south: float, east: float, north: float
                    ) -> Optional[dict]:
    """HR flowlines for the view bbox (the engine client caches the bbox)."""
    if not hr_available():
        return None
    try:
        _a, _g, hr = _engine()
        return hr_records_to_geojson(hr.flowlines_in_bbox(west, south, east, north))
    except Exception:  # noqa: BLE001
        return None


def snap_hr(lat: float, lon: float
            ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Nearest HR flowline: ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` or None."""
    if not hr_available():
        return None
    try:
        anchor, _g, _hr = _engine()
        return anchor.hr_snap(lat, lon, half_deg=HR_PROBE_HALF_DEG)
    except Exception:  # noqa: BLE001
        return None


def snap_both(lat: float, lon: float, *, snap_tol_ft: float = 150.0) -> dict:
    """V2 first, then HR (port of EASI's click flow). Worker-thread helper."""
    from .datasources import flowlines
    d = HR_PROBE_HALF_DEG
    hit = flowlines.nearest_point_on_lines(
        flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d), lat, lon)
    if hit and hit[2] <= snap_tol_ft:
        return {"hit": hit, "lat": lat, "lon": lon}
    return {"hit": hit, "hrHit": snap_hr(lat, lon), "lat": lat, "lon": lon}


def route_from_hr(lat: float, lon: float, hr_hit: tuple) -> dict:
    """Anchor an HR-only click: ``{"anchor"}`` or ``{"error", "detail"}``."""
    try:
        anchor, _g, _hr = _engine()
        return anchor.route_from_hr(lat, lon, tuple(hr_hit))
    except Exception as exc:  # noqa: BLE001
        return {"error": "snap_service_error", "detail": str(exc)}


def v2_anchor(comid: int, lat: float, lon: float, snap_lat=None, snap_lon=None,
              snap_dist_ft=None) -> dict:
    try:
        anchor, _g, _hr = _engine()
        return anchor.v2_anchor(comid, lat, lon, snap_lat, snap_lon, snap_dist_ft)
    except Exception:  # noqa: BLE001 - vendored engine absent: a minimal payload
        return {"anchorSchemaVersion": 1, "anchorKind": "v2Direct",
                "clickedPoint": {"lat": lat, "lon": lon},
                "scoredReach": {"network": "nhdplus-v2", "comid": int(comid),
                                "gnisName": None, "drainageAreaSqkm": None,
                                "snapLat": snap_lat, "snapLon": snap_lon,
                                "snapDistFt": snap_dist_ft},
                "notes": []}


def anchor_label(anchor: Optional[dict]) -> str:
    return engine_prefill.anchor_label(anchor)


def declined(anchor: Optional[dict]) -> bool:
    return bool(((anchor or {}).get("routing") or {}).get("declined"))


def clicked_reach(anchor: Optional[dict]) -> dict:
    """The HR stream that was clicked (``clickedStream`` in the shared payload):
    ``nhdplusId``, ``gnisName``, ``drainageAreaSqkm``, ``snapLat``, ``snapLon``,
    ``snapDistFt``. Empty on a covered click."""
    return dict((anchor or {}).get("clickedStream") or {})
