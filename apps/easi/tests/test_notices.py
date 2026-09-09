"""The routed-site note (2026-09-04, note form 2026-09-06): two plain
sentences shared by the report banner and the PDF (the worksheet ribbon went
2026-09-07), and the one-sentence per-metric provenance for a borrowed
COMID-keyed row."""
from __future__ import annotations

from easi import basin
from easi.notices import borrowed_note, marker_note, routed_notice


def _anchor(*, ratio=32.02, routed_ft=1687.6, reach_name="Mink Brook", comid=9327042):
    routing = {"method": "nldi-hydrolocation-raindrop", "routedDistanceFt": routed_ft,
               "daRatio": ratio, "daRatioLimit": 10.0, "declined": False}
    return {"anchorKind": "hrSurrogate",
            "clickedStream": {"gnisName": None, "snapDistFt": 3.2},
            "scoredReach": {"gnisName": reach_name, "comid": comid},
            "routing": routing}


def _delin(source="site-engine", **eng):
    block = {"engineVersion": "0.2.2", "areaSqkm": 1.0005, "status": "ok"}
    block.update(eng)
    return {"watershed_source": source, "watershed_engine": block, "comid": 9327042,
            "gnis_name": "(unnamed stream)"}


def _plain(w):
    assert w["title"] == "Note"
    for line in w["lines"]:
        assert "\u2014" not in line and ";" not in line and "v0.2" not in line
        assert "limit" not in line and "Warning" not in line and "unavailable" not in line
        assert len(line) < 170
    return w["lines"]


def test_site_engine_note_is_one_short_sentence_whatever_the_ratio():
    """Since 2026-09-08 the note says only where the watershed metrics come
    from. Which rows are borrowed is a marker on those rows (marker_note)."""
    lines = _plain(routed_notice(_anchor(), _delin()))
    assert lines == [
        "This stream is outside the StreamCat network. Watershed metrics use the HR reach "
        "watershed (1.00 km\u00b2) from the STAF site engine."]
    # the ratio never reaches the banner, past the bound, within it, or unknown
    assert lines == _plain(routed_notice(_anchor(ratio=2.7), _delin()))
    assert lines == _plain(routed_notice(_anchor(ratio=None), _delin()))
    assert not any("Low flow" in line for line in lines)


def test_marker_note_says_where_the_marked_rows_come_from():
    """It names no metric: the borrowed set is not fixed, since nutrients and
    regulatory impairment join it whenever their StreamCat fallback fires."""
    assert marker_note(_anchor()) == ("Comes from the nearest StreamCat reach, "
                                      "1,688 ft downstream.")
    assert marker_note(_anchor(routed_ft=None)) == ("Comes from the nearest StreamCat "
                                                    "reach, downstream.")
    assert marker_note(_anchor(ratio=None)) == marker_note(_anchor())   # no ratio in it
    for text in (marker_note(_anchor()), marker_note(_anchor(routed_ft=None))):
        assert "\u2014" not in text and ";" not in text and len(text) < 170
        assert "Low flow" not in text and "substrate" not in text


def test_marker_note_is_empty_off_a_routed_site():
    assert marker_note({"anchorKind": "v2Direct"}) == ""
    assert marker_note(None) == "" and marker_note({}) == ""


def test_not_calculated_and_legacy_variants():
    lines = routed_notice(_anchor(), _delin("not-calculated", reason="walk budget exceeded"))["lines"]
    assert lines == [("This stream is outside the StreamCat network, and the STAF site "
                      "engine could not calculate its watershed (walk budget exceeded). "
                      "Watershed metrics are unavailable.")]
    legacy = routed_notice(_anchor(ratio=2.7),
                           {"watershed_source": "v2-basin", "gnis_name": "Mink Brook"})
    assert legacy["title"] == "Note"
    assert legacy["lines"] == ["This stream is outside the StreamCat network. Results describe "
                               "Mink Brook, 1,688 ft downstream, not the clicked stream "
                               "(drainage area ratio 2.7, limit 10)."]


def test_covered_network_has_no_note():
    assert routed_notice({"anchorKind": "v2Direct"}, _delin()) is None
    assert routed_notice(None, None) is None


def test_borrowed_note_names_the_reach_distance_and_ratio():
    assert borrowed_note(_anchor()) == (
        "Scored from the nearest StreamCat reach, Mink Brook (COMID 9327042), "
        "1,688 ft downstream, which drains 32 times this stream.")
    assert borrowed_note(_anchor(ratio=2.69)).endswith("which drains 2.7 times this stream.")
    assert borrowed_note(_anchor(ratio=None)) == (
        "Scored from the nearest StreamCat reach, Mink Brook (COMID 9327042), "
        "1,688 ft downstream.")
    assert borrowed_note(_anchor(routed_ft=None, reach_name=None)) == (
        "Scored from the nearest StreamCat reach (COMID 9327042), which drains 32 "
        "times this stream.")
    assert borrowed_note(_anchor(reach_name=None, comid=None, ratio=None, routed_ft=None)) == (
        "Scored from the nearest StreamCat reach.")
    assert borrowed_note({"anchorKind": "v2Direct"}) == "" and borrowed_note(None) == ""


def test_fmt_ratio_prints_whole_numbers_past_ten():
    assert basin.fmt_ratio(32.02) == "32" and basin.fmt_ratio(10.0) == "10"
    assert basin.fmt_ratio(2.69) == "2.7" and basin.fmt_ratio(0.96) == "1.0"
    assert basin.fmt_ratio(None) is None and basin.fmt_ratio("n/a") is None
