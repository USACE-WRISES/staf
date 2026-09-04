"""The Basin card on a routed site (2026-09-03).

The drainage area row printed the HR value as served (0.9871999900000001 km²)
and a "Reach-keyed evidence: unavailable past the substitution limit" row said
nothing a reader could use. The card now formats its numbers through the
basin helpers and drops that row; the snap card and the ribbon carry the
evidence story. The card is a Shiny render, so this reads the source.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

app = pytest.importorskip("app")


def _card_source() -> str:
    src = Path(app.__file__).read_text(encoding="utf-8")
    m = re.search(r"def basin_card\(\):.*?class_=\"easi-basin-card\"", src, re.S)
    assert m, "basin_card not found"
    return m.group(0)


def test_card_numbers_go_through_the_formatters():
    card = _card_source()
    assert 'basin.fmt_km2(eng.get("areaSqkm"))' in card
    assert 'basin.fmt_km2(d.get("drainage_area_sqkm"))' in card
    assert 'basin.fmt_ft(d.get("reach_length_ft"))' in card
    assert "drainage_area_sqkm\")} km" not in card


def test_card_has_no_reach_keyed_evidence_row():
    card = _card_source()
    assert 'row("Reach-keyed evidence"' not in card
    assert "unavailable past the substitution limit" not in card
    # the COMID row says what the reach is when the routing was declined
    assert '"Nearest covered reach COMID" if r.get("declined")' in card
