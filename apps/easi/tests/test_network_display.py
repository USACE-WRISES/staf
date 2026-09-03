"""The one-network map display (easi.network_display, 2026-09-03).

The HR geometry is split by the click rule: within the snap tolerance of a
V2 reach it draws covered (dark blue), elsewhere uncovered (cyan); V2
geometry with no HR line nearby draws covered as an orphan. Offline: the
lines are synthetic, near 40 N 83 W, where 60 ft is about 0.000165 degrees
of latitude and 300 ft about 0.000823.
"""
from __future__ import annotations

import copy

import pytest
from shapely.geometry import shape

from easi import network_display as nd

LAT_60FT = 0.000165
LAT_300FT = 0.000823
LAT_1000FT = 0.00275
TOL = 150.0


def _line(pts, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": pts}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def _len_ft(feature):
    geom = shape(feature["geometry"])
    return nd._project([geom], nd._TO_ALBERS)[0].length * nd.FT_PER_M


V2 = _fc(_line([[-83.05, 40.30], [-83.04, 40.30]], comid=1, gnis_name="Sugar Run"))


def test_hr_line_within_tolerance_is_all_covered():
    hr = _fc(_line([[-83.05, 40.30 + LAT_60FT], [-83.04, 40.30 + LAT_60FT]], nhdplusid=11))
    covered, uncovered = nd.split_by_coverage(hr, V2, TOL)
    assert [f["properties"] for f in covered["features"]] == [{"nhdplusid": 11, "cover": "v2"}]
    assert uncovered["features"] == []
    # a uniform line keeps its exact input coordinates (no round trip)
    assert covered["features"][0]["geometry"]["coordinates"] == hr["features"][0]["geometry"]["coordinates"]


def test_hr_line_beyond_tolerance_is_uncovered_and_the_v2_line_is_an_orphan():
    hr = _fc(_line([[-83.05, 40.30 + LAT_300FT], [-83.04, 40.30 + LAT_300FT]], nhdplusid=12))
    covered, uncovered = nd.split_by_coverage(hr, V2, TOL)
    assert [f["properties"] for f in uncovered["features"]] == [{"nhdplusid": 12, "cover": "hr"}]
    assert [f["properties"] for f in covered["features"]] == [
        {"comid": 1, "gnis_name": "Sugar Run", "cover": "v2-orphan"}]
    assert covered["features"][0]["geometry"]["coordinates"] == V2["features"][0]["geometry"]["coordinates"]


def test_tributary_is_covered_for_the_tolerance_then_uncovered():
    trib = _fc(_line([[-83.045, 40.30], [-83.045, 40.30 + LAT_1000FT]], nhdplusid=13))
    covered, uncovered = nd.split_by_coverage(trib, V2, TOL)
    cov = [f for f in covered["features"] if f["properties"]["cover"] == "v2"]
    unc = uncovered["features"]
    assert len(cov) == 1 and len(unc) == 1
    assert abs(_len_ft(cov[0]) - TOL) <= nd.SAMPLE_FT / 2 + 1.0
    total = _len_ft(cov[0]) + _len_ft(unc[0])
    assert abs(total - _len_ft(trib["features"][0])) < 3.3          # within 1 m
    assert cov[0]["properties"]["nhdplusid"] == 13 and unc[0]["properties"]["nhdplusid"] == 13
    # the V2 line is shadowed by the tributary only near the junction, so most
    # of it comes back as an orphan piece
    orphans = [f for f in covered["features"] if f["properties"]["cover"] == "v2-orphan"]
    assert orphans and sum(_len_ft(f) for f in orphans) > 0.8 * _len_ft(V2["features"][0])


def test_partial_orphan_is_about_half_the_v2_line():
    # HR shadows the west half of the V2 line only
    hr = _fc(_line([[-83.05, 40.30 + LAT_60FT], [-83.045, 40.30 + LAT_60FT]], nhdplusid=14))
    covered, _uncovered = nd.split_by_coverage(hr, V2, TOL)
    orphans = [f for f in covered["features"] if f["properties"]["cover"] == "v2-orphan"]
    assert len(orphans) == 1
    ratio = _len_ft(orphans[0]) / _len_ft(V2["features"][0])
    assert 0.4 < ratio < 0.6


def test_empty_and_missing_inputs():
    assert nd.split_by_coverage(_fc(), _fc(), TOL) == (nd._empty(), nd._empty())
    assert nd.split_by_coverage(None, None, TOL) == (nd._empty(), nd._empty())
    hr = _fc(_line([[-83.05, 40.31], [-83.04, 40.31]], nhdplusid=15))
    v2_only = nd.build_display(V2, None, TOL)
    assert v2_only["mode"] == "v2-only"
    assert v2_only["covered"]["features"][0]["properties"] == {"comid": 1, "gnis_name": "Sugar Run",
                                                              "cover": "v2"}
    assert v2_only["uncovered"]["features"] == []
    hr_only = nd.build_display(None, hr, TOL)
    assert hr_only["mode"] == "hr-only" and hr_only["covered"]["features"] == []
    assert hr_only["uncovered"]["features"][0]["properties"] == {"nhdplusid": 15, "cover": "hr"}
    empty = nd.build_display(None, None, TOL)
    assert empty == {"mode": "empty", "covered": nd._empty(), "uncovered": nd._empty()}
    assert nd.build_display(V2, hr, TOL)["mode"] == "segmented"


def test_multipart_hr_feature_is_exploded_and_each_part_classified():
    hr = _fc({"type": "Feature", "properties": {"nhdplusid": 16},
              "geometry": {"type": "MultiLineString", "coordinates": [
                  [[-83.05, 40.30 + LAT_60FT], [-83.04, 40.30 + LAT_60FT]],
                  [[-83.05, 40.30 + LAT_300FT], [-83.04, 40.30 + LAT_300FT]]]}})
    covered, uncovered = nd.split_by_coverage(hr, V2, TOL)
    assert [f["properties"] for f in covered["features"] if f["properties"]["cover"] == "v2"] == [
        {"nhdplusid": 16, "cover": "v2"}]
    assert [f["properties"] for f in uncovered["features"]] == [{"nhdplusid": 16, "cover": "hr"}]


def test_output_is_deterministic():
    trib = _fc(_line([[-83.045, 40.30], [-83.045, 40.30 + LAT_1000FT]], nhdplusid=13))
    a = nd.split_by_coverage(copy.deepcopy(trib), copy.deepcopy(V2), TOL)
    b = nd.split_by_coverage(trib, V2, TOL)
    assert a == b


def test_fetch_streams_runs_both_fetchers_once_and_falls_back():
    calls = {"v2": 0, "hr": 0}
    hr = _fc(_line([[-83.05, 40.30 + LAT_60FT], [-83.04, 40.30 + LAT_60FT]], nhdplusid=11))

    def fv2(w, s, e, n):
        calls["v2"] += 1
        return V2

    def fhr(w, s, e, n):
        calls["hr"] += 1
        return hr
    res = nd.fetch_streams((-83.06, 40.29, -83.03, 40.31), tol_ft=TOL, fetch_v2=fv2, fetch_hr=fhr)
    assert calls == {"v2": 1, "hr": 1}
    assert res["bbox"] == (-83.06, 40.29, -83.03, 40.31)
    assert res["v2"] is V2 and res["hr"] is hr and res["mode"] == "segmented"
    assert res["covered"]["features"] and res["uncovered"]["features"] == []

    res = nd.fetch_streams((-83.06, 40.29, -83.03, 40.31), tol_ft=TOL, fetch_v2=fv2,
                           fetch_hr=lambda *a: None)
    assert res["mode"] == "v2-only" and res["hr"] is None
    assert res["covered"]["features"][0]["properties"]["cover"] == "v2"


def test_feature_by_id():
    fc = _fc(_line([[0, 0], [1, 1]], comid=5), _line([[0, 0], [1, 1]], comid="7"))
    assert nd.feature_by_id(fc, "comid", 7)["properties"]["comid"] == "7"
    assert nd.feature_by_id(fc, "comid", 5.0)["properties"]["comid"] == 5
    assert nd.feature_by_id(fc, "comid", 9) is None
    assert nd.feature_by_id(None, "comid", 5) is None
    assert nd.feature_by_id(fc, "comid", None) is None


def test_v2_reach_feature_is_none_when_the_service_fails(monkeypatch):
    from easi.datasources import fabric
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: None)
    nd.v2_reach_feature.cache_clear()
    assert nd.v2_reach_feature(123) is None
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: {})
    nd.v2_reach_feature.cache_clear()
    assert nd.v2_reach_feature(124) is None
    feat = _line([[-83.05, 40.30], [-83.04, 40.30]], comid=125)
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: feat)
    nd.v2_reach_feature.cache_clear()
    assert nd.v2_reach_feature(125) is feat


@pytest.mark.parametrize("spacing", [nd.SAMPLE_FT, 2 * nd.SAMPLE_FT])
def test_transition_error_is_bounded_by_half_the_spacing(spacing):
    trib = _fc(_line([[-83.045, 40.30], [-83.045, 40.30 + LAT_1000FT]], nhdplusid=13))
    covered, _uncovered = nd.split_by_coverage(trib, V2, TOL, spacing_ft=spacing)
    cov = [f for f in covered["features"] if f["properties"]["cover"] == "v2"]
    assert abs(_len_ft(cov[0]) - TOL) <= spacing / 2 + 1.0
