"""EPA Level III ecoregion resolution + the basin ecoregion row (offline; bundled polygons)."""
from __future__ import annotations

from easi import basin, geo
from easi.metrics.base import AnalysisContext


def test_level3_at_returns_code_and_name_for_conus_point():
    # a point in the Snake River Plain, Idaho (agricultural) -> a resolved EPA Level III ecoregion
    r = geo.level3_at(42.57, -114.37)
    assert r is not None
    assert r["code"] and r["name"]            # non-empty code + name
    assert isinstance(r["code"], str)


def test_level3_at_none_outside_conus():
    assert geo.level3_at(0.0, 0.0) is None      # Gulf of Guinea (ocean)
    assert geo.level3_at(None, None) is None


def test_basin_characteristics_includes_ecoregion(monkeypatch):
    monkeypatch.setattr(geo, "level3_at",
                        lambda lat, lon: {"code": "58", "name": "Northeastern Highlands"})
    ctx = AnalysisContext(lat=44.0, lon=-71.5, comid=1, drainage_area_sqkm=10.0)
    rows = basin.basin_characteristics(ctx)["rows"]
    val = next((v for lbl, v in rows if lbl == "EPA ecoregion (Level III)"), None)
    assert val is not None and "Northeastern Highlands" in val and "58" in val
