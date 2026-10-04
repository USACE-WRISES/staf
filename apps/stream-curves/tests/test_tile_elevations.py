"""3DEP elevations from USGS's own tile files first (``terrain.tile_elevations``), offline.

The vendored site engine's tile reader (``dem_tiles.best_tile_dem``) is replaced by a synthetic
EPSG:5070 grid (elevation = 100 m + x / 1000), so a transect and a point read exact, known values
without a network; with the reader off (no catalogs, the default) the services answer as before.
"""
from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from pyproj import Transformer

import streamcurves.datasources as ds
import streamcurves.datasources.dep3 as dep3
import streamcurves.terrain as terrain
from streamcurves._vendor.site_engine._extracted import dem_tiles

LON, LAT = -83.0, 40.0
TO_5070 = Transformer.from_crs(4326, 5070, always_xy=True)


def _grid():
    x0, y0 = TO_5070.transform(LON, LAT)
    xs = np.arange(x0 - 200.0, x0 + 200.0, 1.0)
    ys = np.arange(y0 + 200.0, y0 - 200.0, -1.0)
    elev = 100.0 + np.tile(xs / 1000.0, (ys.size, 1))
    return xr.DataArray(elev[None, :, :], dims=("band", "y", "x"), coords={"band": [1], "y": ys, "x": xs})


@pytest.fixture()
def tiles(monkeypatch):
    calls = []

    def fake(buf):
        calls.append(buf)
        return _grid(), 1, {"source": "test"}
    monkeypatch.setattr(dem_tiles, "best_tile_dem", fake)
    return calls


@pytest.fixture(autouse=True)
def fresh_cache():
    ds.clear_ds_cache()
    yield
    ds.clear_ds_cache()


def _expected(lon, lat):
    x, _ = TO_5070.transform(lon, lat)
    return 100.0 + x / 1000.0


def test_a_transect_reads_the_tiles(tiles, monkeypatch):
    def no_service(*args, **kwargs):
        raise AssertionError("the 3DEP service was asked")
    monkeypatch.setattr(terrain, "_post_json", no_service)
    pts = np.column_stack([np.linspace(LON - 0.001, LON + 0.001, 9), np.full(9, LAT)])
    got = terrain.sample_transect_3dep(pts)
    assert got["resolution_m"] == 1.0 and len(tiles) == 1
    assert got["elevs"] == pytest.approx([_expected(x, LAT) for x in pts[:, 0]], abs=1e-6)
    assert got["stations"][0] == 0.0 and got["stations"][-1] == pytest.approx(170.8, abs=1.0)
    stations = np.arange(9) * 10.0
    assert np.array_equal(terrain.sample_transect_3dep(pts, stations)["stations"], stations)


def test_a_point_elevation_reads_the_tiles(tiles, monkeypatch):
    def no_service(*args, **kwargs):
        raise AssertionError("EPQS was asked")
    monkeypatch.setattr(dep3, "_get_json", no_service)
    assert ds.epqs_elev(LON + 0.0005, LAT) == pytest.approx(_expected(LON + 0.0005, LAT), abs=1e-6)


def test_without_tiles_the_services_answer(monkeypatch):
    monkeypatch.setattr(dem_tiles, "best_tile_dem", lambda buf: None)
    asked = []
    monkeypatch.setattr(dep3, "_get_json", lambda url, params=None, **kw: asked.append(url) or {"value": "252.61"})
    assert ds.epqs_elev(LON, LAT) == 252.61 and asked
    assert terrain.tile_elevations([[LON, LAT]]) is None
