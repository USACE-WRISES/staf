"""EPA ecoregion and slope strata for EASI.

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


def ecoregion_crosswalk() -> dict:
    """The bundled Level III -> Level II / I crosswalk, cached with EASI data."""
    return config._load("ecoregion-crosswalk.json")


def slope_class(slope) -> str | None:
    """Slope in m/m, with the analysis's right=False boundaries at 0.005 and 0.02."""
    if isinstance(slope, bool) or not _finite(slope) or float(slope) < 0:
        return None
    value = float(slope)
    return "lt_0.5" if value < 0.005 else "0.5_to_2" if value < 0.02 else "ge_2"


def strata_for(l3_code, *, slope=None) -> dict:
    """Resolve stored Level III identity and slope without looking up polygons."""
    code = None if l3_code is None else str(l3_code).strip()
    if code and code.endswith(".0"):
        code = code[:-2]
    code = code or None
    crosswalk = ecoregion_crosswalk()
    entry = crosswalk.get("l3", {}).get(code) or {}
    l2 = entry.get("l2")
    return {"l3": code, "l2": l2, "l1": entry.get("l1"),
            "l2_name": (crosswalk.get("l2", {}).get(l2) or {}).get("name"),
            "slope_class": slope_class(slope)}


@functools.lru_cache(maxsize=None)
def _nars9_names(path_str: str) -> dict:
    """Names only, read without constructing geometries or performing a spatial lookup."""
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            features = json.load(stream).get("features", [])
    except (OSError, ValueError):
        return {}
    return {str(props["WSA_9"]): props.get("WSA_9_NM")
            for feature in features if (props := feature.get("properties") or {}).get("WSA_9")}


def nars9_name(code) -> str | None:
    return _nars9_names(str(NARS9_PATH)).get(code)


def strata_at(lat, lon, *, slope=None) -> dict:
    """Resolve a live location once, retaining the official NARS nutrient region."""
    l3, nars = level3_at(lat, lon) or {}, nars9_at(lat, lon) or {}
    return {**strata_for(l3.get("code"), slope=slope), "nars9": nars.get("code")}
