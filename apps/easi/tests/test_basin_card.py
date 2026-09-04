"""The Basin pane on a routed site (2026-09-03, 2026-09-04).

The drainage area row printed the HR value as served (0.9871999900000001 km²),
a "Reach-keyed evidence: unavailable past the substitution limit" row said
nothing a reader could use, and the site-engine variant added an engine walk
count, the polygon area beside the drainage area, and the decline rule's
ratio. The pane now formats its numbers through the basin helpers and keeps
the engine row, the drainage area once, the reach length, and the covered
reach's COMID only when that reach supplies evidence. The snap card's
tooltip, the ribbon, and the report carry the rest. The card is a Shiny
render, so this reads the source.
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
    assert 'basin.fmt_km2(d.get("drainage_area_sqkm"))' in card
    assert 'basin.fmt_ft(d.get("reach_length_ft"))' in card
    assert "drainage_area_sqkm\")} km" not in card


def test_card_is_lean_on_routed_sites():
    card = _card_source()
    for gone in ('row("Reach-keyed evidence"', "unavailable past the substitution limit",
                 '"Reaches walked"', '"Exact watershed area"', '"Drainage area ratio"',
                 "Nearest covered reach COMID"):
        assert gone not in card, gone
    assert 'row("Watershed engine"' in card
    # the covered reach's COMID only when it supplies evidence
    assert 'None if r.get("declined")' in card
    assert 'row("Evidence reach COMID", d.get("comid"))' in card
    assert 'comid_row = row("COMID", d.get("comid"))' in card
