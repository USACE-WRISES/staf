"""The Identify card for a stream outside the StreamCat network (2026-09-02).

Three short lines, the caveat in one sentence, the numbers behind the info
icon: the click at Mink Brook read as an error because the card repeated the
routing message in a yellow box with the ratio and the limit inline.
"""
from __future__ import annotations

from easi.snapcard import hr_snap_card


def _anchor(*, declined: bool, ratio=31.59, code="surrogate_da_ratio_exceeded",
            snap_ft=3.2, routed_ft=1687.6):
    routing = {"method": "nldi-hydrolocation-raindrop", "routedDistanceFt": routed_ft,
               "daRatio": ratio, "daRatioLimit": 10.0, "declined": declined}
    if declined:
        routing["declineCode"] = code
        routing["declineMessage"] = "the routing message"
    return {"anchorKind": "hrSurrogate",
            "clickedStream": {"gnisName": None, "snapDistFt": snap_ft},
            "scoredReach": {"gnisName": "Mink Brook", "comid": 5214461},
            "routing": routing}


def _lines(card):
    return [text for _cls, text in card["lines"]]


def test_declined_card_is_three_short_lines_with_the_numbers_in_the_tip():
    card = hr_snap_card(_anchor(declined=True))
    classes = [cls for cls, _ in card["lines"]]
    lines = _lines(card)
    assert card["declined"] is True
    assert classes == ["ok", "", "warn"]
    assert lines[0] == "✓ Snapped to an unnamed stream (3 ft away). Not in the StreamCat lookup network."
    assert lines[1].startswith("EASI will compute the exact watershed with the STAF site engine")
    assert lines[2] == "Three reach metrics are unavailable here."
    for line in lines:
        assert "31.59" not in line and "COMID" not in line
        assert len(line) < 140
    tip = card["tip_html"]
    assert "low flow, substrate, and biological integrity" in tip
    assert "drains 31.59 times this stream" in tip and "The limit is 10" in tip
    assert "Mink Brook (COMID 5214461)" in tip
    assert "eight watershed metrics" in tip and "SFARI and DEEP" in tip


def test_within_bound_card_names_the_reach_and_distance():
    card = hr_snap_card(_anchor(declined=False, ratio=2.69))
    lines = _lines(card)
    assert card["declined"] is False
    assert [cls for cls, _ in card["lines"]] == ["ok", "", ""]
    assert lines[2] == "Reach evidence from Mink Brook (COMID 5214461), 1,688 ft downstream."
    assert "drains 2.69 times this stream (limit 10)" in card["tip_html"]


def test_unknown_drainage_area_explains_itself():
    card = hr_snap_card(_anchor(declined=True, ratio=None, code="surrogate_da_unavailable"))
    assert _lines(card)[2] == "Three reach metrics are unavailable here."
    assert "Drainage area is unknown" in card["tip_html"]


def test_copy_is_plain_and_tolerates_missing_fields():
    for anchor in (_anchor(declined=True), _anchor(declined=False),
                   {"routing": {"declined": True}}, {}):
        card = hr_snap_card(anchor)
        assert len(card["lines"]) == 3
        for _cls, text in card["lines"]:
            assert "—" not in text and ";" not in text
        assert "—" not in card["tip_html"]
    named = _anchor(declined=False)
    named["clickedStream"] = {"gnisName": "Sugar Run", "snapDistFt": None}
    assert _lines(hr_snap_card(named))[0].startswith("✓ Snapped to Sugar Run.")
