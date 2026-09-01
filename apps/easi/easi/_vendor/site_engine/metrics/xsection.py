"""Reach cross-section metrics (entrenchment ratio, bank-height ratio).

Re-anchors the EASI 3DEP transect machinery at the engine's HR reach: the
``_extracted`` copies of ``threedep``/``geomorph``/``bieger`` are byte-synced
from EASI (the sync gate guards parity), so engine and EASI produce identical
values for identical inputs. Slow family (live 3DEP raster pulls); it degrades
with a warning rather than blocking the record. Never raises.
"""
from __future__ import annotations

from ..provenance import metric_entry
from . import register

_SRC = "USGS 3DEP representative cross section (engine reach)"
_VINTAGE = "3DEP 1 m where published, else 10 m; Bieger et al. 2015 bankfull"


@register("xsection")
def compute(record: dict, tree_geoms: list) -> dict:
    reach = (record.get("reach") or {}).get("geometry")
    site = record.get("site") or {}
    da = site.get("drainageAreaSqkm")
    if not reach or not da:
        return {"entrenchmentRatio": metric_entry(
            None, "ratio", _SRC, _VINTAGE, "reach",
            ["reach geometry or drainage area unavailable"])}
    try:
        from .._extracted import bieger, threedep

        bf = bieger.bankfull_geometry(da, site.get("snapLat"),
                                      site.get("snapLon"))
        geom = threedep.reach_geomorphology(
            reach, da, bankfull=(bf["width_m"], bf["depth_m"]),
            bankfull_area_m2=bf["area_m2"], division=bf["division_name"])
        if not isinstance(geom, dict):
            geom = {}
        warns: list[str] = []
        if geom.get("edge_limited"):
            warns.append("flood-prone width reached the DEM buffer edge")
        if bf.get("extrapolated"):
            warns.append("drainage area is outside the Bieger fit range; "
                         "bankfull is extrapolated")
        res = geom.get("dem_resolution_m")
        if res:
            warns.append(f"DEM resolution {res} m")
        out = {}
        for key, name in (("entrenchment_ratio", "entrenchmentRatio"),
                          ("bank_height_ratio", "bankHeightRatio")):
            value = geom.get(key)
            out[name] = metric_entry(
                round(float(value), 3) if value is not None else None,
                "ratio", _SRC, _VINTAGE, "reach",
                warns if value is not None else
                warns + ["cross-section ratio unavailable"])
        out["bankfullWidthM"] = metric_entry(
            bf["width_m"], "m", f"Bieger 2015 ({bf['division_name']})",
            _VINTAGE, "reach",
            ["extrapolated"] if bf.get("extrapolated") else [])
        return out
    except Exception as exc:  # noqa: BLE001 - resilience by design
        return {"entrenchmentRatio": metric_entry(
            None, "ratio", _SRC, _VINTAGE, "reach",
            [f"cross-section derivation failed: {exc}"])}
