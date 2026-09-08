"""The Identify card for a stream outside the StreamCat network (2026-09-02,
never a warning since 2026-09-06).

Three short lines, the numbers behind the info icon: the click at Mink Brook
read as an error because the card repeated the routing message in a yellow
box with the ratio and the limit inline. The three reach metrics now always
come from the nearest covered reach, so the third line names it and the tip
states the drainage-area ratio without a limit.
"""
from __future__ import annotations

from easi.snapcard import hr_snap_card


def _anchor(*, ratio=31.59, snap_ft=3.2, routed_ft=1687.6):
    routing = {"method": "nldi-hydrolocation-raindrop", "routedDistanceFt": routed_ft,
               "daRatio": ratio, "daRatioLimit": 10.0, "declined": False}
    return {"anchorKind": "hrSurrogate",
            "clickedStream": {"gnisName": None, "snapDistFt": snap_ft},
            "scoredReach": {"gnisName": "Mink Brook", "comid": 5214461},
            "routing": routing}


def _lines(card):
    return [text for _cls, text in card["lines"]]


def test_card_is_three_short_lines_with_the_numbers_in_the_tip():
    card = hr_snap_card(_anchor())
    classes = [cls for cls, _ in card["lines"]]
    lines = _lines(card)
    assert "declined" not in card
    assert classes == ["ok", "", ""]
    assert lines[0] == "\u2713 Snapped to an unnamed stream (3 ft away)."
    assert lines[1] == ("The STAF site engine calculates the HR reach watershed, usually in "
                        "under a minute.")
    # the third line fits the pane on one line, in the legend's words (2026-09-07)
    assert lines[2] == "Reach evidence: Mink Brook, 1,688 ft downstream."
    assert len(lines[2]) <= 50
    for line in lines:
        assert "31" not in line and "COMID" not in line and "StreamCat" not in line
        assert len(line) < 90
    tip = card["tip_html"]
    assert tip.startswith('<div class="easi-tip-title">Reach evidence</div>')
    assert ("Low flow, substrate, and biological integrity come from Mink Brook "
            "(COMID 5214461), the nearest StreamCat reach, 1,688 ft downstream.") in tip
    assert "It drains 32 times this stream." in tip
    assert tip.count("easi-tip-sec") == 1                       # two sentences, nothing else
    assert "limit" not in tip and "unavailable" not in tip
    for gone in ("Everything else", "eight watershed metrics", "SFARI and DEEP",
                 "not on the StreamCat network"):
        assert gone not in tip, gone


def test_within_bound_ratio_keeps_one_decimal():
    card = hr_snap_card(_anchor(ratio=2.69))
    assert _lines(card)[2] == "Reach evidence: Mink Brook, 1,688 ft downstream."
    assert "It drains 2.7 times this stream." in card["tip_html"]


def test_unknown_ratio_or_distance_is_left_out():
    card = hr_snap_card(_anchor(ratio=None, routed_ft=None))
    assert _lines(card)[2] == "Reach evidence: Mink Brook, downstream."
    assert "drains" not in card["tip_html"]
    assert "Mink Brook (COMID 5214461), the nearest StreamCat reach." in card["tip_html"]


def test_copy_is_plain_and_tolerates_missing_fields():
    for anchor in (_anchor(), _anchor(ratio=None), {"routing": {"declined": True}}, {}):
        card = hr_snap_card(anchor)
        assert len(card["lines"]) == 3
        for _cls, text in card["lines"]:
            assert "\u2014" not in text and ";" not in text
        assert "\u2014" not in card["tip_html"]
    named = _anchor()
    named["clickedStream"] = {"gnisName": "Sugar Run", "snapDistFt": None}
    assert _lines(hr_snap_card(named))[0].startswith("\u2713 Snapped to Sugar Run.")
