"""DEEP's regional bankfull curves (deep.bieger) need the physiographic divisions.

``division_at`` swallows every error and returns None, which selects the national
curve, so a missing or unreadable divisions file fails silently. These tests fail
instead: the file must load, and points inside a division must get its curve.
"""
import pytest

from deep import bieger


def test_divisions_file_loads():
    assert bieger._GEOJSON.exists(), bieger._GEOJSON
    gdf = bieger._divisions()
    names = set(gdf["DIVISION"].astype(str).str.strip().str.upper())
    assert set(bieger._DIV_ABBR) <= names


@pytest.mark.parametrize("lat, lon, abbr", [
    (41.5, -93.6, "IPL"),     # central Iowa, Interior Plains
    (38.9, -79.8, "AHI"),     # West Virginia, Appalachian Highlands
    (39.7, -105.6, "RMS"),    # Colorado Front Range, Rocky Mountain System
    (38.9, -76.6, "APL"),     # Maryland coastal plain, Atlantic Plain
])
def test_points_inside_a_division_get_its_curve(lat, lon, abbr):
    assert bieger.division_at(lat, lon) == abbr
    bf = bieger.bankfull_geometry(100.0, lat, lon)
    assert bf["division"] == abbr and bf["regional"] is True
    assert bf["width_m"] == round(bieger._power(bieger.COEF[abbr]["width"], 100.0), 2)


def test_outside_conus_takes_the_national_curve():
    bf = bieger.bankfull_geometry(100.0, 21.3, -157.8)   # Honolulu
    assert bf["division"] == "USA" and bf["regional"] is False
