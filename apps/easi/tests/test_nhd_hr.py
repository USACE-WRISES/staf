"""Offline tests for the NHDPlus HR datasource: parsing, sentinel guards,
bbox guards/cache, attrs shape parity, and the HR nearest-line snap."""
from __future__ import annotations

from easi.datasources import nhd_hr


def _feat(**props) -> dict:
    base = {
        "nhdplusid": 24000800021917.0, "gnis_name": "Rush Run",
        "reachcode": "05060001001737", "lengthkm": 1.2, "totdasqkm": 2.7176,
        "slope": 0.01767, "fcode": 46003, "ftype": 460, "streamorde": 1,
        "hydroseq": 24000800000444.0, "uphydroseq": 24000800000455.0,
        "dnhydroseq": 24000800000440.0, "vpuid": "0506", "innetwork": 1,
    }
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "LineString",
                         "coordinates": [[-83.02, 40.09], [-83.01, 40.10]]}}


def test_parse_feature_types_and_id_roundtrip():
    rec = nhd_hr.parse_feature(_feat())
    assert rec["nhdplusid"] == 24000800021917 and isinstance(rec["nhdplusid"], int)
    assert rec["gnis_name"] == "Rush Run"
    assert rec["totdasqkm"] == 2.7176
    assert rec["slope"] == 0.01767
    assert rec["fcode"] == 46003 and rec["stream_order"] == 1
    assert rec["hydroseq"] == 24000800000444
    assert rec["uphydroseq"] == 24000800000455
    assert rec["vpuid"] == "0506"
    assert rec["geometry"]["type"] == "LineString"


def test_parse_feature_sentinels():
    # HR uses -9998 for "no value"; any negative slope is unusable either way.
    assert nhd_hr.parse_feature(_feat(slope=-9998))["slope"] is None
    assert nhd_hr.parse_feature(_feat(slope=-0.5))["slope"] is None
    assert nhd_hr.parse_feature(_feat(totdasqkm=0))["totdasqkm"] is None
    assert nhd_hr.parse_feature(_feat(totdasqkm=None))["totdasqkm"] is None
    assert nhd_hr.parse_feature(_feat(gnis_name="  "))["gnis_name"] is None
    assert nhd_hr.parse_feature(_feat(uphydroseq=0))["uphydroseq"] is None


def test_parse_feature_requires_id():
    assert nhd_hr.parse_feature(_feat(nhdplusid=None)) is None
    assert nhd_hr.parse_feature(None) is None


def test_exceeded_reads_both_locations():
    assert nhd_hr._exceeded({"exceededTransferLimit": True})
    assert nhd_hr._exceeded({"properties": {"exceededTransferLimit": True}})
    assert not nhd_hr._exceeded({"features": []})
    assert not nhd_hr._exceeded(None)


def test_bbox_guards_never_fetch(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not fetch")
    monkeypatch.setattr(nhd_hr, "_request", boom)
    nhd_hr._fetch_bbox.cache_clear()
    assert nhd_hr.hr_flowlines_in_bbox(-83.0, 40.0, -83.0, 40.1) is None   # degenerate
    assert nhd_hr.hr_flowlines_in_bbox(-84.0, 40.0, -83.0, 41.0) is None   # too large


def test_bbox_fetch_parses_and_strips_props(monkeypatch):
    payload = {"type": "FeatureCollection",
               "features": [_feat(), _feat(nhdplusid=None),
                            {"type": "Feature", "properties": {"nhdplusid": 5},
                             "geometry": None}]}
    monkeypatch.setattr(nhd_hr, "_request", lambda *a, **k: payload)
    nhd_hr._fetch_bbox.cache_clear()
    fc = nhd_hr.hr_flowlines_in_bbox(-83.03, 40.08, -83.00, 40.11)
    assert fc and len(fc["features"]) == 1                 # id-less + geom-less dropped
    assert fc["features"][0]["properties"] == {"nhdplusid": 24000800021917}


def test_bbox_exceeded_returns_none(monkeypatch):
    monkeypatch.setattr(nhd_hr, "_request",
                        lambda *a, **k: {"type": "FeatureCollection",
                                         "features": [_feat()],
                                         "exceededTransferLimit": True})
    nhd_hr._fetch_bbox.cache_clear()
    assert nhd_hr.hr_flowlines_in_bbox(-83.03, 40.08, -83.00, 40.11) is None


def test_hr_attrs_matches_flowline_attrs_shape(monkeypatch):
    monkeypatch.setattr(nhd_hr, "hr_flowline_by_id",
                        lambda nid, **k: nhd_hr.parse_feature(_feat()))
    out = nhd_hr.hr_attrs(24000800021917)
    assert set(out) == {"gnis_name", "drainage_area_sqkm", "huc8", "slope",
                        "fcode", "stream_order", "sinuosity"}
    assert out["huc8"] == "05060001"
    assert out["drainage_area_sqkm"] == 2.7176
    # A straight two-point line has sinuosity exactly 1.0.
    assert out["sinuosity"] == 1.0


def test_hr_attrs_error_path(monkeypatch):
    monkeypatch.setattr(nhd_hr, "hr_flowline_by_id", lambda nid, **k: None)
    out = nhd_hr.hr_attrs(42)
    assert out["gnis_name"] is None and out["slope"] is None
    assert "42" in out["_hr_error"]


def test_derive_reach_hr_walks_uphydroseq(monkeypatch):
    # A three-segment chain heading upstream (south to north); each segment is
    # ~0.005 deg (~555 m). The walk must collect enough mainstem and trim the
    # requested length upstream of the snap point.
    def seg(nid, hs, up, lat0):
        return {"nhdplusid": nid, "gnis_name": None, "reachcode": "05060001001737",
                "lengthkm": 0.555, "totdasqkm": 2.0, "slope": 0.01,
                "fcode": 46003, "ftype": 460, "stream_order": 1,
                "hydroseq": hs, "uphydroseq": up, "dnhydroseq": None,
                "vpuid": "0506",
                "geometry": {"type": "LineString",
                             "coordinates": [[-83.0, lat0],
                                             [-83.0, lat0 + 0.005]]}}

    chain = {1: seg(101, 11, 12, 40.000), 2: seg(102, 12, 13, 40.005),
             3: seg(103, 13, 0, 40.010)}
    monkeypatch.setattr(nhd_hr, "hr_flowline_by_id", lambda nid, **k: chain[1])
    monkeypatch.setattr(nhd_hr, "_feature_by_hydroseq",
                        lambda hs, **k: {12: chain[2], 13: chain[3]}.get(hs))
    gj, actual_ft, warns = nhd_hr.derive_reach_hr(101, 40.001, -83.0, 1000.0)
    assert gj and gj["features"]
    assert actual_ft is not None and abs(actual_ft - 1000.0) < 5.0
    assert warns == []


def test_derive_reach_hr_short_chain_warns(monkeypatch):
    one = {"nhdplusid": 101, "gnis_name": None, "reachcode": None,
           "lengthkm": 0.111, "totdasqkm": 1.0, "slope": 0.01, "fcode": 46003,
           "ftype": 460, "stream_order": 1, "hydroseq": 11, "uphydroseq": 0,
           "dnhydroseq": None, "vpuid": "0506",
           "geometry": {"type": "LineString",
                        "coordinates": [[-83.0, 40.0], [-83.0, 40.001]]}}
    monkeypatch.setattr(nhd_hr, "hr_flowline_by_id", lambda nid, **k: one)
    monkeypatch.setattr(nhd_hr, "_feature_by_hydroseq", lambda hs, **k: None)
    gj, actual_ft, warns = nhd_hr.derive_reach_hr(101, 40.0005, -83.0, 1000.0)
    assert gj and actual_ft < 1000.0
    assert any("mainstem available upstream" in w for w in warns)


def test_derive_reach_hr_no_geometry(monkeypatch):
    monkeypatch.setattr(nhd_hr, "hr_flowline_by_id", lambda nid, **k: None)
    gj, actual_ft, warns = nhd_hr.derive_reach_hr(42, 40.0, -83.0, 1000.0)
    assert gj is None and actual_ft is None
    assert "42" in warns[0]


def test_nearest_point_on_hr_lines_returns_nhdplusid():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nhdplusid": 111},
         "geometry": {"type": "LineString",
                      "coordinates": [[-83.10, 40.00], [-83.10, 40.01]]}},
        {"type": "Feature", "properties": {"nhdplusid": 222},
         "geometry": {"type": "LineString",
                      "coordinates": [[-83.00, 40.00], [-83.00, 40.01]]}},
    ]}
    hit = nhd_hr.nearest_point_on_hr_lines(fc, 40.005, -83.0001)
    assert hit is not None
    assert hit[3] == 222
    assert hit[2] < 100.0          # a few dozen feet, not miles
