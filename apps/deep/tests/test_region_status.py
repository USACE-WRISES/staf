"""DEEP runs Draft, Preliminary and Final assessments, each labeled in one palette (owner,
2026-10-08): Draft gray, Preliminary amber, Final blue, on the map's regions, the region list's
chips, the legend's key and the badges. The colors live twice, in app.py and www/coverage.js;
these tests keep the two equal and keep every status distinct from the navy of a hovered or
selected region and from the streams' blues."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

app = pytest.importorskip("app")
WWW = Path(app.__file__).resolve().parent / "www"
NAVY = "#2f4b7c"


def _js_status() -> dict:
    src = (WWW / "coverage.js").read_text(encoding="utf-8")
    block = src.split("var STATUS = {", 1)[1].split("};", 1)[0]
    rows = re.findall(r'(\w+): \{ label: "([^"]+)", line: "(#[0-9a-f]{6})", fill: "(#[0-9a-f]{6})"', block)
    return dict((key, (label, line, fill)) for key, label, line, fill in rows)


def test_the_map_script_and_the_app_hold_the_same_colors():
    py = dict((key, (s["label"], s["color"], s["fillColor"]))
              for key, s in app.REGION_STATUS_STYLE.items())
    assert _js_status() == py
    assert list(py) == ["draft", "preliminary", "certified"]
    assert [label for label, _line, _fill in py.values()] == ["Draft", "Preliminary", "Final"]


def test_every_status_has_its_own_color_and_none_is_the_selection_or_a_stream():
    colors = [s["color"] for s in app.REGION_STATUS_STYLE.values()]
    fills = [s["fillColor"] for s in app.REGION_STATUS_STYLE.values()]
    assert len(set(colors)) == 3 and len(set(fills)) == 3
    taken = {NAVY, app.FLOWLINE_STYLE["color"], app.HR_FLOWLINE_STYLE["color"]}
    assert not taken & (set(colors) | set(fills))


def test_the_badges_follow_the_status():
    assert app._badge_class("draft") == "deep-badge-draft"
    assert app._badge_class("preliminary") == "deep-badge-prelim"
    assert app._badge_class("certified") == "deep-badge-cert"
    assert app._badge_class("retired") == "deep-badge-prelim", "unknown reads as preliminary"
    css = (WWW / "styles.css").read_text(encoding="utf-8")
    for cls in ("deep-badge-draft", "deep-badge-prelim", "deep-badge-cert"):
        assert f".{cls} {{" in css
    chips = (WWW / "deep.css").read_text(encoding="utf-8")
    for cls in ("deep-cov-draft", "deep-cov-prelim", "deep-cov-final"):
        assert f".{cls} {{" in chips


def test_the_legend_keys_only_the_statuses_on_the_map():
    html = str(app._legend_ui(app.STEP_IDENTIFY, False, "hr", None, False,
                              regions=("draft", "preliminary")))
    key = html.split('class="deep-reg-key"', 1)[1]
    assert "Assessment regions" in key
    assert re.findall(r'class="easi-legend-label">([^<]+)<', key) == ["Draft", "Preliminary"]
    assert "--swatch-line:#b45309;" in key and "rgba(217,119,6,.28)" in key
    none = str(app._legend_ui(app.STEP_IDENTIFY, False, "hr", None, False))
    assert "deep-reg-key" not in none, "no regions yet: no key"
    basin = str(app._legend_ui(app.STEP_BASIN, True, "hr", None, False, regions=("draft",)))
    assert "deep-reg-key" not in basin, "the key is Identify's"
    css = (WWW / "deep.css").read_text(encoding="utf-8")
    assert ".easi-shell.deep-regions-off .deep-reg-key { display: none; }" in css


def test_the_help_names_the_three_statuses_and_their_order():
    src = Path(app.__file__).read_text(encoding="utf-8")
    copy = re.sub(r'"\s*\n\s*"', "", src.split("def _about():", 1)[1].split("# ---- left pane ----", 1)[0])
    assert "colored by status (gray Draft, amber Preliminary, blue Final)" in copy
    assert "(Final before Preliminary before Draft)" in copy
    assert "a Draft (gray on the map) has not been reviewed yet" in copy
    assert "\u2014" not in copy
