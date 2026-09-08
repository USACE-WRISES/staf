"""The map legend (2026-09-07, EASI's): docked under the layers button,
always visible on the Identify and Basin steps, naming the two engines, the
state of the stream fetch, and the StreamCat reach. Pure builder, no session."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")


def _html(step="identify", zoomed=True, mode="segmented", reach=None, routed=False):
    return str(app._legend_ui(step, zoomed, mode, reach, routed) or "")


def test_names_both_engines_with_their_colors():
    html = _html()
    assert "StreamCat lookup engine" in html and "COMID-keyed values from this reach" in html
    assert "STAF site engine" in html and "the HR reach watershed, every stream" in html
    assert app.FLOWLINE_STYLE["color"] in html and app.HR_FLOWLINE_STYLE["color"] in html
    assert app.FLOWLINE_STYLE["color"] != app.HR_FLOWLINE_STYLE["color"]
    assert "easi-legend-title" in html and "Zoom in" not in html


def test_notes_follow_the_zoom_and_the_fetch_mode():
    assert "Zoom in to see streams" in _html(zoomed=False, mode=None)
    assert "Fine streams unavailable here. Zoom in." in _html(mode="v2-only")
    assert "No StreamCat reach in view." in _html(mode="hr-only")
    assert "No streams in view." in _html(mode="empty")
    assert "easi-legend-note" not in _html(mode=None)          # loading: no note


def test_reach_rows():
    html = _html(reach={"comid": 9327042, "name": "Mink Brook"})
    assert "StreamCat reach: Mink Brook (COMID 9327042)" in html
    assert "easi-legend-sw-glow" in html and app.SCORED_REACH_STYLE["color"] in html
    html = _html(reach={"comid": 9327042, "name": None}, routed=True)
    assert "Nearest StreamCat reach: unnamed stream (COMID 9327042)" in html
    assert "StreamCat reach:" not in _html()


def test_basin_step_adds_the_watershed_and_reach_rows():
    html = _html(step="basin")
    assert "Watershed" in html and "Assessment reach" in html
    assert app.WATERSHED_STYLE["fillColor"] in html and app.REACH_STYLE["color"] in html
    assert "easi-legend-sw-fill" in html
    assert "Assessment reach" not in _html(step="identify")


def test_hidden_outside_the_map_steps():
    assert app._legend_ui("measure", True, "segmented", None, False) is None
    assert app._legend_ui("report", True, "segmented", None, False) is None


def test_copy_is_plain():
    import re
    for html in (_html(), _html(zoomed=False, mode=None), _html(mode="v2-only"),
                 _html(step="basin", reach={"comid": 1, "name": "x"}, routed=True)):
        text = re.sub(r"<[^>]+>", " ", html)        # the visible strings, not the markup
        assert "—" not in text and ";" not in text


def test_dock_script_layers_and_cache_bust_are_wired():
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "legend-dock.js" in src and "styles.css?v=15" in src
    assert 'id="easi-legend-panel"' in src.replace("'", '"')
    assert app.LAYER_COVERED == "Streams: StreamCat lookup engine"
    assert app.LAYER_UNCOVERED == "Streams: STAF site engine"
    js = (Path(app.__file__).parent / "www" / "legend-dock.js").read_text(encoding="utf-8")
    assert "leaflet-control-layers" in js and "disableClickPropagation" in js
    assert "easi-legend-panel" in js
    css = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")
    assert ".easi-legend-panel.leaflet-control" in css and "#stream_legend.recalculating" in css
