"""Offline tests for the basin-characteristics helper."""
from __future__ import annotations

from easi import basin
from easi.metrics.base import AnalysisContext


def test_basin_characteristics_from_ctx():
    ctx = AnalysisContext(lat=40, lon=-83, comid=1, drainage_area_sqkm=12.3,
                          slope=0.0042, stream_order=3, sinuosity=1.35)
    ctx.extras["reach_geomorph"] = {"bankfull_width_m": 10.7, "bankfull_depth_m": 1.03,
                                    "entrenchment_ratio": 2.4, "bank_height_ratio": 1.1}
    ctx.extras["streamcat"] = {"tmean8110ws": 11.2, "elevws": 312.0}
    rows = basin.basin_characteristics(ctx)["rows"]
    labels = [r[0] for r in rows]
    for expected in ("Drainage area", "Channel slope", "Stream order", "Sinuosity",
                     "Mean basin elevation"):
        assert expected in labels
    # bankfull/ER/BHR live in the report's cross-section table; mean annual air temp was
    # dropped from the report as not needed for screening
    for absent in ("Bankfull width × depth", "Entrenchment ratio", "Bank-height ratio",
                   "Mean annual air temp"):
        assert absent not in labels
    assert all(isinstance(r[1], str) for r in rows)   # JSON-safe strings


def test_basin_characteristics_empty_ctx():
    ctx = AnalysisContext(lat=40, lon=-83, comid=1)
    assert basin.basin_characteristics(ctx)["rows"] == []
