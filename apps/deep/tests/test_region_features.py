"""Tests for DEEP's available-assessments map feed (assessments.library_region_features).

Feeds the shaded "Available STAF assessments" overlay on the Assessment step: one
GeoJSON feature per available assessment that carries a region outline, keyed by
assessmentId so a map click can load it.
"""
from __future__ import annotations

from deep import assessments, config


def test_region_geometry_normalizes_shapes():
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert assessments._region_geometry({"polygon": poly})["type"] == "Polygon"
    # a bare ring of [x, y] points normalizes to a single-ring Polygon
    assert assessments._region_geometry({"polygon": [[0, 0], [1, 0], [1, 1], [0, 0]]})["type"] == "Polygon"
    assert assessments._region_geometry({}) is None
    assert assessments._region_geometry(None) is None


def test_library_region_features_selects_only_polygon_bearers(monkeypatch):
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    multi = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}
    fake = [
        {"assessmentId": "with-poly", "assessmentName": "With Poly",
         "region": {"name": "ECBP", "polygon": poly}},
        {"assessmentId": "no-region", "assessmentName": "No Region"},              # skipped
        {"assessmentId": "region-no-poly", "assessmentName": "Bare",
         "region": {"name": "X"}},                                                 # skipped
        {"assessmentId": "lib-region", "assessmentName": "Lib",
         "library": {"region": {"name": "NH", "polygon": multi}}},                 # via library block
    ]
    monkeypatch.setattr(config, "assessments", lambda: fake)

    fc = assessments.library_region_features()
    assert fc["type"] == "FeatureCollection"
    ids = [f["properties"]["assessmentId"] for f in fc["features"]]
    assert ids == ["with-poly", "lib-region"]
    assert fc["features"][0]["properties"]["regionName"] == "ECBP"
    assert fc["features"][1]["geometry"]["type"] == "MultiPolygon"


def test_library_region_features_come_only_from_library_assessments():
    # State-SQT registry entries never carry a region polygon, so every feature in
    # the real feed traces back to a library-published assessment and is renderable.
    # At least one exists now that the library ships Eastern Corn Belt Plains v1.
    fc = assessments.library_region_features()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) >= 1
    lib_ids = {aid for aid, a in config.assessments_by_id().items() if a.get("library")}
    for f in fc["features"]:
        assert f["properties"]["assessmentId"] in lib_ids
        assert f["properties"]["assessmentName"]
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_applicable_assessments(monkeypatch):
    # A square covering lon 0..2, lat 0..2.
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    fake = [
        {"assessmentId": "poly", "region": {"name": "Sq", "polygon": sq}},
        {"assessmentId": "no-region"},                                  # no region at all
        {"assessmentId": "region-no-poly", "region": {"name": "X"}},    # region, but no polygon
    ]
    monkeypatch.setattr(config, "assessments", lambda: fake)

    # A point inside the square: the polygon assessment plus both polygon-less ones apply.
    assert set(assessments.applicable_assessments(lat=1, lon=1)) == {
        "poly", "no-region", "region-no-poly"
    }
    # A point outside the square: only the polygon-less assessments apply everywhere.
    outside = assessments.applicable_assessments(lat=5, lon=5)
    assert set(outside) == {"no-region", "region-no-poly"}
    assert "poly" not in outside
