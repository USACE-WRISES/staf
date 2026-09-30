"""HR reach watershed delineation by NHDPlus HR catchment aggregation.

The watershed is the drainage area of the HR reach the point snaps to: the
reach, not the point, is the outlet, so a point partway up a reach gets that
reach's whole upstream area (the union is validated against the reach's
published ``totdasqkm`` below).

The engine's primary method (spike-selected; see README): walk the upstream
tree of the anchor reach one BFS level at a time, union the tree's
``NHDPlusCatchment`` polygons, and validate the union area against the
published HR ``totdasqkm``. Network-consistent by construction: the catchment
fabric matches the HR flowlines the engine anchors on, which is exactly what
snapped MMW (V2 basins) and unsnapped MMW (grid-mismatch slivers) could not
deliver.

Runtime (0.2.1): each level is one spatial query on the frontier's endpoints
(``hr.parents_by_node``, about a second) filtered by ``dnhydroseq``
membership, so a hop costs a second or two and the tree geometries arrive
with the walk. The 0.2.0 walk queried ``dnhydroseq`` directly, which the
service scans in about 36 seconds per query, so a five-minute budget was
worth about eight hops; ``hr.parents_by_dnhydroseq`` stays as the reference
walk for ``scripts/walk_equivalence.py``.

Budgets keep big basins from walking forever: past ``max_hops`` or
``max_reaches`` the delineation REFUSES with a reason rather than returning a
truncated watershed as if it were complete. Never raises.

Under a request policy with a deadline (the apps' ``interactive``, see
``hr.POLICIES``) the walk and the catchments share it: past it the next query
is not sent and the delineation fails with its usual reason. The riparian
geometry fetch that follows a complete union is outside the deadline, so a
watershed that completes carries the same record either way.
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

    with hr.deadline(hr.active_policy().deadline_s):
        found = _tree_and_catchments(anchor, int(nid), out, max_hops=max_hops,
                                     max_reaches=max_reaches, progress=progress)
    if found is None:
        return out
    anchor, tree_ids, geoms_by_id, cats, hops = found

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

    # Tree flowline geometries for the riparian buffer: the node walk carries
    # them, so this fetch covers only ids that came back without geometry.
    # Best-effort: the polygon above is the load-bearing result and stays
    # complete either way.
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


def _tree_and_catchments(anchor: dict, nid: int, out: dict, *, max_hops: int,
                         max_reaches: int, progress):
    """The walk and the catchment fetch: ``(anchor, tree_ids, geoms_by_id,
    catchments, hops)``, or None with ``out``'s status and reason set."""
    tree_ids = {int(nid)}
    # The node walk needs the frontier's geometry; an anchor without one is
    # fetched once (a parsed record from flowlines_in_bbox always carries it).
    if not anchor.get("geometry"):
        fetched = hr.flowline_by_id(int(nid))
        if not fetched or not fetched.get("geometry"):
            out["reason"] = "anchor reach geometry unavailable"
            return None
        anchor = {**anchor, "geometry": fetched["geometry"]}
    geoms_by_id: dict[int, dict] = {int(nid): anchor["geometry"]}
    frontier = [anchor]
    hops = 0
    while frontier:
        if hops >= max_hops or len(tree_ids) >= max_reaches:
            out["status"] = "refused"
            out["reason"] = (f"watershed exceeds the engine budget "
                             f"({len(tree_ids)} reaches, {hops} hops; the "
                             f"budget is {max_reaches} reaches and "
                             f"{max_hops} hops)")
            out["nReaches"], out["nHops"] = len(tree_ids), hops
            return None
        parents = hr.parents_by_node(frontier)
        if parents is None:
            out["reason"] = "upstream tree query failed"
            out["nReaches"], out["nHops"] = len(tree_ids), hops
            return None
        frontier = []
        for rec in parents:
            rid = rec.get("nhdplusid")
            if rid is None or rid in tree_ids:
                continue
            tree_ids.add(int(rid))
            if rec.get("geometry"):
                geoms_by_id[int(rid)] = rec["geometry"]
            if rec.get("hydroseq") and rec.get("geometry"):
                frontier.append(rec)
        hops += 1
        notify(progress, stage="walk", hops=hops, reaches=len(tree_ids))
    out["nReaches"], out["nHops"] = len(tree_ids), hops

    notify(progress, stage="catchments", hops=hops, reaches=len(tree_ids))

    def batches(done, total):
        notify(progress, stage="catchments", hops=hops, reaches=len(tree_ids),
               batch=done, batches=total)
    cats = hr.catchments_by_ids(sorted(tree_ids), progress=batches)
    if cats is None:
        out["reason"] = "catchment query failed"
        return None
    if not cats:
        out["reason"] = "no catchments returned for the upstream tree"
        return None
    return anchor, tree_ids, geoms_by_id, cats, hops
