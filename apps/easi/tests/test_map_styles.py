"""Readable stream styles; the second color and source overlays are opt-in."""
from __future__ import annotations

import pytest

app = pytest.importorskip("app")


def test_optional_coverage_colors_are_legible_and_distinct():
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


def test_layer_labels_say_what_the_legend_says():
    assert app.LAYER_STREAMS == "Streams"
    assert app.LAYER_COVERAGE == "StreamCat coverage"
    assert app.LAYER_SCORED == "StreamCat source reach"


def test_the_miss_copy_is_one_short_instruction():
    # the legend names the colors and the engines; the toast only says what
    # to do (2026-09-04)
    from pathlib import Path
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert app._MISS_TEXT == ("No stream line within 150 ft of the click. "
                              "Zoom in and click a line.")
    assert "—" not in app._MISS_TEXT and ";" not in app._MISS_TEXT
    assert "thin lines get a calculated watershed" not in src
    assert "blue stream line" not in src and "Click a blue stream" not in src
