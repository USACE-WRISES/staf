"""NHDPlus V2 flowlines for EASI's acquisition code: the STAF data bundle first, else the fabric API.

``fabric`` is one of the frozen scoring method's sources (its feature decoding also scores stored
evidence), so it stays as the method pinned it and the bundle-first reads live here. The bundle's
features have the fabric API's shape (the vendored site engine's ``bundle.v2_feature``: the same
properties and EROM flows, the exact V2 line), so the callers decode both the same way.

Never raises: an answer the bundle cannot give comes from the service, which keeps its own rules
(``fabric``: retries, the feature memo, None for no answer).
"""
from __future__ import annotations

from typing import Optional

from . import fabric


def _bundle():
    """The vendored site engine's STAF data bundle module (its answers are None wherever the
    bundle is off or does not hold the request), or None."""
    try:
        from .._vendor.site_engine import bundle
        return bundle
    except Exception:  # noqa: BLE001 - no engine here: the service answers
        return None


def features_in_bbox(west: float, south: float, east: float, north: float, **kwargs
                     ) -> Optional[list[dict]]:
    """``fabric.features_in_bbox``: the V2 flowlines in the box (comid, gnis_name, geometry), the
    bundle's where it covers the box (every line in it; the service stops at its ``limit``)."""
    b = _bundle()
    try:
        recs = b.v2_lines_in_box(west, south, east, north) if b is not None else None
    except Exception:  # noqa: BLE001 - the service answers instead
        recs = None
    if recs is not None:
        return [{"type": "Feature", "properties": {"comid": r["nhdplusid"], "gnis_name": r.get("gnis_name")},
                 "geometry": r["geometry"]} for r in recs]
    return fabric.features_in_bbox(west, south, east, north, **kwargs)


def feature_by_comid(comid: int, **kwargs) -> Optional[dict]:
    """``fabric.feature_by_comid``: the COMID's feature (attributes and geometry), the bundle's
    where it holds the COMID."""
    b = _bundle()
    try:
        local = b.v2_feature(int(comid)) if b is not None else None
    except Exception:  # noqa: BLE001 - the service answers instead
        local = None
    return local if local is not None else fabric.feature_by_comid(comid, **kwargs)
