"""The NHD stream network for the map and the click.

DEEP draws the full high-resolution NHD from the vendored STAF site engine's
HR client, split by the click rule into the reaches the StreamCat lookup
engine covers and every other stream (``network_display``), and snaps every
click to the HR line. The STAF site engine computes the HR reach watershed
for any stream; the StreamCat reach for the point (the V2 line under the
click, else the nearest StreamCat reach downstream) is ``comid_anchor``'s job
and never blocks the pick (2026-09-07). A thin adapter over the vendored
engine; the same file lives in SFARI. Never raises.
"""
from __future__ import annotations

from typing import Optional

from . import engine_prefill

HR_PROBE_HALF_DEG = 0.012


def _engine():
    from deep._vendor.site_engine import anchor, hr
    return anchor, hr


def hr_available() -> bool:
    return engine_prefill.site_engine_available()


def hr_records_to_geojson(records: list[dict]) -> Optional[dict]:
    """The engine's parsed HR records as an id-only FeatureCollection."""
    feats = [{"type": "Feature", "properties": {"nhdplusid": r.get("nhdplusid")},
              "geometry": r.get("geometry")} for r in (records or []) if r.get("geometry")]
    return {"type": "FeatureCollection", "features": feats} if feats else None


def hr_flowlines_status(west: float, south: float, east: float, north: float
                        ) -> tuple[str, Optional[dict]]:
    """``(status, FeatureCollection | None)`` for the view bbox under the map's
    fast-fail policy: ``ok``, ``empty``, ``truncated``, ``too-large`` or
    ``failed`` (no answer, or no engine here). The engine client caches only
    answers, so a failed box is asked again next time."""
    if not hr_available():
        return "failed", None
    try:
        _anchor, hr = _engine()
        status, records = hr.flowlines_in_bbox_status(west, south, east, north,
                                                      fast_fail=True)
    except Exception:  # noqa: BLE001
        return "failed", None
    if status != "ok":
        return status, None
    fc = hr_records_to_geojson(records)
    return ("ok", fc) if fc else ("empty", None)


def snap_status(lat: float, lon: float
                ) -> tuple[str, Optional[tuple[float, float, float, Optional[int]]]]:
    """``(status, hit)`` for a pick: the HR probe box's status under the map's
    fast-fail policy and the nearest HR line, ``(snap_lat, snap_lon, dist_ft,
    nhdplusid)`` or None. ``failed`` (no answer, or no engine here) is never
    the same fact as "no stream nearby". The math is ``anchor.hr_snap``'s,
    which the engine's own anchoring keeps using."""
    if not hr_available():
        return "failed", None
    try:
        anchor, hr = _engine()
        d = HR_PROBE_HALF_DEG
        status, records = hr.flowlines_in_bbox_status(lon - d, lat - d, lon + d, lat + d,
                                                      fast_fail=True)
        if status != "ok":
            return status, None
        return status, anchor.nearest_point_on_records(records, lat, lon)
    except Exception:  # noqa: BLE001
        return "failed", None


def snap_hr(lat: float, lon: float
            ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Nearest HR flowline: ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` or None."""
    return snap_status(lat, lon)[1]


def snap_point(lat: float, lon: float) -> dict:
    """The click or typed point snapped to the NHD (worker-thread helper for
    the app): ``{"hit": (snap_lat, snap_lon, dist_ft, nhdplusid) | None,
    "lat", "lon", "hrStatus"}``; the app says the service failed, not "no
    stream", when ``hrStatus`` is ``failed``."""
    status, hit = snap_status(lat, lon)
    return {"hit": hit, "lat": lat, "lon": lon, "hrStatus": status}
