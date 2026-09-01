"""Dams in the true watershed (USACE NID): count, density, and storage.

Exact-watershed analogs of the StreamCat dam metrics: NID dam points inside
the watershed polygon, counted (``damdens`` analog per km2), with their
NORMAL storage (``damnrmstor`` analog: StreamCat's DamNrmStor is NID normal
storage per unit area) and their NID storage (``damnidstor`` analog) both
totaled and normalized by area. Same public FeatureServer EASI's proximity
query uses, switched to polygon membership. Never raises.
"""
from __future__ import annotations

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import esri_polygon, post_query_features, watershed_geom

NID_URL = ("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
           "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")
_FIELDS = "NAME,NID_STORAGE,NORMAL_STORAGE,DAM_HEIGHT"
_SRC = "USACE NID"


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _unavailable(reason: str) -> dict:
    return {"damCount": metric_entry(None, "count", _SRC, VINTAGES["nid"],
                                     "pointWatershed", [reason])}


@register("dams")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    area_sqkm = (record.get("watershed") or {}).get("areaSqkm")
    if ws is None or not area_sqkm:
        return _unavailable("watershed polygon or area unavailable")
    poly = esri_polygon(ws)
    if poly is None:
        return _unavailable("polygon could not be encoded for the query")
    feats = post_query_features(NID_URL, poly, _FIELDS, return_geometry=True)
    if feats is None:
        return _unavailable("NID query failed or was truncated")
    try:
        from shapely.geometry import shape
        from shapely.prepared import prep

        prepared = prep(ws)
        inside = []
        for f in feats:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                if prepared.covers(shape(geom)):
                    inside.append(f.get("properties") or {})
            except Exception:  # noqa: BLE001
                continue
        normal = 0.0
        nid_storage = 0.0
        missing_normal = 0
        for p in inside:
            n = _num(p.get("NORMAL_STORAGE"))
            if n is None:
                missing_normal += 1
            else:
                normal += n
            nid_storage += _num(p.get("NID_STORAGE")) or 0.0
        warns = ([f"{missing_normal} dam(s) without normal storage counted "
                  "as zero"] if missing_normal else [])
        area = float(area_sqkm)
        return {
            "damCount": metric_entry(
                len(inside), "count", _SRC, VINTAGES["nid"], "pointWatershed"),
            "damDensityPerSqkm": metric_entry(
                round(len(inside) / area, 4), "count/km2", _SRC,
                VINTAGES["nid"], "pointWatershed"),
            "damStorageAcreFt": metric_entry(
                round(normal, 1), "acre-ft", f"{_SRC} normal storage",
                VINTAGES["nid"], "pointWatershed", warns),
            "damStoragePerSqkm": metric_entry(
                round(normal / area, 3), "acre-ft/km2",
                f"{_SRC} normal storage", VINTAGES["nid"], "pointWatershed",
                warns),
            "damNidStorageAcreFt": metric_entry(
                round(nid_storage, 1), "acre-ft", f"{_SRC} NID storage",
                VINTAGES["nid"], "pointWatershed"),
            "damNidStoragePerSqkm": metric_entry(
                round(nid_storage / area, 3), "acre-ft/km2",
                f"{_SRC} NID storage", VINTAGES["nid"], "pointWatershed"),
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"dam membership test failed: {exc}")
