"""Region outline pictures for the start page's recent projects and the assessment gallery.

An inline SVG of the region's outline (an EPA Level III ecoregion or a US state, from the same
bundled GeoJSON the wizard and publish use, geo.region_polygon_geometry) filled in the app's
accent family on its soft tile, with a faint graticule so the shape reads as a map. Pure
Python, cached per region: the gallery renders dozens of them.
"""
from __future__ import annotations

import math
from functools import lru_cache

from . import geo

_BG = "#eef3fb"
_FILL = "#c8d8f0"
_STROKE = "#2f4b7c"
_GRID = "#dbe5f3"


def _rings(geometry: dict | None) -> list[list[list[float]]]:
    if not geometry:
        return []
    t = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if t == "Polygon":
        return [list(r) for r in coords]
    if t == "MultiPolygon":
        return [list(r) for poly in coords for r in poly]
    return []


def placeholder_svg(width: int = 320, height: int = 200) -> str:
    """The tile when a region has no outline (a drawn area, an unknown code)."""
    return (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'aria-hidden="true" preserveAspectRatio="xMidYMid slice">'
            f'<rect width="{width}" height="{height}" fill="{_BG}"/>'
            f'<path d="M{width*.12:.0f} {height*.78:.0f} C {width*.42:.0f} {height*.78:.0f} '
            f'{width*.46:.0f} {height*.26:.0f} {width*.88:.0f} {height*.24:.0f}" fill="none" '
            f'stroke="{_STROKE}" stroke-width="5" stroke-linecap="round" opacity=".55"/>'
            f'<circle cx="{width*.55:.0f}" cy="{height*.46:.0f}" r="8" fill="#ff9500"/></svg>')


@lru_cache(maxsize=256)
def _outline(kind: str, code: str, width: int, height: int) -> str | None:
    try:
        geom = geo.region_polygon_geometry(kind, code)
    except Exception:  # noqa: BLE001 - a picture is never worth an error
        return None
    rings = _rings(geom)
    if not rings:
        return None
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    lon0, lon1, lat0, lat1 = min(xs), max(xs), min(ys), max(ys)
    k = math.cos(math.radians((lat0 + lat1) / 2)) or 1.0     # equirectangular aspect
    w_deg = max((lon1 - lon0) * k, 1e-6)
    h_deg = max(lat1 - lat0, 1e-6)
    pad = 0.14
    scale = min(width * (1 - 2 * pad) / w_deg, height * (1 - 2 * pad) / h_deg)
    ox = (width - w_deg * scale) / 2
    oy = (height - h_deg * scale) / 2

    def xy(p):
        return ox + (p[0] - lon0) * k * scale, oy + (lat1 - p[1]) * scale

    parts = []
    for r in rings:
        pts = [xy(p) for p in r]
        if len(pts) < 3:
            continue
        parts.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z")
    if not parts:
        return None
    grid = []
    step = width / 8
    for i in range(1, 8):
        grid.append(f'<path d="M{i*step:.0f} 0 V{height}" stroke="{_GRID}" stroke-width="1"/>')
    for i in range(1, 5):
        y = i * height / 5
        grid.append(f'<path d="M0 {y:.0f} H{width}" stroke="{_GRID}" stroke-width="1"/>')
    return (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
            f'aria-hidden="true" preserveAspectRatio="xMidYMid meet">'
            f'<rect width="{width}" height="{height}" fill="{_BG}"/>{"".join(grid)}'
            f'<path d="{" ".join(parts)}" fill="{_FILL}" fill-rule="evenodd" stroke="{_STROKE}" '
            f'stroke-width="2" stroke-linejoin="round"/></svg>')


def outline_svg(region: dict | None, *, width: int = 320, height: int = 200) -> str:
    """The region's outline picture, or the placeholder tile."""
    region = region or {}
    kind = str(region.get("kind") or "")
    code = str(region.get("code") or "")
    if kind in ("ecoregion", "state") and code:
        svg = _outline(kind, code, int(width), int(height))
        if svg:
            return svg
    return placeholder_svg(width, height)


__all__ = ["outline_svg", "placeholder_svg"]
