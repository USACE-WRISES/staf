"""Soil K: area-weighted across map units, honest about uncovered area, and
labeled as the old map-unit mean on the fallback path."""
from __future__ import annotations

from site_engine.metrics import soils

_WS_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[
        [-83.06, 40.30], [-83.04, 40.30], [-83.04, 40.32], [-83.06, 40.32],
        [-83.06, 40.30]]]}}]}


def _record():
    return {"watershed": {"polygon": _WS_FC, "areaSqkm": 3.78}}


def test_area_weighted_mean(monkeypatch):
    monkeypatch.setattr(soils, "_query_areas",
                        lambda wkt: {"100": 900.0, "200": 100.0})
    monkeypatch.setattr(soils, "_query_kwfact_by_mukey",
                        lambda keys: {"100": [(0.1, 100.0)],
                                      "200": [(0.5, 60.0), (0.5, 40.0)]})
    e = soils.compute(_record(), [])["soilKFactor"]
    assert abs(e["value"] - 0.14) < 1e-6      # not the 0.3 map-unit mean
    assert "area-weighted" in e["source"]
    assert e["warnings"] == []


def test_map_units_without_k_leave_the_denominator(monkeypatch):
    monkeypatch.setattr(soils, "_query_areas",
                        lambda wkt: {"100": 500.0, "200": 500.0})
    monkeypatch.setattr(soils, "_query_kwfact_by_mukey",
                        lambda keys: {"100": [(0.2, 100.0)]})
    e = soils.compute(_record(), [])["soilKFactor"]
    assert abs(e["value"] - 0.2) < 1e-6
    assert any("50%" in w for w in e["warnings"])


def test_fallback_keeps_the_limitation(monkeypatch):
    monkeypatch.setattr(soils, "_query_areas", lambda wkt: None)
    monkeypatch.setattr(soils, "_query_kwfact",
                        lambda wkt: [(0.37, 85.0), (0.43, 90.0)])
    e = soils.compute(_record(), [])["soilKFactor"]
    assert e["value"] is not None
    assert soils._LIMITATION in e["warnings"]
    assert any("area weighting unavailable" in w for w in e["warnings"])
    assert "area-weighted" not in e["source"]
