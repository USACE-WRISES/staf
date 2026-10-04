import pytest

import fixture
from hrslim import catchment_features, fmt, line_features

X0, Y0, D, ID = fixture.X0, fixture.Y0, fixture.D, fixture.ID


def test_lines_in_bbox_carry_the_service_fields(dataset):
    t = dataset.lines_in_bbox(X0, Y0, X0 + 0.02, Y0 + 0.029)
    feats = line_features(t)
    ids = sorted(f["properties"]["nhdplusid"] for f in feats)
    assert ids == sorted([ID[1], ID[2], ID[3], ID[4]])
    props = feats[0]["properties"]
    assert list(props) == list(fmt.SERVICE_FIELDS)
    assert props["innetwork"] == 1
    no_flow = [f for f in feats if f["properties"]["nhdplusid"] == ID[3]][0]
    assert no_flow["properties"]["qama"] is None and no_flow["properties"]["gnis_name"] is None


def test_exact_intersection_drops_lines_whose_box_only_overlaps(dataset):
    box = fixture.CORNER_BOX
    loose = dataset.lines_in_bbox(*box, exact=False)
    assert loose is not None and ID[6] in loose.column("nhdplusid").to_pylist()
    assert dataset.lines_in_bbox(*box) is None


def test_reach_by_id_and_hydroseq(dataset):
    by_id = line_features(dataset.reach(nhdplusid=ID[5]))
    assert len(by_id) == 1 and by_id[0]["properties"]["vpuid"] == "9902"
    by_hs = line_features(dataset.reach(hydroseq=1002))
    assert [f["properties"]["nhdplusid"] for f in by_hs] == [ID[2]]
    assert dataset.reach(nhdplusid=12345) is None


def test_multipart_line_comes_back_multipart(dataset):
    f = line_features(dataset.reach(nhdplusid=ID[6]))[0]
    assert f["geometry"]["type"] == "MultiLineString"


def test_upstream_tree_crosses_the_vpu_boundary(dataset):
    tree = dataset.upstream_tree(ID[1])
    assert tree["status"] == "ok"
    assert tree["ids"] == sorted([ID[1], ID[2], ID[3], ID[4], ID[5]])
    # levels: {1} -> {2, 3} -> {4} -> {5 across the boundary} -> nothing
    assert tree["nHops"] == 4


def test_a_one_reach_tree_reports_one_hop(dataset):
    tree = dataset.upstream_tree(ID[5])
    assert (tree["status"], tree["nReaches"], tree["nHops"]) == ("ok", 1, 1)


def test_budget_refusals_match_the_engine_wording(dataset):
    by_reaches = dataset.upstream_tree(ID[1], max_reaches=2)
    assert by_reaches["status"] == "refused"
    assert (by_reaches["nReaches"], by_reaches["nHops"]) == (3, 1)
    assert by_reaches["reason"] == ("watershed exceeds the engine budget (3 reaches, 1 hops; "
                                    "the budget is 2 reaches and 200 hops)")
    by_hops = dataset.upstream_tree(ID[1], max_hops=2)
    assert (by_hops["status"], by_hops["nReaches"], by_hops["nHops"]) == ("refused", 4, 2)


def test_unknown_reach_fails(dataset):
    assert dataset.upstream_tree(1)["status"] == "failed"


def test_catchments_by_ids_span_vpus(dataset):
    t = dataset.catchments_by_ids([ID[1], ID[5]])
    assert sorted(t.column("nhdplusid").to_pylist()) == sorted([ID[1], ID[5]])
    feats = catchment_features(t)
    assert all(f["geometry"]["type"] == "Polygon" for f in feats)


def test_catchments_in_bbox_by_tolerance(dataset):
    t = dataset.catchments_in_bbox(X0, Y0, X0 + 0.005, Y0 + 0.005, 10)
    assert t.column("nhdplusid").to_pylist() == [ID[1]]
    with pytest.raises(ValueError):
        dataset.catchments_in_bbox(X0, Y0, X0 + 0.005, Y0 + 0.005, 15)


def test_watershed_unions_the_tree_catchments(dataset):
    out = dataset.watershed(ID[1])
    assert out["status"] == "ok" and out["nCatchments"] == 5
    # five 0.01 degree cells near 39 N are about 0.87 x 1.11 km each
    assert 4.5 < out["areaSqkm"] < 5.0
    assert out["vaaAreaSqkm"] == 4.6
    assert out["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_qa_returns_the_original_geometry(dataset):
    feats = dataset.qa_in_bbox(X0, Y0, X0 + D, Y0 + D)
    assert sorted(f["properties"]["kind"] for f in feats) == ["catchment", "line"]


def test_summary_lists_both_vpus(dataset):
    s = dataset.summary()
    assert [v["vpu"] for v in s["vpus"]] == ["9901", "9902"]
    assert s["tolerances"] == [10.0, 20.0] and s["defaultTolerance"] == 20.0
