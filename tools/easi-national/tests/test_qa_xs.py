"""The parity report's cross-section columns: the comparison, the live
resolution, the reach distance and the summary lines."""
from __future__ import annotations

from shapely.geometry import LineString

from builder import qa


def test_xs_compare_reads_both_sides_and_the_live_resolution():
    offline = {"entrenchment_ratio": 2.31, "bank_height_ratio": 1.2, "n_transects": 9, "dem_resolution_m": 1}
    live = {"entrenchment_ratio": 2.3, "bank_height_ratio": 1.25, "n_transects": 9,
            "geom": {"dem_resolution_m": 1, "dem_source": "USGS 3DEP 1 m DEM"}}
    out = qa.xs_compare(offline, live)
    assert out["er_diff"] == 0.01 and out["bhr_diff"] == 0.05 and out["res_same"]
    assert qa.live_resolution({"geom": {"dem_resolution_m": 10.0}}) == 10
    assert qa.xs_compare(None, None) == {"offline": {"er": None, "bhr": None, "n": None, "res": None},
                                         "live": {"er": None, "bhr": None, "n": None, "res": None},
                                         "er_diff": None, "bhr_diff": None, "res_same": True}


def test_reach_hausdorff_and_the_report_lines():
    stored = LineString([(-78.5, 38.0), (-78.5, 38.003)])
    live = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {
        "type": "LineString", "coordinates": [(-78.5, 38.0), (-78.5, 38.0031)]}}]}
    d = qa.reach_hausdorff_m(stored, live)
    assert d is not None and 5 < d < 20                      # about 11 m of extra reach
    assert qa.reach_hausdorff_m(None, live) is None
    report = {"huc8": "02080204", "n": 2, "xs_mode": "10m", "results": [
        {"comid": 1, "eci_live": 0.6, "eci_offline": 0.6, "diffs": [], "xs_identical": True,
         "xs": {"offline": {"er": 2.3, "bhr": 1.2, "n": 9, "res": 10}, "live": {"er": 2.3, "bhr": 1.2, "n": 9, "res": 10},
                "er_diff": 0.0, "bhr_diff": 0.0, "res_same": True, "live_one_metre": True},
         "reach_hausdorff_m": 1.2},
        {"comid": 2, "eci_live": 0.5, "eci_offline": 0.52,
         "diffs": [{"metric": "floodplain-connectivity", "metric_id": qa.XS_METRICS[0], "live": "Fair", "offline": "Good"}],
         "xs_identical": False,
         "xs": {"offline": {"er": 2.3, "bhr": 1.2, "n": 9, "res": 1}, "live": {"er": 1.9, "bhr": 1.4, "n": 9, "res": 10},
                "er_diff": 0.4, "bhr_diff": 0.2, "res_same": False, "live_one_metre": False},
         "reach_hausdorff_m": 3.0}]}
    text = qa.format_report(report)
    assert "cross-section ratings identical: 1/2; same DEM resolution: 1/2; live would use 1 m on 1/2" in text
    assert "median ER difference 0.0" in text and "median reach Hausdorff 2.1 m" in text
    assert "identical reaches: 1/2" in text and "floodplain-connectivity: 1 differences" in text
