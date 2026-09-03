"""The stream network on the map (2026-09-02, one network since 2026-09-03).

Uncovered (NHDPlus HR) streams drew as 1.2 px pale blue, nearly invisible on
the topo basemap and easy to confuse with the covered lines. Now the HR
geometry draws once, split by the click rule: dark blue where the StreamCat
lookup engine scores the reach, cyan where the STAF site engine answers, both
at the same weight; the scored reach glows under the pin after a click; the
grey dashed route line is the only dashed one. The miss copy names the colors
and the engines.
"""
from __future__ import annotations

import pytest

app = pytest.importorskip("app")


def test_the_two_colors_are_legible_and_distinct():
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert app.FLOWLINE_STYLE["weight"] >= 3
    # the same weight: at 2 px the cyan lines were still hard to click
    # consistently (2026-09-02)
    assert app.HR_FLOWLINE_STYLE["weight"] == app.FLOWLINE_STYLE["weight"]
    assert app.HR_FLOWLINE_STYLE["opacity"] >= 0.85


def test_no_hover_style_on_the_stream_layers():
    # A hover restyle made every fetch's layer rebuild visible as a flash
    # (2026-09-02); the pointer cursor alone marks a clickable line.
    assert not hasattr(app, "FLOWLINE_HOVER_STYLE")
    assert not hasattr(app, "HR_FLOWLINE_HOVER_STYLE")


def test_only_the_route_line_is_dashed():
    assert "dashArray" in app.ROUTE_STYLE
    assert "dashArray" not in app.FLOWLINE_STYLE
    assert "dashArray" not in app.HR_FLOWLINE_STYLE
    assert "dashArray" not in app.SCORED_REACH_STYLE


def test_the_scored_reach_is_a_glow_under_the_covered_line():
    assert app.SCORED_REACH_STYLE["color"] == app.FLOWLINE_STYLE["color"]
    assert app.SCORED_REACH_STYLE["weight"] > app.FLOWLINE_STYLE["weight"]
    assert app.SCORED_REACH_STYLE["opacity"] <= 0.5


def test_layer_labels_name_the_engines():
    assert "StreamCat lookup engine" in app.LAYER_COVERED
    assert "STAF site engine" in app.LAYER_UNCOVERED
    assert app.LAYER_SCORED == "Scored reach"


def test_the_miss_copy_names_the_colors_and_the_engines():
    from pathlib import Path
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "dark blue lines are scored by the StreamCat lookup engine" in app._MISS_TEXT
    assert "cyan lines get an exact watershed from the STAF site engine" in app._MISS_TEXT
    assert "—" not in app._MISS_TEXT and ";" not in app._MISS_TEXT
    assert "thin lines get a calculated watershed" not in src
    assert "blue stream line" not in src and "Click a blue stream" not in src
