"""Site context (Layer A): point -> anchored HR reach -> attributes.

Snap the input point to the nearest HR flowline, read the reach's VAAs,
compute sinuosity from its geometry, and resolve the EPA Level III ecoregion
from the bundled polygon set (synced into ``_extracted/data`` from EASI, the
canonical source). Never raises.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from typing import Optional

from . import hr
from .geometry import line_sinuosity, nearest_point_on_records

SNAP_TOL_FT = 150.0
_PROBE_HALF_DEG = 0.012
_ECOREGIONS_PATH = Path(__file__).parent / "_extracted" / "data" / "ecoregions_l3.geojson"


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


@functools.lru_cache(maxsize=1)
def _ecoregion_index() -> list:
    """``(code, name, prepared_geom, bounds)`` rows; [] when the data or the
    geo stack is unavailable. Port of ``easi/geo.py``'s cached index."""
    from shapely.geometry import shape
    from shapely.prepared import prep

    if not _ECOREGIONS_PATH.exists():
        return []
    try:
        fc = json.loads(_ECOREGIONS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - resilience by design
        return []
    out = []
    for f in (fc.get("features") or []):
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty:
            continue
        props = f.get("properties") or {}
        out.append((props.get("US_L3CODE"), props.get("US_L3NAME"),
                    prep(g), g.bounds))
    return out


def ecoregion_l3_at(lat, lon) -> Optional[dict]:
    """EPA Level III ecoregion covering the point, ``{"code", "name"}`` or None."""
    if not _finite(lat) or not _finite(lon):
        return None
    try:
        from shapely.geometry import Point
        pt = Point(float(lon), float(lat))
        for code, name, pg, (minx, miny, maxx, maxy) in _ecoregion_index():
            if float(lon) < minx or float(lon) > maxx:
                continue
            if float(lat) < miny or float(lat) > maxy:
                continue
            if pg.covers(pt):
                return {"code": None if code is None else str(code),
                        "name": name or ""}
    except Exception:  # noqa: BLE001
        pass
    return None


def site_context(lat: float, lon: float, *, snap_tol_ft: float = SNAP_TOL_FT
                 ) -> dict:
    """Anchor the point on the HR network and assemble the site block.

    Returns ``{"status": "ok"|"failed", "reason", "anchor": <parsed rec>,
    "site": {...}}`` where ``site`` is the camelCase SiteComputation block.
    """
    records = hr.flowlines_in_bbox(
        lon - _PROBE_HALF_DEG, lat - _PROBE_HALF_DEG,
        lon + _PROBE_HALF_DEG, lat + _PROBE_HALF_DEG)
    snap = nearest_point_on_records(records, lat, lon)
    if snap is None:
        return {"status": "failed", "reason": "no HR flowline near the point",
                "anchor": None, "site": None}
    s_lat, s_lon, dist_ft, nid = snap
    if dist_ft > snap_tol_ft:
        return {"status": "failed",
                "reason": (f"nearest HR flowline is {dist_ft:.0f} ft away "
                           f"(tolerance {snap_tol_ft:.0f} ft)"),
                "anchor": None, "site": None}
    anchor = next((r for r in records if r.get("nhdplusid") == nid), None)
    if anchor is None:
        return {"status": "failed", "reason": "anchored reach not resolvable",
                "anchor": None, "site": None}
    sinuosity = None
    if anchor.get("geometry"):
        try:
            from shapely.geometry import shape
            sinuosity = line_sinuosity(shape(anchor["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    site = {
        "network": "nhdplus-hr",
        "nhdplusId": anchor["nhdplusid"],
        "gnisName": anchor.get("gnis_name"),
        "reachcode": anchor.get("reachcode"),
        "vpuid": anchor.get("vpuid"),
        "snapLat": round(s_lat, 6), "snapLon": round(s_lon, 6),
        "snapDistFt": round(dist_ft, 1),
        "drainageAreaSqkm": anchor.get("totdasqkm"),
        "slope": anchor.get("slope"),
        "streamOrder": anchor.get("stream_order"),
        "fcode": anchor.get("fcode"),
        "sinuosity": sinuosity,
        "eromQamaCfs": anchor.get("qama"),
        "ecoregionL3": ecoregion_l3_at(s_lat, s_lon),
    }
    return {"status": "ok", "reason": None, "anchor": anchor, "site": site}
