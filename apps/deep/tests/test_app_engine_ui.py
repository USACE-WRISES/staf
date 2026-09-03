"""The app's basis badge, engine line, engine scheduling rule, and copy.
Imports the Shiny module without running a server."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("shiny")
app = importlib.import_module("app")


def test_basis_tag_follows_the_basis():
    assert app._basis_tag({"origin": "desktop", "basis": "site-engine"}) == (
        "exact watershed", "deep-basis-tag engine")
    assert app._basis_tag({"origin": "desktop", "basis": "streamcat"})[0] == "StreamCat"
    assert app._basis_tag({"origin": "desktop", "basis": "nlcd"})[0] == "NLCD"
    assert app._basis_tag({"origin": "desktop", "basis": "3dep"})[0] == "3DEP"
    # a legacy engine entry without a basis still reads as the engine
    assert app._basis_tag({"origin": "desktop", "engine": True})[0] == "exact watershed"
    assert app._basis_tag({"origin": "field", "basis": "streamcat"}) is None
    assert app._basis_tag({"origin": "desktop"}) is None
    assert app._basis_tag(None) is None


def test_engine_line_states():
    assert app._engine_line_ui({"status": "idle"}, False, {}) is None
    ok = str(app._engine_line_ui({"status": "ok", "record": {
        "engineVersion": "0.2.0", "watershed": {"areaSqkm": 4.19, "nReaches": 3}}}, False, {}))
    assert "STAF site engine v0.2.0" in ok and "4.19 km2 over 3 reaches" in ok
    warn = str(app._engine_line_ui({"status": "refused", "reason": "over budget"}, False, {}))
    assert "refused: over budget" in warn and "warn" in warn
    assert "walking upstream, 43 reaches, 7 hops" in app._engine_progress_text(
        {"stage": "walk", "reaches": 43, "hops": 7})


def test_engine_scheduling_rule(monkeypatch):
    import types

    from deep import curves
    streamcat_bundle = types.SimpleNamespace(raw={})
    engine_bundle = types.SimpleNamespace(raw={"predictorSource": "site-engine v0.2.0"})
    assert app._engine_wanted_for(None) is False
    assert app._engine_wanted_for(streamcat_bundle) is False     # never pays the minutes
    assert app._engine_wanted_for(engine_bundle) is True
    mixed_bundle = types.SimpleNamespace(
        raw={"predictorSource": "mixed (site-engine v0.2.2 + streamcat)"})
    assert app._engine_wanted_for(mixed_bundle) is True      # any engine curve opens the gate
    monkeypatch.setattr(curves, "ENGINE_PAIRING_MODE", "label")
    assert app._engine_wanted_for(streamcat_bundle) is True


def test_map_styles_legible_and_hover_highlighted():
    assert app.HR_FLOWLINE_STYLE["color"] != app.FLOWLINE_STYLE["color"]
    assert app.FLOWLINE_STYLE["weight"] >= 3 and app.HR_FLOWLINE_STYLE["weight"] >= 2
    assert app.FLOWLINE_HOVER_STYLE["weight"] > app.FLOWLINE_STYLE["weight"]
    assert app.HR_FLOWLINE_HOVER_STYLE["weight"] > app.HR_FLOWLINE_STYLE["weight"]
    assert "dashArray" in app.ROUTE_STYLE and "dashArray" not in app.HR_FLOWLINE_STYLE
    assert "cyan" in app._MISS_TEXT


def test_copy_and_cache_bust():
    assert "—" not in app._MISS_TEXT and "NHDPlus V2" in app._MISS_TEXT
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "deep.css?v=8" in src and "styles.css?v=12" in src
    css = (Path(app.__file__).parent / "www" / "deep.css").read_text(encoding="utf-8")
    assert ".deep-basis-tag.engine" in css and ".deep-engine-line" in css
