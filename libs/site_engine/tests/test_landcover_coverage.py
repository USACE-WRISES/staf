"""NLCD outside its own footprint (2026-09-08).

The land-cover layer covers the conterminous United States only, and the
service answers a polygon beyond it without complaining. Measured on
2026-09-08: a watershed straddling the Quebec border comes back with cells of
0, which is no NLCD class, and ``cover_statistics`` raises on it; a watershed
in Alaska or Hawaii comes back entirely nodata (127) and ``cover_statistics``
returns nothing, which the engine then reported as zero percent of every class
with a NaN impervious.

These are offline: pygeohydro is a stub returning the rasters those places
actually produce.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
import xarray as xr
from shapely.geometry import box

from site_engine.metrics import landcover as lc

#: any real polygon: the stub ignores it, geopandas does not
GEOM = box(-83.06, 40.30, -83.04, 40.32)

# the real table's shape, including the one entry with no space before the dash
_CLASSES = {
    "11": "Open Water - All areas of open water.",
    "22": "Developed, Low Intensity -Includes areas with a mixture of ...",
    "41": "Deciduous Forest - Areas dominated by trees ...",
    "82": "Cultivated Crops - Areas used to produce annual crops ...",
    "90": "Woody Wetlands - Areas where forest or shrubland vegetation ...",
    "95": "Emergent Herbaceous Wetlands - Areas where perennial herbaceous ...",
    "127": "Nodata",
}


def _raster(cover_values, impervious_values):
    cover = xr.DataArray(np.array(cover_values, dtype="uint8"), dims=("i",))
    imp = xr.DataArray(np.array(impervious_values, dtype="float64"), dims=("i",))
    return {"cover_2021": cover, "impervious_2021": imp}


def _stub(monkeypatch, dataset, *, statistics=None, raises=None):
    """Stand in for pygeohydro with a raster the service really returns."""
    mod = types.ModuleType("pygeohydro")

    def nlcd_bygeom(gs, resolution=30, years=None):
        return {"0": dataset}

    def cover_statistics(da):
        if raises is not None:
            raise raises
        return types.SimpleNamespace(classes=dict(statistics or {}))

    mod.nlcd_bygeom = nlcd_bygeom
    mod.cover_statistics = cover_statistics
    mod.helpers = types.SimpleNamespace(nlcd_helper=lambda: {"classes": _CLASSES})
    monkeypatch.setitem(sys.modules, "pygeohydro", mod)
    return mod


def test_a_covered_watershed_takes_the_unchanged_path(monkeypatch):
    """The conterminous case must not notice this change at all."""
    _stub(monkeypatch,
          _raster([41, 41, 82, 90], [0.0, 10.0, 5.0, 1.0]),
          statistics={"Deciduous Forest": 50.0, "Cultivated Crops": 25.0,
                      "Woody Wetlands": 25.0})
    out = lc._stats_for(GEOM)
    assert out["forestPct"] == 50.0 and out["cropPct"] == 25.0
    assert out["woodyWetlandPct"] == 25.0
    assert out["imperviousPct"] == 4.0
    # no coverage note is attached when the polygon is covered
    assert "_coveredFraction" not in out


def test_a_border_watershed_scores_the_covered_part(monkeypatch):
    """Cells of 0 are Canada: cover_statistics raises, so the second pass runs.

    Half the polygon is covered, so the classes are percentages of that half
    and the fraction says so.
    """
    _stub(monkeypatch,
          _raster([0, 0, 41, 82], [127.0, 127.0, 10.0, 0.0]),
          raises=ValueError("Given ds is invalid. Valid options are: 11 12 ..."))
    out = lc._stats_for(GEOM)
    assert out is not None
    assert out["_coveredFraction"] == 0.5
    assert out["forestPct"] == 50.0 and out["cropPct"] == 50.0
    # 127 is nodata, not a 127 percent impervious surface
    assert out["imperviousPct"] == 5.0


def test_an_uncovered_watershed_reports_nothing_rather_than_zero(monkeypatch):
    """Alaska and Hawaii: every cell nodata, and no exception is raised.

    Before this the engine returned zero percent of every class, which reads as
    measurement. Absence has to look like absence.
    """
    _stub(monkeypatch,
          _raster([127, 127, 127], [np.nan, np.nan, np.nan]),
          statistics={})                      # what cover_statistics really gives
    assert lc._stats_for(GEOM) is None


def test_class_names_and_the_impervious_mask(monkeypatch):
    _stub(monkeypatch, _raster([41], [0.0]))
    names = lc._nlcd_class_names()
    assert names[41] == "Deciduous Forest"
    assert names[22] == "Developed, Low Intensity"      # no space before the dash
    assert lc.NLCD_NODATA not in names                  # nodata is not a class
    imp = xr.DataArray(np.array([0.0, 50.0, 127.0], dtype="float64"), dims=("i",))
    assert lc._mean_impervious(imp) == 25.0             # the 127 is dropped
    empty = xr.DataArray(np.array([np.nan], dtype="float64"), dims=("i",))
    assert lc._mean_impervious(empty) is None
    assert lc._class_sums({"Woody Wetlands": 2.0,
                           "Emergent Herbaceous Wetlands": 1.0}) \
        ["woodyWetlandPct"] == 2.0


def test_compute_states_the_covered_fraction_and_never_scores_it(monkeypatch):
    record = {"watershed": {"polygon": {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {
            "type": "Polygon",
            "coordinates": [[[-83.06, 40.30], [-83.04, 40.30],
                             [-83.04, 40.32], [-83.06, 40.32], [-83.06, 40.30]]]}}]}},
        "input": {"config": {}}}
    monkeypatch.setattr(lc, "_stats_for",
                        lambda geom: {"imperviousPct": 5.0, "cropPct": 50.0,
                                      "hayPasturePct": 0.0, "forestPct": 45.0,
                                      "shrubPct": 0.0, "grasslandPct": 0.0,
                                      "woodyWetlandPct": 0.0, "herbWetlandPct": 0.0,
                                      "_coveredFraction": 0.78})
    out = lc.compute(record, [])
    assert "_coveredFractionWatershed" not in out       # a note, never a metric
    entry = out["cropPctWatershed"]
    assert entry["value"] == 50.0
    assert any("78 percent" in w for w in entry["warnings"])

    monkeypatch.setattr(lc, "_stats_for", lambda geom: None)
    out = lc.compute(record, [])
    warn = out["landcoverWatershedUnavailable"]["warnings"][0]
    assert "no land cover" in warn and "footprint" in warn
