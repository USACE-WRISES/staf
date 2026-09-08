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


def hr_flowlines_fc(west: float, south: float, east: float, north: float
                    ) -> Optional[dict]:
    """HR flowlines for the view bbox (the engine client caches the bbox)."""
    if not hr_available():
        return None
    try:
        _anchor, hr = _engine()
        return hr_records_to_geojson(hr.flowlines_in_bbox(west, south, east, north))
    except Exception:  # noqa: BLE001
        return None


def snap_hr(lat: float, lon: float
            ) -> Optional[tuple[float, float, float, Optional[int]]]:
    """Nearest HR flowline: ``(snap_lat, snap_lon, dist_ft, nhdplusid)`` or None."""
    if not hr_available():
        return None
    try:
        anchor, _hr = _engine()
        return anchor.hr_snap(lat, lon, half_deg=HR_PROBE_HALF_DEG)
    except Exception:  # noqa: BLE001
        return None


def snap_point(lat: float, lon: float) -> dict:
    """The click or typed point snapped to the NHD (worker-thread helper for
    the app): ``{"hit": (snap_lat, snap_lon, dist_ft, nhdplusid) | None,
    "lat", "lon"}``."""
    return {"hit": snap_hr(lat, lon), "lat": lat, "lon": lon}
