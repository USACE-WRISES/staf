"""EASI's lookups answer from the STAF data bundle first, offline (Phase 4, 2026-10-02).

The bundle is the hr-data app's two-region fixture (``apps/hr-data/tests/fixture2.py``) with its
V2 part (``build_v2``: the same network, its ids standing in for COMIDs, or a partial one whose
HR-only reaches drain to the outlet's V2 catchment) and its lookups (``write_lookups``: the
per-flowline extras and the ATTAINS units). Every service is refused, so an answer that reached
for the network fails the test.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from easi import delineation, routing
from easi._vendor.site_engine import _hrslim, bundle, hr
from easi._vendor.site_engine._hrslim import arcs, fmt, fmt2, grid
from easi.datasources import attains, fabric, nhd_hr, nrsa, v2, wbd

_FIXTURE = Path(__file__).resolve().parents[2] / "hr-data" / "tests" / "fixture2.py"
pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="hr-data fixture not present")


def _fixture_module():
    # the fixture imports ``hrslim``; the vendored engine's byte-identical copy stands in
    aliases = {"hrslim": _hrslim, "hrslim.arcs": arcs, "hrslim.fmt": fmt, "hrslim.fmt2": fmt2,
               "hrslim.grid": grid}
    saved = {k: sys.modules.get(k) for k in aliases}
    sys.modules.update(aliases)
    try:
        spec = importlib.util.spec_from_file_location("easi_bundle_fixture2", _FIXTURE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(scope="module")
def fx():
    return _fixture_module()


def _offline(tmp_path, fx, monkeypatch, *, partial: bool):
    folder = fx.build(tmp_path / "bundle")
    (folder / bundle.ABSENT_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                             encoding="utf-8")
    fx.build_v2(folder, partial=partial)
    fx.write_lookups(folder)
    bundle.set_source("bundle", folder)
    hr.clear_caches()
    fabric.clear_feature_memo()
    nhd_hr._fetch_bbox.cache_clear()
    import types

    import requests

    def no_network(*args, **kwargs):
        raise AssertionError("a lookup asked a live service")
    monkeypatch.setattr(requests, "get", no_network)
    monkeypatch.setattr(requests, "post", no_network)
    # a stand-in for pynhd: the tests only need NLDI refused, and the real import is slow and
    # re-registers XML namespace prefixes for the whole session
    fake = types.ModuleType("pynhd")
    fake.NLDI = no_network
    monkeypatch.setitem(sys.modules, "pynhd", fake)
    yield folder
    bundle.set_source(None)
    hr.clear_caches()
    fabric.clear_feature_memo()
    nhd_hr._fetch_bbox.cache_clear()


@pytest.fixture()
def offline(tmp_path, fx, monkeypatch):
    yield from _offline(tmp_path, fx, monkeypatch, partial=False)


@pytest.fixture()
def offline_partial(tmp_path, fx, monkeypatch):
    """V2 lacks the HR-only reaches 2, 4 and 5."""
    yield from _offline(tmp_path, fx, monkeypatch, partial=True)


def test_v2_flowlines_and_attributes(offline, fx):
    feat = v2.feature_by_comid(fx.ID[1])                          # the method's fabric decodes it
    attrs = fabric.attrs_from_feature(feat)
    assert attrs["drainage_area_sqkm"] == 5.0 and attrs["huc8"] == "02060005"
    assert attrs["stream_order"] == 1 and attrs["gnis_name"] == "Outlet Run"
    box = v2.features_in_bbox(*fx.box_deg(-10, -10, 510, 510))
    assert sorted(f["properties"]["comid"] for f in box) == sorted(fx.ID.values())
    assert delineation.flowline_attrs(fx.ID[4])["drainage_area_sqkm"] == 2.0


def test_basin_reach_and_snap(offline, fx):
    ws, area, warns = delineation.delineate_watershed(fx.ID[1])
    assert area == pytest.approx(5.0, rel=1e-4) and not warns
    assert ws["type"] == "FeatureCollection" and len(ws["features"]) == 1
    lon, lat = fx.point_deg(50, 25)
    reach, length_ft, _ = delineation.derive_reach(fx.ID[1], lat, lon, 300.0)
    assert reach["features"] and length_ft == pytest.approx(300.0, abs=1.0)


def test_snap_and_routing_name_the_bundle(offline_partial, fx):
    lon, lat = fx.point_deg(50, 125)                              # on an HR-only reach
    snap = delineation.snap_point(lat, lon)
    jlon, jlat = fx.point_deg(50, 50)                             # the outlet's top
    assert snap["comid"] == fx.ID[1] and snap["drainage_area_sqkm"] == 5.0
    assert (snap["snapped_lon"], snap["snapped_lat"]) == pytest.approx((jlon, jlat), abs=1e-5)
    out = routing.resolve_anchor(lat, lon)["anchor"]
    assert out["anchorKind"] == "hrSurrogate" and out["clickedStream"]["nhdplusId"] == fx.ID[2]
    assert out["scoredReach"]["comid"] == fx.ID[1] and out["scoredReach"]["drainageAreaSqkm"] == 5.0
    assert out["routing"]["method"] == bundle.RAINDROP_METHOD and out["routing"]["daRatio"] == 1.67


def test_hr_reach_lookups(offline, fx):
    rec = nhd_hr.hr_flowline_by_id(fx.ID[2])
    assert rec["totdasqkm"] == 3.0 and rec["uphydroseq"] == 1004 and "qama" not in rec
    attrs = nhd_hr.hr_attrs(fx.ID[1])
    assert attrs["sinuosity"] == 1.234 and attrs["drainage_area_sqkm"] == 5.0
    lon, lat = fx.point_deg(50, 125)
    reach, length_ft, _ = nhd_hr.derive_reach_hr(fx.ID[2], lat, lon, 300.0)
    assert reach["features"] and length_ft == pytest.approx(300.0, abs=1.0)


def test_huc12_attains_and_nrsa_network(offline, fx):
    lon, lat = fx.point_deg(50, 25)
    assert wbd.huc12_at_point(lat, lon) == "020600050101"
    exact = attains.impairment_at_point(lat, lon)
    assert exact["assessment_unit"] == "IA 02-TEST-0001" and exact["ircategory"] == "5"
    lon, lat = fx.point_deg(125, 50)
    assert attains.impairment_at_point(lat, lon) == {}
    near = attains.impairment_near_point(lat, lon)
    assert near["assessment_unit"] == "IA 02-TEST-0002" and near["distance_m"] == 120.0
    assert nrsa._connected_comids(fx.ID[1], 10.0) == {fx.ID[1], fx.ID[2], fx.ID[4], fx.ID[5]}
