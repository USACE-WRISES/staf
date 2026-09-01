"""Soil K-factor over the true watershed (USDA Soil Data Access).

Area-weighted (0.2.0): one SDA spatial query returns, per map unit
intersecting the watershed, the clipped intersection area
(``mupolygongeo.STIntersection(...).STArea()``), and one tabular query returns
the surface-horizon ``kwfact`` of each map unit's major components. The
reported value is the intersection-area-weighted mean of the map-unit K,
where each map-unit K is its component-percent-weighted mean. That is the
same construction StreamCat's ``kffact`` grid mean approximates.

Fallback (labeled): when the watershed is too complex for the SDA helper or
the area query fails, the 0.1.0 map-unit mean is reported with the
``_LIMITATION`` warning so nobody reads it as area-weighted. Never raises.
"""
from __future__ import annotations

import requests

from ..provenance import metric_entry
from . import register
from .common import watershed_geom

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
_VINTAGE = "gSSURGO via Soil Data Access (live service)"
_WKT_SIMPLIFY_DEG = 0.001
_MAX_WKT_CHARS = 60000
_MUKEY_CHUNK = 500
_SRC_WEIGHTED = ("SSURGO surface-horizon kwfact, area-weighted by SDA "
                 "polygon intersection")
_SRC_FALLBACK = "SSURGO surface-horizon kwfact"
_LIMITATION = ("map-unit mean weighted by component percent, not by "
               "intersected area")
_COVERAGE_WARN = 0.95


def _wkt(geom) -> str | None:
    """Compact exterior-only WKT for the SDA spatial helper."""
    try:
        simple = geom.simplify(_WKT_SIMPLIFY_DEG, preserve_topology=True)
        polys = (list(simple.geoms) if simple.geom_type == "MultiPolygon"
                 else [simple])
        parts = []
        for p in polys:
            ring = ", ".join(f"{x:.5f} {y:.5f}" for x, y in p.exterior.coords)
            parts.append(f"(({ring}))")
        wkt = ("POLYGON" + parts[0] if len(parts) == 1
               else "MULTIPOLYGON(" + ", ".join(parts) + ")")
        return wkt if len(wkt) <= _MAX_WKT_CHARS else None
    except Exception:  # noqa: BLE001
        return None


def _post_sql(sql: str) -> list[list] | None:
    """Rows (header stripped) for one SDA tabular query, or None."""
    try:
        r = requests.post(SDA_URL, json={"query": sql,
                                         "format": "JSON+COLUMNNAME"},
                          timeout=90)
        if r.status_code != 200:
            return None
        return (r.json().get("Table") or [])[1:]
    except Exception:  # noqa: BLE001
        return None


def _query_areas(wkt: str) -> dict[str, float] | None:
    """``{mukey: intersected area m2}`` for the AOI, or None on failure."""
    sql = (
        "SELECT m.mukey, GEOGRAPHY::STGeomFromWKB(m.mupolygongeo.STIntersection("
        f"geometry::STGeomFromText('{wkt}', 4326)).STAsBinary(), 4326)"
        ".MakeValid().STArea() AS area_m2 "
        "FROM mupolygon m "
        f"WHERE m.mupolygongeo.STIntersects(geometry::STGeomFromText('{wkt}', "
        "4326)) = 1")
    rows = _post_sql(sql)
    if rows is None:
        return None
    out: dict[str, float] = {}
    for row in rows:
        try:
            key, area = str(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if area > 0:
            out[key] = out.get(key, 0.0) + area
    return out


def _query_kwfact_by_mukey(mukeys: list[str]
                           ) -> dict[str, list[tuple[float, float]]] | None:
    """``{mukey: [(kwfact, component_pct), ...]}`` for the surface horizon of
    each map unit's major components, or None on failure."""
    out: dict[str, list[tuple[float, float]]] = {}
    keys = sorted({str(k) for k in mukeys})
    for i in range(0, len(keys), _MUKEY_CHUNK):
        chunk = keys[i:i + _MUKEY_CHUNK]
        in_list = ", ".join(f"'{k}'" for k in chunk)
        sql = (
            "SELECT c.mukey, ch.kwfact, c.comppct_r "
            "FROM component c "
            "JOIN chorizon ch ON ch.cokey = c.cokey "
            f"WHERE c.mukey IN ({in_list}) "
            "AND c.majcompflag = 'Yes' AND ch.hzdept_r = 0")
        rows = _post_sql(sql)
        if rows is None:
            return None
        for row in rows:
            try:
                key = str(row[0])
                k = float(row[1])
                pct = float(row[2]) if row[2] not in (None, "") else 1.0
            except (TypeError, ValueError, IndexError):
                continue
            out.setdefault(key, []).append((k, pct))
    return out


def _query_kwfact(wkt: str) -> list[tuple[float, float]] | None:
    """Fallback path: ``(kwfact, component_pct)`` rows for the AOI without
    area weighting (the 0.1.0 query), or None on failure."""
    sql = (
        "SELECT c.mukey, ch.kwfact, c.comppct_r "
        "FROM component c "
        "JOIN chorizon ch ON ch.cokey = c.cokey "
        "WHERE c.mukey IN (SELECT * FROM "
        f"SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')) "
        "AND c.majcompflag = 'Yes' AND ch.hzdept_r = 0")
    rows = _post_sql(sql)
    if rows is None:
        return None
    out = []
    for row in rows:
        try:
            k = float(row[1])
            pct = float(row[2]) if row[2] not in (None, "") else 1.0
        except (TypeError, ValueError, IndexError):
            continue
        out.append((k, pct))
    return out


def _pct_weighted(rows: list[tuple[float, float]]) -> float | None:
    total = sum(pct for _, pct in rows)
    return sum(k * pct for k, pct in rows) / total if total else None


def _entry(value, source, warnings) -> dict:
    return {"soilKFactor": metric_entry(
        round(value, 3) if value is not None else None, "dimensionless",
        source, _VINTAGE, "pointWatershed", warnings)}


def _fallback(wkt: str, reason: str) -> dict:
    rows = _query_kwfact(wkt)
    if rows is None:
        return _entry(None, _SRC_FALLBACK, ["Soil Data Access query failed"])
    if not rows:
        return _entry(None, _SRC_FALLBACK,
                      ["no surface-horizon kwfact in the AOI"])
    return _entry(_pct_weighted(rows), _SRC_FALLBACK,
                  [_LIMITATION, f"area weighting unavailable: {reason}"])


@register("soils")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    if ws is None:
        return _entry(None, _SRC_FALLBACK, ["watershed polygon unavailable"])
    wkt = _wkt(ws)
    if wkt is None:
        return _entry(None, _SRC_FALLBACK,
                      ["watershed too complex for the SDA query"])
    areas = _query_areas(wkt)
    if not areas:
        return _fallback(wkt, "the intersection-area query failed"
                         if areas is None else "no map units intersected")
    kmap = _query_kwfact_by_mukey(sorted(areas))
    if kmap is None:
        return _fallback(wkt, "the map-unit kwfact query failed")
    weighted = 0.0
    covered = 0.0
    for key, area in areas.items():
        k_mu = _pct_weighted(kmap.get(key) or [])
        if k_mu is None:
            continue
        weighted += area * k_mu
        covered += area
    if covered <= 0:
        return _entry(None, _SRC_WEIGHTED,
                      ["no surface-horizon kwfact in the AOI"])
    total = sum(areas.values())
    warns: list[str] = []
    share = covered / total if total else 1.0
    if share < _COVERAGE_WARN:
        warns.append(f"{(1 - share):.0%} of the watershed area has no "
                     "surface-horizon K and is excluded from the mean")
    return _entry(weighted / covered, _SRC_WEIGHTED, warns)
