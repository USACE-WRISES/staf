"""The map legend: docked under the layers button, always visible on the
Identify and Basin steps, saying what each color means for the data, the state
of the stream fetch, and which reach the values come from. Pure builder, no
session.

Since 2026-09-08 the legend names consequences rather than engines, matching
SFARI and DEEP, so the tests here guard the absence of the engine vocabulary as
much as the presence of the new copy."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")


def _html(step="identify", zoomed=True, mode="segmented", scored=None, routed=False):
    return str(app._legend_ui(step, zoomed, mode, scored, routed) or "")


def test_the_rows_say_what_each_color_means_for_the_data():
    html = _html()
    assert "All data from this reach" in html
    assert "All data, some from downstream" in html
    assert app.FLOWLINE_STYLE["color"] in html and app.HR_FLOWLINE_STYLE["color"] in html
    assert app.FLOWLINE_STYLE["color"] != app.HR_FLOWLINE_STYLE["color"]
    assert "easi-legend-title" in html and "Zoom in" not in html


def test_no_engine_vocabulary_reaches_the_legend_or_the_layer_control():
    """The point of the change: an assessor never picks an engine, so the map
    must not ask them to learn the names. Provenance labels elsewhere still
    carry them, which is why this asserts only on the legend and the control."""
    everywhere = "".join([
        _html(), _html(zoomed=False, mode=None), _html(mode="v2-only"),
        _html(mode="hr-only"), _html(mode="empty"),
        _html(step="basin", scored={"comid": 5214461, "name": "Sugar Run"}),
        _html(scored={"comid": 5214461, "name": None}, routed=True),
        app.LAYER_COVERED, app.LAYER_UNCOVERED, app.LAYER_SCORED,
    ])
    for jargon in ("StreamCat", "STAF site engine", "COMID", "HR reach watershed"):
        assert jargon not in everywhere, jargon


def test_notes_follow_the_zoom_and_the_fetch_mode():
    assert "Zoom in to see streams" in _html(zoomed=False, mode=None)
    assert "Fine streams unavailable here. Zoom in." in _html(mode="v2-only")
    assert "No streams with all data in view." in _html(mode="hr-only")
    assert "No streams in view." in _html(mode="empty")
    assert "easi-legend-note" not in _html(mode=None)          # loading: no note


def test_scored_and_routed_rows_name_where_the_values_come_from():
    """Two states. On a covered reach the highlight is the clicked reach; on any
    other stream it is the reach downstream the borrowed values come from."""
    html = _html(scored={"comid": 5214461, "name": "Sugar Run"})
    assert "This reach: Sugar Run" in html
    assert "easi-legend-sw-glow" in html and app.SCORED_REACH_STYLE["color"] in html
    html = _html(scored={"comid": 5214461, "name": None}, routed=True)
    assert "Downstream reach: unnamed stream" in html
    assert "5214461" not in html                     # the COMID stays off the map
    assert "This reach:" not in _html() and "Downstream reach:" not in _html()


def test_basin_step_adds_the_watershed_and_reach_rows():
    html = _html(step="basin")
    assert "Watershed" in html and "Assessment reach" in html
    assert app.WATERSHED_STYLE["fillColor"] in html and app.REACH_STYLE["color"] in html
    assert "easi-legend-sw-fill" in html
    assert "Assessment reach" not in _html(step="identify")


def test_hidden_outside_the_map_steps():
    assert app._legend_ui("assess", True, "segmented", None, False) is None
    assert app._legend_ui("report", True, "segmented", None, False) is None


def test_copy_is_plain():
    import re
    for html in (_html(), _html(zoomed=False, mode=None), _html(mode="v2-only"),
                 _html(step="basin", scored={"comid": 1, "name": "x"}, routed=True)):
        text = re.sub(r"<[^>]+>", " ", html)        # the visible strings, not the markup
        assert "—" not in text and ";" not in text


def test_dock_script_and_cache_bust_are_wired():
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "legend-dock.js" in src and "styles.css?v=47" in src
    assert 'id="easi-legend-panel"' in src.replace("'", '"')
    js = (Path(app.__file__).parent / "www" / "legend-dock.js").read_text(encoding="utf-8")
    assert "leaflet-control-layers" in js and "disableClickPropagation" in js
    assert "easi-legend-panel" in js
