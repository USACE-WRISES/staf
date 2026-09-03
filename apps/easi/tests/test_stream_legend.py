"""The map legend (2026-09-03): docked under the layers button, always
visible on the Identify and Basin steps, naming the two engines, the state
of the stream fetch, and the highlighted reach. Pure builder, no session."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")


def _html(step="identify", zoomed=True, mode="segmented", scored=None, routed=False):
    return str(app._legend_ui(step, zoomed, mode, scored, routed) or "")


def test_names_both_engines_with_their_colors():
    html = _html()
    assert "StreamCat lookup engine" in html and "scores the reach in seconds" in html
    assert "STAF site engine" in html and "calculates the exact watershed" in html
    assert app.FLOWLINE_STYLE["color"] in html and app.HR_FLOWLINE_STYLE["color"] in html
    assert "easi-legend-title" in html and "Zoom in" not in html


def test_notes_follow_the_zoom_and_the_fetch_mode():
    assert "Zoom in to see streams" in _html(zoomed=False, mode=None)
    assert "Fine streams unavailable here. Zoom in." in _html(mode="v2-only")
    assert "No StreamCat reach in view." in _html(mode="hr-only")
    assert "No streams in view." in _html(mode="empty")
    assert "easi-legend-note" not in _html(mode=None)          # loading: no note


def test_scored_and_routed_rows():
    html = _html(scored={"comid": 5214461, "name": "Sugar Run"})
    assert "Scored reach: Sugar Run (COMID 5214461)" in html
    assert "easi-legend-sw-glow" in html and app.SCORED_REACH_STYLE["color"] in html
    html = _html(scored={"comid": 5214461, "name": None}, routed=True)
    assert "Reach evidence: unnamed stream (COMID 5214461)" in html
    assert "Scored reach" not in _html()


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
    assert "legend-dock.js" in src and "styles.css?v=42" in src
    assert 'id="easi-legend-panel"' in src.replace("'", '"')
    js = (Path(app.__file__).parent / "www" / "legend-dock.js").read_text(encoding="utf-8")
    assert "leaflet-control-layers" in js and "disableClickPropagation" in js
    assert "easi-legend-panel" in js
