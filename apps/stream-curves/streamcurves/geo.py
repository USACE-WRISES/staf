"""Port of app/helpers/geo_util.R.

Lightweight geospatial helpers for the import wizard — pure Python over parsed
GeoJSON dicts (NO shapely / sf / terra). Ray-cast point-in-polygon with holes and
a bbox prefilter, spherical polygon area, and haversine distance. Used by the
ecoregion map, the physiographic-division lookup, the state mask, and site dedup.
"""

from __future__ import annotations

import math
import os

import numpy as np

from . import config

# 50 states + DC name -> 2-letter code (R's built-in ``state.abb``/``state.name``
# plus DC, which state_abbr_from_name adds explicitly).
_STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}

# Cache for parsed + indexed GeoJSON (keyed by path::prop), mirroring R's .geo_cache.
_GEO_INDEX_CACHE: dict[str, list[dict]] = {}


# ── reading ──────────────────────────────────────────────────────────────────
def read_geojson_features(path) -> list:
    """Parse a GeoJSON file into its list of raw features (each with ``properties``
    and ``geometry``). Missing file -> empty list."""
    if not os.path.exists(path):
        return []
    fc = config.read_json(path)
    if not isinstance(fc, dict):
        return []
    return fc.get("features") or []


def read_geojson_text(path) -> str:
    """Raw GeoJSON text (for a JS/leaflet consumer). Missing file -> ""."""
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── geometry extraction ──────────────────────────────────────────────────────
def _geom_polygons(geometry) -> list:
    """One feature's geometry -> list of polygons; each polygon = list of rings;
    each ring = list of ``[lon, lat]`` pairs. Handles Polygon and MultiPolygon."""
    if not geometry or "type" not in geometry:
        return []
    coords = geometry.get("coordinates")
    gtype = geometry["type"]
    if gtype == "Polygon":
        rings = [[[float(pt[0]), float(pt[1])] for pt in ring] for ring in coords]
        return [rings]
    if gtype == "MultiPolygon":
        return [
            [[[float(pt[0]), float(pt[1])] for pt in ring] for ring in poly]
            for poly in coords
        ]
    return []


# ── point-in-polygon ─────────────────────────────────────────────────────────
def point_in_ring(x, y, ring) -> bool:
    """Ray-casting point-in-ring test. ``ring`` is a sequence of ``[lon, lat]``
    pairs (closed or not)."""
    n = len(ring)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon_rings(x, y, rings) -> bool:
    """Point in a polygon (first ring = outer, remaining rings = holes)."""
    if not rings:
        return False
    if not point_in_ring(x, y, rings[0]):
        return False
    for h in range(1, len(rings)):
        if point_in_ring(x, y, rings[h]):  # in a hole
            return False
    return True


def prepare_polygon_index(features, prop) -> list[dict]:
    """Build a searchable index: each entry = ``{value, properties, polys, bbox}``
    where ``value`` is ``feature.properties[prop]`` and ``bbox`` enables a fast
    prefilter. Features with no polygon geometry are skipped."""
    out: list[dict] = []
    for f in features:
        polys = _geom_polygons(f.get("geometry"))
        if not polys:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for poly in polys:
            for ring in poly:
                for pt in ring:
                    xs.append(pt[0])
                    ys.append(pt[1])
        props = f.get("properties") or {}
        out.append(
            {
                "value": props.get(prop),
                "properties": props,
                "polys": polys,
                "bbox": {
                    "xmin": min(xs),
                    "ymin": min(ys),
                    "xmax": max(xs),
                    "ymax": max(ys),
                },
            }
        )
    return out


def locate_polygon_property(lon, lat, index, default=None):
    """Return the ``prop`` value of the first feature whose polygon contains
    (lon, lat), else ``default``."""
    if not _finite(lon) or not _finite(lat):
        return default
    for entry in index:
        b = entry["bbox"]
        if lon < b["xmin"] or lon > b["xmax"] or lat < b["ymin"] or lat > b["ymax"]:
            continue
        for poly in entry["polys"]:
            if point_in_polygon_rings(lon, lat, poly):
                return entry["value"]
    return default


def load_polygon_index(path, prop) -> list[dict]:
    """Load + index a GeoJSON file by ``prop``, cached by ``path::prop``."""
    key = f"{path}::{prop}"
    cached = _GEO_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    idx = prepare_polygon_index(read_geojson_features(path), prop)
    _GEO_INDEX_CACHE[key] = idx
    return idx


# ── distance / area ──────────────────────────────────────────────────────────
def spherical_polygon_area_m2(lon, lat) -> float:
    """Approximate area (m^2) of a lon/lat polygon ring on the sphere
    (Chamberlain & Duquette; no projection). Sign-agnostic. The ring may be
    given open — the last vertex wraps to the first."""
    lon = list(lon)
    lat = list(lat)
    n = len(lon)
    if n < 3:
        return 0.0
    radius = 6378137.0
    rad = math.pi / 180.0
    lonr = [v * rad for v in lon]
    latr = [v * rad for v in lat]
    total = 0.0
    for i in range(n):
        j = 0 if i == n - 1 else i + 1
        total += (lonr[j] - lonr[i]) * (2 + math.sin(latr[i]) + math.sin(latr[j]))
    return abs(total * radius * radius / 2.0)


def haversine_m(lon1, lat1, lon2, lat2):
    """Great-circle distance in metres, vectorised over the second point. Returns
    a float for scalar input, else a numpy array."""
    radius = 6371000.0
    rad = math.pi / 180.0
    lon2a = np.asarray(lon2, dtype=float)
    lat2a = np.asarray(lat2, dtype=float)
    dlat = (lat2a - lat1) * rad
    dlon = (lon2a - lon1) * rad
    a = (
        np.sin(dlat / 2.0) ** 2
        + math.cos(lat1 * rad) * np.cos(lat2a * rad) * np.sin(dlon / 2.0) ** 2
    )
    d = 2 * radius * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    if np.ndim(d) == 0:
        return float(d)
    return d


# ── masks ────────────────────────────────────────────────────────────────────
def state_at(lon, lat, states_path):
    """2-letter US state (or DC) for a lat/lon via point-in-polygon on the bundled
    ``us_states.geojson`` (property ``state``). None outside the US / file missing."""
    if not os.path.exists(states_path) or not _finite(lon) or not _finite(lat):
        return None
    idx = load_polygon_index(states_path, "state")
    s = locate_polygon_property(lon, lat, idx, default=None)
    if s is None:
        return None
    return str(s)


def state_abbr_from_name(name):
    """Map a full US state name (e.g. ``Ohio``) to its 2-letter code; None if
    unknown."""
    if name is None or str(name) == "":
        return None
    return _STATE_ABBR.get(str(name).strip().upper())


def bieger_division_at(lon, lat, physio_path, division_abbr=None):
    """Bieger physiographic-division abbreviation for a point, via point-in-polygon
    on ``physio_divisions.geojson`` (property ``DIVISION``). Returns a
    ``division_abbr`` key or ``"USA"`` outside CONUS / unknown.

    NOTE(parity): R read the ``bieger_division_abbr`` table from R/18_geomorph.R
    (not part of this port's module set). Pass the DIVISION-name -> abbr mapping as
    ``division_abbr``; when it is None or the name is unmapped this returns "USA"
    exactly as R does for an unknown division."""
    if not os.path.exists(physio_path):
        return "USA"
    idx = load_polygon_index(physio_path, "DIVISION")
    div_name = locate_polygon_property(lon, lat, idx, default=None)
    if div_name is None:
        return "USA"
    if division_abbr is None:
        return "USA"
    abbr = division_abbr.get(str(div_name).strip().upper())
    return "USA" if abbr is None else abbr


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# ── region-of-applicability polygon (for the DEEP "available assessments" layer) ──
# A published library assessment carries its region outline so DEEP can shade it on
# the map. We resolve the outline from the same bundled GeoJSONs the import wizard
# uses (ecoregion US_L3CODE / state abbr) and simplify it (Ramer-Douglas-Peucker,
# pure Python to honor this module's no-shapely rule) so the inlined polygon stays
# small in the bundle. ~500 m tolerance is well within a shaded regional overlay.
_REGION_SIMPLIFY_EPS = 0.005   # degrees (~500 m)
_REGION_COORD_PRECISION = 4    # decimal places (~11 m)


def _rdp(points: list, eps: float) -> list:
    """Ramer-Douglas-Peucker on a list of ``[lon, lat]`` points (iterative, so a huge
    ring can't overflow the recursion stack). Keeps endpoints; drops points closer
    than ``eps`` to the running chord."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        x0, y0 = points[i0]
        x1, y1 = points[i1]
        dx, dy = x1 - x0, y1 - y0
        seg = math.hypot(dx, dy) or 1e-12
        dmax, idx = 0.0, -1
        for i in range(i0 + 1, i1):
            x, y = points[i]
            d = abs(dx * (y0 - y) - (x0 - x) * dy) / seg
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [points[i] for i in range(n) if keep[i]]


def _simplify_ring(ring: list) -> list:
    """Simplify + round one ring; return the original if it over-simplifies (< 4 pts,
    which can't form a closed polygon)."""
    r = _rdp(ring, _REGION_SIMPLIFY_EPS)
    if len(r) >= 3 and r[0] != r[-1]:
        r = r + [r[0]]
    if len(r) < 4:
        r = list(ring)
    p = _REGION_COORD_PRECISION
    return [[round(float(x), p), round(float(y), p)] for x, y in r]


def region_polygon_geometry(kind, code):
    """GeoJSON geometry (``Polygon``/``MultiPolygon``) outlining an EPA Level III
    ecoregion (``US_L3CODE``) or a US state (2-letter ``state``), simplified for a
    lightweight map overlay. ``None`` for other kinds or when the code isn't found."""
    from .paths import DATA_DIR

    if kind == "ecoregion":
        path, prop = DATA_DIR / "ecoregions_l3.geojson", "US_L3CODE"
    elif kind == "state":
        path, prop = DATA_DIR / "us_states.geojson", "state"
    else:
        return None
    code = str(code)
    polys: list = []
    for f in read_geojson_features(path):
        if str((f.get("properties") or {}).get(prop)) != code:
            continue
        polys.extend(_geom_polygons(f.get("geometry")))
    simplified = []
    for poly in polys:
        rings = [_simplify_ring(ring) for ring in poly if len(ring) >= 3]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            simplified.append(rings)
    if not simplified:
        return None
    if len(simplified) == 1:
        return {"type": "Polygon", "coordinates": simplified[0]}
    return {"type": "MultiPolygon", "coordinates": simplified}


def region_with_polygon(region: dict) -> dict:
    """Return a copy of ``region`` with a ``polygon`` GeoJSON geometry attached when it
    can be resolved (ecoregion/state) and none is present. Regions that already carry a
    polygon (e.g. a user-drawn shape) and unresolvable ones are returned unchanged."""
    if not region or region.get("polygon"):
        return region
    geom = region_polygon_geometry(region.get("kind"), region.get("code"))
    if not geom:
        return region
    return {**region, "polygon": geom}
