"""Bieger fit-range flag: drainage areas outside the division's fitted data
range (Bieger et al. 2015, Table 2) mark the estimate as extrapolated."""
from __future__ import annotations

from easi import bieger


def test_national_range_boundaries():
    lo, hi = bieger.DA_FIT_RANGE_SQKM["USA"]
    assert (lo, hi) == (0.2, 155213.0)
    # inside
    assert bieger.bankfull_geometry(1.0)["extrapolated"] is False
    assert bieger.bankfull_geometry(lo)["extrapolated"] is False
    assert bieger.bankfull_geometry(hi)["extrapolated"] is False
    # outside, both directions
    assert bieger.bankfull_geometry(0.1)["extrapolated"] is True
    assert bieger.bankfull_geometry(200000)["extrapolated"] is True


def test_division_range_is_used(monkeypatch):
    # Pin the division so the check exercises a regional range (IHI: 78-2484).
    monkeypatch.setattr(bieger, "division_at", lambda lat, lon: "IHI")
    small = bieger.bankfull_geometry(2.0, 35.8, -84.2)
    assert small["division"] == "IHI"
    assert small["extrapolated"] is True
    assert small["fit_range_sqkm"] == [78.0, 2484.0]
    mid = bieger.bankfull_geometry(500.0, 35.8, -84.2)
    assert mid["extrapolated"] is False


def test_flag_rides_every_result():
    out = bieger.bankfull_geometry(10.0)
    assert set(("extrapolated", "fit_range_sqkm")) <= set(out)
