"""Soil K-factor over the true watershed (USDA Soil Data Access).

Feasibility verdict (G6): BUILD. One SDA tabular query returns the surface
horizon ``kwfact`` of the major components of every map unit intersecting the
watershed (spiked live: sub-second for a headwater basin). The reported value
is the component-percent-weighted mean across those map units.

Documented limitation (rides every entry): map units are not weighted by
their intersected polygon area, so the value is a map-unit mean, not the
area-weighted grid mean StreamCat ``kffact`` computes. Proper area weighting
is the named upgrade. Never raises.
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
_LIMITATION = ("map-unit mean weighted by component percent, not by "
               "intersected area")


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


def _query_kwfact(wkt: str) -> list[tuple[float, float]] | None:
    """``(kwfact, component_pct)`` rows for the AOI, or None on failure."""
    sql = (
        "SELECT c.mukey, ch.kwfact, c.comppct_r "
        "FROM component c "
        "JOIN chorizon ch ON ch.cokey = c.cokey "
        "WHERE c.mukey IN (SELECT * FROM "
        f"SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')) "
        "AND c.majcompflag = 'Yes' AND ch.hzdept_r = 0")
    try:
        r = requests.post(SDA_URL, json={"query": sql,
                                         "format": "JSON+COLUMNNAME"},
                          timeout=90)
        if r.status_code != 200:
            return None
        rows = (r.json().get("Table") or [])[1:]
    except Exception:  # noqa: BLE001
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


@register("soils")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    if ws is None:
        return {"soilKFactor": metric_entry(
            None, "dimensionless", "SSURGO kwfact", _VINTAGE,
            "pointWatershed", ["watershed polygon unavailable"])}
    wkt = _wkt(ws)
    if wkt is None:
        return {"soilKFactor": metric_entry(
            None, "dimensionless", "SSURGO kwfact", _VINTAGE,
            "pointWatershed", ["watershed too complex for the SDA query"])}
    rows = _query_kwfact(wkt)
    if rows is None:
        return {"soilKFactor": metric_entry(
            None, "dimensionless", "SSURGO kwfact", _VINTAGE,
            "pointWatershed", ["Soil Data Access query failed"])}
    if not rows:
        return {"soilKFactor": metric_entry(
            None, "dimensionless", "SSURGO kwfact", _VINTAGE,
            "pointWatershed", ["no surface-horizon kwfact in the AOI"])}
    total = sum(pct for _, pct in rows)
    mean = sum(k * pct for k, pct in rows) / total if total else None
    return {"soilKFactor": metric_entry(
        round(mean, 3) if mean is not None else None, "dimensionless",
        "SSURGO surface-horizon kwfact", _VINTAGE, "pointWatershed",
        [_LIMITATION])}
