"""The app's provenance badges, tooltips, engine line, and user-visible copy.
Imports the Shiny module without running a server."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("shiny")
app = importlib.import_module("app")


def test_badges_follow_the_origin():
    assert app._ev_badge({"origin": "engine", "status": "ok"}) == ("exact watershed", "sfari-ev-tag engine")
    assert app._ev_badge({"origin": "streamcat", "status": "ok"}) == ("StreamCat", "sfari-ev-tag streamcat")
    assert app._ev_badge({"origin": "pull", "status": "ok"}) == ("desktop", "sfari-ev-tag")
    assert app._ev_badge({"status": "ok"}) == ("desktop", "sfari-ev-tag")     # legacy entry
    assert app._ev_badge({"origin": "engine", "status": "pending"}) == app._PENDING_BADGE
    assert app._ev_badge(None) == ("field", "sfari-ev-tag field")


def test_tooltip_names_the_reach_and_the_fallback():
    tip = app._ev_tip({"source": "EPA StreamCat rddens", "confidence": "M",
                       "anchor_label": "nearest covered reach, COMID 5214461",
                       "fallback_reason": "STAF site engine failed: no stream.",
                       "note": "national default"})
    assert tip.startswith("Source: EPA StreamCat rddens")
    assert "Describes the nearest covered reach, COMID 5214461." in tip
    assert "Fallback: STAF site engine failed" in tip
    assert tip.endswith("national default")
    pending = app._ev_tip({"source": "x", "upgrade_pending": True})
    assert "still running" in pending
    assert app._ev_describes({"anchor_label": ""}) is None
    assert "describes the nearest covered reach" in str(app._ev_describes(
        {"anchor_label": "nearest covered reach, COMID 1"}))


def test_engine_progress_text():
    assert app._engine_progress_text({}) == "Calculating the exact watershed: starting…"
    txt = app._engine_progress_text({"stage": "walk", "reaches": 43, "hops": 7})
    assert txt == "Calculating the exact watershed: walking upstream, 43 reaches, 7 hops…"
    assert "(landcover)" in app._engine_progress_text({"stage": "metrics", "family": "landcover"})


def test_engine_line_states():
    assert app._engine_line_ui({"status": "idle"}, False, {}) is None
    ok = str(app._engine_line_ui({"status": "ok", "record": {
        "engineVersion": "0.2.0", "watershed": {"areaSqkm": 4.19, "nReaches": 3}}}, False, {}))
    assert "STAF site engine v0.2.0" in ok and "4.19 km2 over 3 reaches" in ok
    warn = str(app._engine_line_ui({"status": "refused", "reason": "over budget"}, False, {}))
    assert "refused: over budget" in warn and "warn" in warn
    running = str(app._engine_line_ui({"status": "running"}, True, {"stage": "site"}))
    assert "locating the stream" in running


def test_copy_has_no_em_dash_and_names_both_engines():
    for text in (app._MISS_TEXT, app._engine_progress_text({}),
                 app._ev_tip({"source": "x", "fallback_reason": "y"})):
        assert "—" not in text
    assert "NHDPlus V2" in app._MISS_TEXT and "NHD streams" in app._MISS_TEXT
    # head content renders as a dependency, not in str(app_ui): read the source
    from pathlib import Path
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert 'styles.css?v=17' in src and 'styles.css?v=16' not in src


def test_map_styles_exist():
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert "dashArray" in app.ROUTE_STYLE
    # 2026-09-02: both networks legible and hover-highlighted; the route line
    # stays the only dashed one
    assert app.FLOWLINE_STYLE["weight"] >= 3 and app.HR_FLOWLINE_STYLE["weight"] >= 2
    assert not hasattr(app, "FLOWLINE_HOVER_STYLE")    # a hover restyle flashed the layer
    assert "dashArray" not in app.FLOWLINE_STYLE and "dashArray" not in app.HR_FLOWLINE_STYLE
    assert "cyan" in app._MISS_TEXT and "dark blue" in app._MISS_TEXT


def test_field_forms_output_stays_alive_while_the_modal_is_hidden():
    # The modal's output binds while Bootstrap's fade still hides it; a
    # suspended output never resumes, so the keep-alive decorator must stay.
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert ("@output(suspend_when_hidden=False)\n    @render.ui\n    def field_forms_body"
            in src)
