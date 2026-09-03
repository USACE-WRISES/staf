"""The two stream networks on the map (2026-09-02).

Uncovered (NHDPlus HR) streams drew as 1.2 px pale blue, nearly invisible on
the topo basemap and easy to confuse with the covered lines. Now: cyan at
weight 2, covered lines heavier, both thickening under the pointer, and the
grey dashed route line the only dashed one. The miss copy names the colors.
"""
from __future__ import annotations

import pytest

app = pytest.importorskip("app")


def test_the_two_networks_are_legible_and_distinct():
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert app.FLOWLINE_STYLE["weight"] >= 3
    assert app.HR_FLOWLINE_STYLE["weight"] >= 2
    assert app.HR_FLOWLINE_STYLE["opacity"] >= 0.85


def test_hover_thickens_each_network():
    assert app.FLOWLINE_HOVER_STYLE["weight"] > app.FLOWLINE_STYLE["weight"]
    assert app.HR_FLOWLINE_HOVER_STYLE["weight"] > app.HR_FLOWLINE_STYLE["weight"]


def test_only_the_route_line_is_dashed():
    assert "dashArray" in app.ROUTE_STYLE
    assert "dashArray" not in app.FLOWLINE_STYLE
    assert "dashArray" not in app.HR_FLOWLINE_STYLE


def test_the_miss_copy_names_the_colors():
    from pathlib import Path
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "dark blue lines have StreamCat data" in src
    assert "cyan lines get a calculated watershed" in src
    assert "thin lines get a calculated watershed" not in src
