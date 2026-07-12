"""Point -> Level III ecoregion / US state resolution for DEEP (net-new; Part A2).

DEEP needs to resolve a snapped site to its EPA Level III ecoregion and US state so
the Assessment step can match published library assessments to the site's region and
so a saved session/report can record where the site fell.

The polygon sets (``ecoregions_l3.geojson``, ``us_states.geojson``) are copied from
StreamCurves — there is no shared ``libs/`` package yet, so this mirrors the
mirror-the-contract convention already used across the monorepo. The point-in-polygon
resolvers mirror ``streamcurves/geo.py`` (lazy read, cached index, bbox prefilter) but
use shapely's ``covers`` predicate so a point exactly on an official boundary counts as
inside (boundary-inclusive) — plain ray-casting cannot guarantee that for a shared edge
or a vertex.

Both GeoJSONs are read lazily and the built geometry index is cached, so the first
lookup pays the parse cost and subsequent lookups are fast.

FUTURE: consolidate with ``streamcurves/geo.py`` into a shared ``staf-core`` package.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ECOREGIONS_PATH = DATA_DIR / "ecoregions_l3.geojson"
STATES_PATH = DATA_DIR / "us_states.geojson"


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


@functools.lru_cache(maxsize=None)
def _index(path_str: str, value_prop: str, name_prop: str) -> list[tuple]:
    """Build + cache the point-in-polygon index for one GeoJSON.

    Returns a list of ``(value, name, prepared_geom, (minx, miny, maxx, maxy))`` tuples;
    the prepared geometry makes repeated ``covers`` tests fast and the bounds drive a
    cheap bbox prefilter. Cached by ``path::value_prop::name_prop`` (via lru_cache args).
    Missing file / unreadable geometry -> empty list.
    """
    from shapely.geometry import shape
    from shapely.prepared import prep

    path = Path(path_str)
    if not path.exists():
        return []
    try:
        fc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple] = []
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
        out.append((props.get(value_prop), props.get(name_prop), prep(g), g.bounds))
    return out


def _resolve(path: Path, value_prop: str, name_prop: str, lat, lon):
    """First feature whose polygon covers ``(lat, lon)`` -> ``(value, name)``, else None.

    Boundary-inclusive: ``covers`` returns True for a point on an edge or vertex. A bbox
    prefilter (inclusive comparison) skips non-candidates without ever rejecting a
    boundary point.
    """
    if not _finite(lat) or not _finite(lon):
        return None
    from shapely.geometry import Point

    lat = float(lat)
    lon = float(lon)
    pt = Point(lon, lat)  # GeoJSON is (lon, lat)
    for value, name, pg, (minx, miny, maxx, maxy) in _index(str(path), value_prop, name_prop):
        if lon < minx or lon > maxx or lat < miny or lat > maxy:
            continue
        if pg.covers(pt):
            return value, name
    return None


def level3_at(lat, lon) -> dict | None:
    """EPA Level III ecoregion covering ``(lat, lon)`` as ``{"code", "name"}``, else None.

    ``code`` is the ``US_L3CODE`` coerced to str (the published library records the code
    as a string, e.g. ``"55"``); ``name`` is ``US_L3NAME``. None outside the mapped
    ecoregions (e.g. offshore / outside CONUS).
    """
    r = _resolve(ECOREGIONS_PATH, "US_L3CODE", "US_L3NAME", lat, lon)
    if r is None:
        return None
    code, name = r
    return {"code": None if code is None else str(code), "name": name or ""}


def state_at(lat, lon) -> dict | None:
    """US state (or DC) covering ``(lat, lon)`` as ``{"code", "abbr", "name"}``, else None.

    ``code``/``abbr`` are the 2-letter ``state`` property (both keys carry it for callers
    that expect either); ``name`` is the full state name. None outside the US.
    """
    r = _resolve(STATES_PATH, "state", "name", lat, lon)
    if r is None:
        return None
    code, name = r
    code = None if code is None else str(code)
    return {"code": code, "abbr": code, "name": name or ""}
