"""Point -> EPA Level III ecoregion resolution for EASI.

Reports the site's EPA Level III ecoregion in the basin characteristics so a reviewer can
interpret land-cover metrics (e.g. the natural-riparian-vegetation CPOM proxy is non-forest in
grassland/arid ecoregions). The polygon set ``data/ecoregions_l3.geojson`` (attrs ``US_L3CODE`` /
``US_L3NAME``) is copied from DEEP/StreamCurves; the boundary-inclusive point-in-polygon resolver
mirrors ``deep/geo.py`` (lazy read, cached index, bbox prefilter, shapely ``covers``).

FUTURE: consolidate the geo resolvers into a shared ``staf-core`` package.
"""
from __future__ import annotations

import functools
import gzip
import json
import math
from pathlib import Path

from . import config

DATA_DIR = config.DATA_DIR
ECOREGIONS_PATH = DATA_DIR / "ecoregions_l3.geojson"
NARS9_PATH = DATA_DIR / "nars-ecoregions-9.geojson.gz"


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


@functools.lru_cache(maxsize=None)
def _index(path_str: str, value_prop: str, name_prop: str) -> list:
    """Build + cache a ``(value, name, prepared_geom, bounds)`` index for one GeoJSON.

    The prepared geometry makes repeated ``covers`` tests fast; the bounds drive a cheap bbox
    prefilter. Missing file / unreadable geometry -> empty list.
    """
    from shapely.geometry import shape
    from shapely.prepared import prep

    path = Path(path_str)
    if not path.exists():
        return []
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                fc = json.load(stream)
        else:
            fc = json.loads(path.read_text(encoding="utf-8"))
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
        out.append((props.get(value_prop), props.get(name_prop), prep(g), g.bounds))
    return out


def level3_at(lat, lon) -> dict | None:
    """EPA Level III ecoregion covering ``(lat, lon)`` as ``{"code", "name"}``, else None.

    Boundary-inclusive (shapely ``covers``); ``code`` is ``US_L3CODE`` as str, ``name`` is
    ``US_L3NAME``. None outside the mapped ecoregions (offshore / outside CONUS).
    """
    if not _finite(lat) or not _finite(lon):
        return None
    from shapely.geometry import Point

    lat = float(lat)
    lon = float(lon)
    pt = Point(lon, lat)  # GeoJSON is (lon, lat)
    for value, name, pg, (minx, miny, maxx, maxy) in _index(
            str(ECOREGIONS_PATH), "US_L3CODE", "US_L3NAME"):
        if lon < minx or lon > maxx or lat < miny or lat > maxy:
            continue
        if pg.covers(pt):
            return {"code": None if value is None else str(value), "name": name or ""}
    return None


def nars9_at(lat, lon) -> dict | None:
    """Official EPA nine-region NARS polygon covering ``(lat, lon)``.

    Returns ``{"code", "name"}`` using ``WSA_9`` / ``WSA_9_NM``. This asset is
    intentionally separate from the Level III ecoregion layer.
    """
    if not _finite(lat) or not _finite(lon):
        return None
    from shapely.geometry import Point

    lat = float(lat)
    lon = float(lon)
    point = Point(lon, lat)
    for value, name, prepared, (minx, miny, maxx, maxy) in _index(
            str(NARS9_PATH), "WSA_9", "WSA_9_NM"):
        if lon < minx or lon > maxx or lat < miny or lat > maxy:
            continue
        if prepared.covers(point):
            return {"code": None if value is None else str(value), "name": name or ""}
    return None
