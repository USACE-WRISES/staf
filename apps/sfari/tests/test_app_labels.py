"""The app's provenance badges, tooltips, engine line, and user-visible copy.
Imports the Shiny module without running a server."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("shiny")
app = importlib.import_module("app")


def test_badges_follow_the_origin():
    assert app._ev_badge({"origin": "engine", "status": "ok"}) == ("HR reach watershed", "sfari-ev-tag engine")
    assert app._ev_badge({"origin": "streamcat", "status": "ok"}) == ("StreamCat", "sfari-ev-tag streamcat")
    assert app._ev_badge({"origin": "pull", "status": "ok"}) == ("desktop", "sfari-ev-tag")
    assert app._ev_badge({"status": "ok"}) == ("desktop", "sfari-ev-tag")     # legacy entry
    assert app._ev_badge({"origin": "engine", "status": "pending"}) == app._PENDING_BADGE
    assert app._ev_badge(None) == ("field", "sfari-ev-tag field")


def test_tooltip_names_the_reach_and_the_fallback():
    tip = app._ev_tip({"source": "EPA StreamCat rddens", "confidence": "M",
                       "anchor_label": "nearest StreamCat reach, COMID 5214461",
                       "fallback_reason": "STAF site engine failed: no stream.",
                       "note": "national default"})
    assert tip.startswith("Source: EPA StreamCat rddens")
    assert "Describes the nearest StreamCat reach, COMID 5214461." in tip
    assert "Fallback: STAF site engine failed" in tip
    assert tip.endswith("national default")
    # upgrade_pending is a legacy field nothing sets any more (2026-09-07)
    assert "still running" not in app._ev_tip({"source": "x", "upgrade_pending": True})
    assert app._ev_describes({"anchor_label": ""}) is None
    assert "describes the nearest StreamCat reach" in str(app._ev_describes(
        {"anchor_label": "nearest StreamCat reach, COMID 1"}))


def test_engine_progress_text():
    # five plain steps, the reach count while the trace and the union run, the
    # metric family with its position; never "hops" (2026-09-07)
    assert app._engine_progress_text({}) == "Delineating watershed · starting"
    assert app._engine_progress_text({"stage": "site"}) \
        == "Delineating watershed · step 1 of 5 · finding the stream"
    txt = app._engine_progress_text({"stage": "walk", "reaches": 43, "hops": 7})
    assert txt == "Delineating watershed · step 2 of 5 · tracing upstream, 43 reaches"
    assert app._engine_progress_text({"stage": "walk", "reaches": 1, "hops": 0}) \
        .endswith("tracing upstream, 1 reach")
    assert app._engine_progress_text({"stage": "union", "reaches": 1180, "hops": 40}) \
        == "Delineating watershed · step 3 of 5 · joining catchments, 1,180 reaches"
    assert app._engine_progress_text({"stage": "reach", "reaches": 1180}) \
        == "Delineating watershed · step 4 of 5 · marking the assessment reach"
    assert app._engine_progress_text({"stage": "metrics", "family": "landcover"}) \
        == "Delineating watershed · step 5 of 5 · computing metrics, land cover (3 of 7)"
    assert app._engine_progress_text({"stage": "metrics", "family": "xsection"}) \
        .endswith("computing metrics, cross-sections (7 of 7)")
    assert app._engine_progress_text({"stage": "done"}) == "Delineating watershed · finishing"
    for prog in ({}, {"stage": "walk", "reaches": 43, "hops": 7},
                 {"stage": "metrics", "family": "soils"}, {"stage": "done"}):
        text = app._engine_progress_text(prog)
        assert "hops" not in text and "…" not in text and "—" not in text


def test_engine_line_states():
    assert app._engine_line_ui({"status": "idle"}, False, {}) is None
    assert app._engine_line_ui({"status": "ok", "record": {
        "engineVersion": "0.2.0", "watershed": {"areaSqkm": 4.19, "nReaches": 3}}}, False, {}) is None
    warn = str(app._engine_line_ui({"status": "refused", "reason": "over budget"}, False, {}))
    assert "refused: over budget" in warn and "warn" in warn
    running = str(app._engine_line_ui({"status": "running"}, True, {"stage": "site"}))
    assert "step 1 of 5 · finding the stream" in running


def test_copy_has_no_em_dash_and_names_both_engines():
    for text in (app._MISS_TEXT, app._engine_progress_text({}),
                 app._ev_tip({"source": "x", "fallback_reason": "y"})):
        assert "—" not in text
    assert app._MISS_TEXT == ("No stream line within 150 ft of the click. "
                              "Zoom in and click a line.")
    # head content renders as a dependency, not in str(app_ui): read the source
    from pathlib import Path
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert 'styles.css?v=21' in src and 'styles.css?v=20' not in src


def test_map_styles_exist():
    # two colors (2026-09-07, EASI's rule): dark blue where the StreamCat lookup
    # engine answers by COMID, cyan for every other NHD stream, a dashed route
    # to the nearest StreamCat reach, a glow under the StreamCat reach
    assert app.FLOWLINE_STYLE["weight"] >= 3 and "dashArray" not in app.FLOWLINE_STYLE
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert "dashArray" in app.ROUTE_STYLE and app.SCORED_REACH_STYLE["opacity"] < 0.5
    assert not hasattr(app, "FLOWLINE_HOVER_STYLE")
    assert "—" not in app._MISS_TEXT

def test_field_forms_outputs_stay_alive_while_the_modal_is_hidden():
    # The dialog's outputs bind while Bootstrap's fade still hides the modal; a
    # suspended output never resumes, so the keep-alive decorator must stay on
    # every one of them (2026-09-07: four outputs in a static navset shell).
    src = Path(app.__file__).read_text(encoding="utf-8")
    for name in ("ff_site", "ff_status", "ff_table", "ff_preview"):
        assert f"@output(suspend_when_hidden=False)\n    @render.ui\n    def {name}(" in src, name
    assert "field_forms_body" not in src
