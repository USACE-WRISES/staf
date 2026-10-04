"""SFARI's lookups answer from the STAF data bundle first, offline (Phase 4, 2026-10-02).

The bundle is the hr-data app's two-region fixture (``apps/hr-data/tests/fixture2.py``) with its
V2 part (``build_v2``: the same network, its ids standing in for COMIDs) and its lookups
(``write_lookups``: the per-flowline extras and the ATTAINS units). Every service is refused, so
an answer that reached for the network fails the test.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from sfari._vendor.site_engine import _hrslim, bundle, compute_site, hr
from sfari._vendor.site_engine._hrslim import arcs, fmt, fmt2, grid
from sfari.datasources import fabric, nwi

_FIXTURE = Path(__file__).resolve().parents[2] / "hr-data" / "tests" / "fixture2.py"
pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="hr-data fixture not present")


def _fixture_module():
    # the fixture imports ``hrslim``; the vendored engine's byte-identical copy stands in
    aliases = {"hrslim": _hrslim, "hrslim.arcs": arcs, "hrslim.fmt": fmt, "hrslim.fmt2": fmt2,
               "hrslim.grid": grid}
    saved = {k: sys.modules.get(k) for k in aliases}
    sys.modules.update(aliases)
    try:
        spec = importlib.util.spec_from_file_location("sfari_bundle_fixture2", _FIXTURE)
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


@pytest.fixture()
def offline(tmp_path, fx, monkeypatch):
    folder = fx.build(tmp_path / "bundle")
    (folder / bundle.ABSENT_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                             encoding="utf-8")
    fx.build_v2(folder)
    fx.write_lookups(folder)
    bundle.set_source("bundle", folder)
    hr.clear_caches()
    fabric.clear_feature_memo()
    import requests

    def no_network(*args, **kwargs):
        raise AssertionError("a lookup asked a live service")
    monkeypatch.setattr(requests, "get", no_network)
    monkeypatch.setattr(requests, "post", no_network)
    yield folder
    bundle.set_source(None)
    hr.clear_caches()
    fabric.clear_feature_memo()


def test_v2_flowlines_from_the_bundle(offline, fx):
    attrs = fabric.attrs_from_feature(fabric.feature_by_comid(fx.ID[1]))
    assert attrs["drainage_area_sqkm"] == 5.0 and attrs["huc8"] == "02060005"
    box = fabric.features_in_bbox(*fx.box_deg(-10, -10, 510, 510))
    assert sorted(f["properties"]["comid"] for f in box) == sorted(fx.ID.values())


def test_nwi_within_150_m_of_the_reach(offline, fx):
    lon, lat = fx.point_deg(50, 25)
    record = compute_site(lat, lon, {"metricFamilies": []})
    got = nwi.wetlands_for_reach(lat, lon, record)                # the strips, never the box
    assert got["flowlines"] == 1 and got["halfWidthM"] == 150.0
    assert got["pctOfStrip"] == 50.0 and got["acres"] == pytest.approx(3.7, abs=0.05)
    assert got["bySystem"]["palustrine"] == pytest.approx(3.71, abs=0.02)
    assert got["stripAcres"] == pytest.approx(7.4, abs=0.1)
    assert nwi.wetlands_along_reach({"site": {"nhdplusId": fx.ID[1]}, "reach": {"geometry": None}}) is None
