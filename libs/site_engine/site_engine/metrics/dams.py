"""Dams in the true watershed (USACE NID): count and normalized storage.

The exact-watershed analog of StreamCat ``damnrmstor``: NID dam points inside
the watershed polygon, their total NID storage, and storage per unit area.
Same public FeatureServer EASI's proximity query uses, switched to polygon
membership. Never raises.
"""
from __future__ import annotations

from ..provenance import VINTAGES, metric_entry
from . import register
from .common import esri_polygon, post_query_features, watershed_geom

NID_URL = ("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
           "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")


@register("dams")
def compute(record: dict, tree_geoms: list) -> dict:
    ws = watershed_geom((record.get("watershed") or {}).get("polygon"))
    area_sqkm = (record.get("watershed") or {}).get("areaSqkm")
    if ws is None or not area_sqkm:
        return {"damCount": metric_entry(
            None, "count", "USACE NID", VINTAGES["nid"], "pointWatershed",
            ["watershed polygon or area unavailable"])}
    poly = esri_polygon(ws)
    if poly is None:
        return {"damCount": metric_entry(
            None, "count", "USACE NID", VINTAGES["nid"], "pointWatershed",
            ["polygon could not be encoded for the query"])}
    feats = post_query_features(NID_URL, poly, "NAME,NID_STORAGE,DAM_HEIGHT",
                                return_geometry=True)
    if feats is None:
        return {"damCount": metric_entry(
            None, "count", "USACE NID", VINTAGES["nid"], "pointWatershed",
            ["NID query failed or was truncated"])}
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
        storage = 0.0
        for p in inside:
            try:
                storage += float(p.get("NID_STORAGE") or 0.0)
            except (TypeError, ValueError):
                pass
        return {
            "damCount": metric_entry(
                len(inside), "count", "USACE NID", VINTAGES["nid"],
                "pointWatershed"),
            "damStorageAcreFt": metric_entry(
                round(storage, 1), "acre-ft", "USACE NID", VINTAGES["nid"],
                "pointWatershed"),
            "damStoragePerSqkm": metric_entry(
                round(storage / float(area_sqkm), 3), "acre-ft/km2",
                "USACE NID", VINTAGES["nid"], "pointWatershed"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"damCount": metric_entry(
            None, "count", "USACE NID", VINTAGES["nid"], "pointWatershed",
            [f"dam membership test failed: {exc}"])}
