"""Which stream vectors the map needs, and when a fetch is due.

The map fetches the two stream networks (NHDPlus V2 and NHDPlus HR) for a
box around the view center. Until 2026-09-02 the box was square in degrees
(so it reached only about 100 px past the side edges of a wide viewport) and
a fetch fired whenever the rounded box changed at all, which a pan of a few
dozen pixels does. Now the box is wider than it is tall from zoom 15 up, and
a fetch is due only when the viewport is no longer inside the box last
fetched. Pure functions, no widget access, so they are tested offline.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

#: Half-height of the fetch box in degrees of latitude at zoom 15; it halves
#: with every zoom level in, like the viewport, and is capped at 0.08.
HALF_LAT_AT_15 = 0.03
HALF_CAP = 0.08
#: From this zoom the box is twice as wide as it is tall (degrees), so the
#: sideways margin matches the viewport's shape. Below it the box stays
#: square so the HR service's area cap (0.02 deg2) holds at zoom 14.
WIDE_FROM_ZOOM = 15
#: Rounding of the box edges, outward, so the datasource caches key stably
#: and the box never shrinks below what was asked for.
ROUND_TO = 3

BBox = tuple[float, float, float, float]      # (west, south, east, north)
View = tuple[float, float, float, float]      # (south, west, north, east)


def _floor(x: float) -> float:
    f = 10 ** ROUND_TO
    return math.floor(x * f + 1e-9) / f


def _ceil(x: float) -> float:
    f = 10 ** ROUND_TO
    return math.ceil(x * f - 1e-9) / f


def half_sizes(zoom: float) -> tuple[float, float]:
    """``(half_lon, half_lat)`` of the fetch box in degrees for ``zoom``."""
    half_lat = min(HALF_CAP, HALF_LAT_AT_15 * (2 ** (15 - float(zoom))))
    half_lon = half_lat * (2.0 if zoom >= WIDE_FROM_ZOOM else 1.0)
    return half_lon, half_lat


def fetch_box(lat: float, lon: float, zoom: float) -> BBox:
    """The padded box to fetch around the view center, rounded outward."""
    half_lon, half_lat = half_sizes(zoom)
    return (_floor(lon - half_lon), _floor(lat - half_lat),
            _ceil(lon + half_lon), _ceil(lat + half_lat))


def area_deg2(box: BBox) -> float:
    w, s, e, n = box
    return max(0.0, e - w) * max(0.0, n - s)


def view_from_bounds(bounds) -> Optional[View]:
    """``(south, west, north, east)`` from the map's ``bounds`` trait
    (``((south, west), (north, east))``), or None when it is unusable."""
    try:
        (s, w), (n, e) = bounds
        s, w, n, e = float(s), float(w), float(n), float(e)
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (s, w, n, e))) or n < s or e < w:
        return None
    return s, w, n, e


def needs_fetch(view: Optional[Sequence[float]], fetched: Optional[Sequence[float]]) -> bool:
    """True when nothing was fetched yet or the viewport is not inside the
    last fetched box. A view that is unknown fetches (better one fetch than a
    map with no lines); a zoom in stays inside and does not."""
    if fetched is None or view is None:
        return True
    s, w, n, e = view
    fw, fs, fe, fn = fetched
    return not (w >= fw and e <= fe and s >= fs and n <= fn)
