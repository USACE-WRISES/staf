"""The routed-site warning (2026-09-04): two plain sentences shared by the
report banner, the worksheet ribbon, and the PDF."""
from __future__ import annotations

from easi.notices import routed_warning


def _anchor(*, declined=True, ratio=32.02, code="surrogate_da_ratio_exceeded", routed_ft=1687.6):
    routing = {"method": "nldi-hydrolocation-raindrop", "routedDistanceFt": routed_ft,
               "daRatio": ratio, "daRatioLimit": 10.0, "declined": declined}
    if declined:
        routing["declineCode"] = code
    return {"anchorKind": "hrSurrogate",
            "clickedStream": {"gnisName": None, "snapDistFt": 3.2},
            "scoredReach": {"gnisName": "Mink Brook", "comid": 9327042},
            "routing": routing}


def _delin(source="site-engine", **eng):
    block = {"engineVersion": "0.2.2", "areaSqkm": 1.0005, "status": "ok"}
    block.update(eng)
    return {"watershed_source": source, "watershed_engine": block, "comid": 9327042,
            "gnis_name": "(unnamed stream)"}


def _plain(w):
    assert w["title"] == "Warning"
    for line in w["lines"]:
        assert "\u2014" not in line and ";" not in line and "v0.2" not in line
        assert len(line) < 170
    return w["lines"]


def test_declined_site_engine_warning_is_two_sentences():
    lines = _plain(routed_warning(_anchor(), _delin()))
    assert lines[0] == ("This stream is not in the StreamCat lookup network. Watershed "
                        "metrics use the exact watershed from the STAF site engine (1.00 km\u00b2).")
    assert lines[1] == ("Three metrics could not be scored: low flow, substrate, and biological "
                        "integrity. The nearest StreamCat reach drains 32 times this stream, "
                        "past the limit of 10.")


def test_within_bound_names_the_reach():
    lines = _plain(routed_warning(_anchor(declined=False, ratio=2.7), _delin()))
    assert lines[1] == ("Three metrics come from the nearest StreamCat reach, Mink Brook "
                        "(COMID 9327042), 1,688 ft downstream: low flow, substrate, and "
                        "biological integrity.")


def test_unknown_drainage_area_says_so():
    lines = _plain(routed_warning(_anchor(ratio=None, code="surrogate_da_unavailable"), _delin()))
    assert lines[1].endswith("The drainage area needed to check the substitution limit is unknown.")


def test_not_calculated_and_legacy_variants():
    lines = _plain(routed_warning(_anchor(), _delin("not-calculated", reason="walk budget exceeded")))
    assert lines[0].startswith("This stream is not in the StreamCat lookup network, and the STAF "
                               "site engine could not calculate its watershed (walk budget exceeded).")
    assert "Watershed metrics are unavailable." in lines[0]
    legacy = _plain(routed_warning(_anchor(declined=False, ratio=2.7),
                                   {"watershed_source": "v2-basin", "gnis_name": "Mink Brook"}))
    assert legacy == ["This stream is not in the StreamCat lookup network. Results describe "
                      "Mink Brook, 1,688 ft downstream, not the clicked stream (drainage area "
                      "ratio 2.7, limit 10)."]


def test_covered_network_has_no_warning():
    assert routed_warning({"anchorKind": "v2Direct"}, _delin()) is None
    assert routed_warning(None, None) is None
