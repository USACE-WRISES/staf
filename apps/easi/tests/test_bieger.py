"""Offline tests for Bieger 2015 regional bankfull geometry + division lookup.

Values cross-checked against Bieger et al. (2015) Table 4 (predicted dimensions).
Uses the bundled data/physio_divisions.geojson — no network.
"""
from __future__ import annotations

import pytest

from easi import bieger


def test_national_curve_matches_paper():
    # Table 4: USA @ 10 km^2 -> width 6.07 m, depth 0.50 m
    r = bieger.bankfull_geometry(10)               # no location -> national
    assert r["division"] == "USA" and r["regional"] is False
    assert r["width_m"] == pytest.approx(6.07, abs=0.05)
    assert r["depth_m"] == pytest.approx(0.50, abs=0.02)


def test_interior_plains_division():
    # central Kansas -> Interior Plains; Table 4 @10 km^2: width 5.74, depth 0.58
    r = bieger.bankfull_geometry(10, 38.5, -98.0)
    assert r["division"] == "IPL" and r["regional"] is True
    assert r["width_m"] == pytest.approx(5.74, abs=0.1)
    assert r["depth_m"] == pytest.approx(0.58, abs=0.03)


def test_appalachian_division():
    r = bieger.bankfull_geometry(100, 37.5, -80.5)  # West Virginia
    assert r["division"] == "AHI"
    assert r["division_name"] == "Appalachian Highlands"


def test_off_grid_falls_back_to_national():
    r = bieger.bankfull_geometry(10, 0.0, 0.0)      # Atlantic Ocean / off CONUS
    assert r["division"] == "USA" and r["regional"] is False


def test_division_lookup_and_none():
    assert bieger.division_at(38.5, -98.0) == "IPL"
    assert bieger.division_at(None, None) is None


def test_geometry_increases_with_drainage_area():
    small = bieger.bankfull_geometry(5, 38.5, -98.0)
    big = bieger.bankfull_geometry(2000, 38.5, -98.0)
    assert big["width_m"] > small["width_m"] > 0
    assert big["depth_m"] > small["depth_m"] > 0
