"""True point-watershed delineation by NHDPlus HR catchment aggregation.

The engine's primary method (spike-selected; see README): walk the upstream
tree of the anchor reach with batched ``dnhydroseq`` parent queries, union the
tree's ``NHDPlusCatchment`` polygons, and validate the union area against the
published HR ``totdasqkm``. Network-consistent by construction: the catchment
fabric matches the HR flowlines the engine anchors on, which is exactly what
snapped MMW (V2 basins) and unsnapped MMW (grid-mismatch slivers) could not
deliver.

Runtime (0.2.0): the walk is geometry-free (ids and hydroseqs only, large POST
chunks) and the tree flowline geometries the riparian buffer needs are fetched
once at the end. The 0.1.0 walk carried geometry on every level at about five
seconds per reach, which made a five-minute budget worth about 30 reaches.

Budgets keep big basins from walking forever: past ``max_hops`` or
``max_reaches`` the delineation REFUSES with a reason rather than returning a
truncated watershed as if it were complete. Never raises.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import hr
from .geometry import CRS_ALBERS, CRS_WGS84
from .progress import notify

# Union area vs published VAA drainage area: past this relative disagreement
# the result carries a warning (data fault or an incomplete fabric).
AREA_AGREEMENT_WARN = 0.05


def delineate_watershed(anchor: dict, *, max_hops: int = 200,
                        max_reaches: int = 5000,
                        progress: Optional[Callable[[dict], Any]] = None) -> dict:
    """Aggregate the upstream catchments of one parsed HR reach.

    ``anchor`` is an ``hr.parse_feature`` record (needs ``nhdplusid``,
    ``hydroseq``, ``totdasqkm``). Returns::

        {"status": "ok" | "refused" | "failed",
         "method": "hr-catchment-aggregation",
         "polygon": FeatureCollection | None, "areaSqkm", "vaaAreaSqkm",
         "areaAgreement", "nReaches", "nHops",
         "treeFlowlines": [geometry, ...],   # for the riparian buffer
         "warnings": [...], "reason": str | None}
    """
    out: dict = {"status": "failed", "method": "hr-catchment-aggregation",
                 "polygon": None, "areaSqkm": None,
                 "vaaAreaSqkm": anchor.get("totdasqkm"), "areaAgreement": None,
                 "nReaches": 0, "nHops": 0, "treeFlowlines": [],
                 "warnings": [], "reason": None}
    nid = anchor.get("nhdplusid")
    hs = anchor.get("hydroseq")
    if not nid or not hs:
        out["reason"] = "anchor reach has no id or hydroseq"
        return out

    tree_ids = {int(nid)}
    # Geometries the walk happens to carry (stubs, or a geometry-bearing
    # caller) are kept; the rest are fetched once after the union.
    geoms_by_id: dict[int, dict] = (
        {int(nid): anchor["geometry"]} if anchor.get("geometry") else {})
    frontier = [int(hs)]
    hops = 0
    while frontier:
        if hops >= max_hops or len(tree_ids) >= max_reaches:
            out["status"] = "refused"
            out["reason"] = (f"watershed exceeds the engine budget "
                             f"({len(tree_ids)} reaches, {hops} hops; the "
                             f"budget is {max_reaches} reaches and "
                             f"{max_hops} hops)")
            out["nReaches"], out["nHops"] = len(tree_ids), hops
            return out
        parents = hr.parents_by_dnhydroseq(frontier, with_geometry=False)
        if parents is None:
            out["reason"] = "upstream tree query failed"
            out["nReaches"], out["nHops"] = len(tree_ids), hops
            return out
        frontier = []
        for rec in parents:
            rid = rec.get("nhdplusid")
            rhs = rec.get("hydroseq")
            if rid is None or rid in tree_ids:
                continue
            tree_ids.add(int(rid))
            if rec.get("geometry"):
                geoms_by_id[int(rid)] = rec["geometry"]
            if rhs:
                frontier.append(int(rhs))
        hops += 1
        notify(progress, stage="walk", hops=hops, reaches=len(tree_ids))
    out["nReaches"], out["nHops"] = len(tree_ids), hops

    notify(progress, stage="catchments", hops=hops, reaches=len(tree_ids))
    cats = hr.catchments_by_ids(sorted(tree_ids))
    if cats is None:
        out["reason"] = "catchment query failed"
        return out
    if not cats:
        out["reason"] = "no catchments returned for the upstream tree"
        return out

    notify(progress, stage="union", hops=hops, reaches=len(tree_ids))
    try:
        import geopandas as gpd
        from shapely.geometry import shape

        geoms = [shape(c["geometry"]) for c in cats]
        union = (gpd.GeoSeries(geoms, crs=CRS_WGS84).to_crs(CRS_ALBERS)
                 .union_all())
        area_sqkm = float(union.area) / 1e6
        basin = gpd.GeoSeries([union], crs=CRS_ALBERS).to_crs(CRS_WGS84)
        out["polygon"] = basin.__geo_interface__
        out["areaSqkm"] = round(area_sqkm, 4)
    except Exception as exc:  # noqa: BLE001 - resilience by design
        out["reason"] = f"catchment union failed: {exc}"
        return out

    vaa = anchor.get("totdasqkm")
    if vaa:
        agreement = out["areaSqkm"] / float(vaa)
        out["areaAgreement"] = round(agreement, 4)
        if abs(agreement - 1.0) > AREA_AGREEMENT_WARN:
            out["warnings"].append(
                f"union area disagrees with the published drainage area "
                f"by {abs(agreement - 1.0):.1%}")
    else:
        out["warnings"].append(
            "published drainage area unavailable; union not validated")

    # Tree flowline geometries for the riparian buffer: one fetch for the ids
    # the walk did not carry. Best-effort: the polygon above is the
    # load-bearing result and stays complete either way.
    missing = sorted(i for i in tree_ids if i not in geoms_by_id)
    if missing:
        notify(progress, stage="geometry", hops=hops, reaches=len(tree_ids))
        lines = hr.flowlines_by_ids(missing)
        if lines is None:
            out["warnings"].append(
                "tree flowline geometries unavailable; the riparian buffer "
                "is incomplete")
        else:
            for rec in lines:
                if rec.get("geometry") and rec.get("nhdplusid") is not None:
                    geoms_by_id[int(rec["nhdplusid"])] = rec["geometry"]
    out["treeFlowlines"] = [geoms_by_id[i] for i in sorted(geoms_by_id)]
    out["status"] = "ok"
    return out
