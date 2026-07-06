"""Live integration tests for delineation (hit USGS NLDI).

Skipped by default; enable with EASI_LIVE_TESTS=1 (needs network).
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EASI_LIVE_TESTS") != "1",
    reason="live network test; set EASI_LIVE_TESTS=1 to run",
)


def test_delineate_mainstem_scioto():
    from easi import delineation
    d = delineation.run_delineation(39.9550, -83.0030)
    assert d.comid == 5218161
    assert d.gnis_name == "Scioto River"
    # watershed area should match NHDPlus totdasqkm closely
    assert d.watershed_area_sqkm is not None
    assert abs(d.watershed_area_sqkm - d.drainage_area_sqkm) / d.drainage_area_sqkm < 0.05
    assert d.reach_length_ft is not None and 950 <= d.reach_length_ft <= 1000.5
    assert d.watershed_geojson and d.reach_geojson


def test_delineate_headwater_short_reach_ok():
    from easi import delineation
    d = delineation.run_delineation(40.0962, -83.0203)
    assert d.comid is not None
    assert d.watershed_area_sqkm is not None and d.watershed_area_sqkm < 50
    assert d.reach_geojson is not None
