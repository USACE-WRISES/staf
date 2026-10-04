"""The version 2 reader (exact catchments, row-offset network) answers like version 1."""
import numpy as np
import pytest
import shapely

import fixture2
from hrslim import Dataset2, catchment_features, fmt, fmt2, line_features

ID, SIDE = fixture2.ID, fixture2.SIDE
NETWORK_BOX = fixture2.box_deg(1, 1, 199, 260)      # reaches 1-4: not 5 (y 300+), not the ditch


def projected(geom):
    """A lon/lat geometry in grid cells (the fixture's EPSG:5070 10 m grid)."""
    g = fixture2.GRID

    def fwd(q):
        x, y = g.project(q[:, 0], q[:, 1])
        return np.column_stack([(x - g.offset) / g.cell, (y - g.offset) / g.cell])
    return shapely.transform(geom, fwd)


def same_shape(geom, want, tol_cells=0.01):
    return shapely.symmetric_difference(projected(geom), want).area <= tol_cells * want.length


def test_the_folder_opens_as_version_2(dataset2):
    assert isinstance(dataset2, Dataset2)
    assert dataset2.tolerances == [] and dataset2.tolerance(20) == 0.0


def test_lines_in_bbox_carry_the_service_fields(dataset2):
    feats = line_features(dataset2.lines_in_bbox(*NETWORK_BOX))
    by_id = dict((f["properties"]["nhdplusid"], f["properties"]) for f in feats)
    assert sorted(by_id) == sorted(ID[n] for n in (1, 2, 3, 4))
    props = by_id[ID[4]]
    assert list(props) == list(fmt.SERVICE_FIELDS)
    assert props["innetwork"] == 1 and props["ftype"] == 460 and props["vpuid"] == "9901"
    assert props["reachcode"] == "02060005000004"          # 14 digits, zero padded again
    assert (props["hydroseq"], props["dnhydroseq"], props["uphydroseq"]) == (1004, 1002, 2005)
    assert props["lengthkm"] == pytest.approx(0.5) and props["slope"] == pytest.approx(0.001)
    assert by_id[ID[3]]["qama"] is None and by_id[ID[3]]["gnis_name"] is None
    assert by_id[ID[1]]["uphydroseq"] == 1002 and by_id[ID[1]]["dnhydroseq"] == 0


def test_line_geometry_keeps_its_place(dataset2):
    f = line_features(dataset2.reach(nhdplusid=ID[2]))[0]
    got = projected(shapely.geometry.shape(f["geometry"]))
    assert shapely.hausdorff_distance(got, fixture2.line_cells(2)) < 0.15    # within 1.5 m


def test_exact_intersection_drops_lines_whose_box_only_overlaps(dataset2):
    box = fixture2.corner_box()
    loose = dataset2.lines_in_bbox(*box, exact=False)
    assert loose is not None and ID[6] in loose.column("nhdplusid").to_pylist()
    assert dataset2.lines_in_bbox(*box) is None


def test_reach_by_id_and_hydroseq(dataset2):
    by_id = line_features(dataset2.reach(nhdplusid=ID[5]))
    assert len(by_id) == 1 and by_id[0]["properties"]["vpuid"] == "9902"
    assert by_id[0]["properties"]["dnhydroseq"] == 1004                  # across the region line
    by_hs = line_features(dataset2.reach(hydroseq=1002))
    assert [f["properties"]["nhdplusid"] for f in by_hs] == [ID[2]]
    assert dataset2.reach(nhdplusid=12345) is None


def test_multipart_line_comes_back_multipart(dataset2):
    f = line_features(dataset2.reach(nhdplusid=ID[6]))[0]
    assert f["geometry"]["type"] == "MultiLineString"


def test_flowlines_by_ids_across_row_groups_and_regions(dataset2):
    t = dataset2.flowlines_by_ids([ID[6], ID[1], ID[5]])
    assert sorted(t.column("nhdplusid").to_pylist()) == sorted([ID[1], ID[5], ID[6]])
    assert set(line_features(t, attributes=False)[0]["properties"]) == {"nhdplusid"}


def test_upstream_tree_crosses_the_region_boundary(dataset2):
    tree = dataset2.upstream_tree(ID[1])
    assert tree["status"] == "ok"
    assert tree["ids"] == sorted([ID[1], ID[2], ID[3], ID[4], ID[5]])
    assert tree["nHops"] == 4               # {1} -> {2, 3} -> {4} -> {5 across the line}


def test_a_one_reach_tree_reports_one_hop(dataset2):
    tree = dataset2.upstream_tree(ID[5])
    assert (tree["status"], tree["nReaches"], tree["nHops"]) == ("ok", 1, 1)


def test_budget_refusals_match_the_engine_wording(dataset2):
    by_reaches = dataset2.upstream_tree(ID[1], max_reaches=2)
    assert (by_reaches["status"], by_reaches["nReaches"], by_reaches["nHops"]) == ("refused", 3, 1)
    assert by_reaches["reason"] == ("watershed exceeds the engine budget (3 reaches, 1 hops; "
                                    "the budget is 2 reaches and 200 hops)")
    by_hops = dataset2.upstream_tree(ID[1], max_hops=2)
    assert (by_hops["status"], by_hops["nReaches"], by_hops["nHops"]) == ("refused", 4, 2)


def test_unknown_reach_fails(dataset2):
    assert dataset2.upstream_tree(1)["status"] == "failed"
    assert dataset2.watershed(1)["status"] == "failed"


def test_catchments_by_ids_are_exact_and_span_regions(dataset2):
    t = dataset2.catchments_by_ids([ID[1], ID[5]])
    feats = dict((f["properties"]["nhdplusid"], f) for f in catchment_features(t))
    assert sorted(feats) == sorted([ID[1], ID[5]])
    for n in (1, 5):
        g = shapely.geometry.shape(feats[ID[n]]["geometry"])
        assert g.geom_type == "Polygon"
        assert same_shape(g, fixture2.cell_box(*fixture2.REACHES[n][0]))


def test_catchments_in_bbox_ignore_the_tolerance(dataset2):
    box = fixture2.box_deg(10, 10, 40, 40)
    for tol in (None, 10, "20", 15):
        t = dataset2.catchments_in_bbox(*box, tol)
        assert t.column("nhdplusid").to_pylist() == [ID[1]]


def test_watershed_area_is_exact(dataset2):
    out = dataset2.watershed(ID[1])
    assert out["status"] == "ok" and out["nCatchments"] == 5 and out["tolerance_m"] == 0.0
    assert out["areaSqkm"] == 5.0 and out["vaaAreaSqkm"] == 5.0 and out["areaAgreement"] == 1.0
    want = shapely.union_all([fixture2.cell_box(*fixture2.REACHES[n][0]) for n in (1, 2, 3, 4, 5)])
    geom = shapely.geometry.shape(out["geometry"])
    assert geom.geom_type == "Polygon" and same_shape(geom, want)


def test_watershed_joins_regions_without_a_seam(dataset2):
    out = dataset2.watershed(ID[2])                    # 2 and 4 here, 5 in the next region
    assert (out["nReaches"], out["nCatchments"], out["areaSqkm"]) == (3, 3, 3.0)
    geom = shapely.geometry.shape(out["geometry"])
    assert geom.geom_type == "Polygon" and len(geom.interiors) == 0


def test_watershed_refusal_carries_the_walk(dataset2):
    out = dataset2.watershed(ID[1], max_reaches=2)
    assert out["status"] == "refused" and "geometry" not in out and out["nReaches"] == 3


def test_qa_is_empty(dataset2):
    assert dataset2.qa_in_bbox(*NETWORK_BOX) == []


def test_summary_lists_both_regions(dataset2):
    s = dataset2.summary()
    assert s["format"] == 2 and [v["vpu"] for v in s["vpus"]] == ["9901", "9902"]
    assert s["tolerances"] == [] and s["defaultTolerance"] == 0.0
    v = s["vpus"][0]
    assert set(v["bytes"]) == {"lines", "catchments", "arcs"} and v["catchments"] == 5
    assert s["bytes"] == sum(sum(x["bytes"].values()) for x in s["vpus"])


def test_links_name_the_rows_that_leave_a_region(data_dir2):
    import pyarrow.parquet as pq
    links = pq.read_table(data_dir2 / fmt2.LINKS_FILE).to_pylist()
    assert links == [{"target": 1004, "vpu": "9902", "row": 0}]


def test_walk_climbs_minor_divergences(tmp_path):
    """NHDPlus V2 routes minor divergences (``DnMinorHyd``); the walk then climbs from a branch to the
    line that splits into it, inside a region and across regions, as NLDI's navigation does."""
    from hrslim import Dataset2
    ds = Dataset2(fixture2.build(tmp_path / "minor", minor={4: 1006, 5: 1003}))
    assert set(ds.upstream_tree(fixture2.ID[6])["ids"]) == {fixture2.ID[6], fixture2.ID[4], fixture2.ID[5]}
    assert set(ds.upstream_tree(fixture2.ID[3])["ids"]) == {fixture2.ID[3], fixture2.ID[5]}
    base = Dataset2(fixture2.build(tmp_path / "base"))
    assert base.upstream_tree(fixture2.ID[6])["ids"] == [fixture2.ID[6]]
