"""The Nationwide screening viewer mounts only behind EASI_NATIONAL_VIEWER.

The public app ships without the switch, the map assets, the help paragraph and
the tile route. The flag is read once at import, so the page-level checks pin
whichever state the test process started in and the helper checks cover both.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

app = pytest.importorskip("app")

SRC = Path(app.__file__).read_text(encoding="utf-8")
VIEWER_MARKERS = ('"viewer_on"', "Nationwide screening", "maplibre-gl.js", "viewer.js?v=",
                  "viewer-compatibility.js", "nationwide-viewer.css", "viewer_workspace")


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    ("", False), ("0", False), ("off", False), ("no", False), ("false", False),
])
def test_env_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("EASI_NATIONAL_VIEWER_PROBE", value)
    assert app._env_flag("EASI_NATIONAL_VIEWER_PROBE") is expected


def test_env_flag_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("EASI_NATIONAL_VIEWER_PROBE", raising=False)
    assert app._env_flag("EASI_NATIONAL_VIEWER_PROBE") is False


def test_helpers_carry_the_viewer_only_when_on():
    assert app._viewer_head_tags(False) == []
    on = "".join(str(tag) for tag in app._viewer_head_tags(True))
    assert 'href="vendor/maplibre-gl.css"' in on and 'src="vendor/maplibre-gl.js"' in on
    assert re.search(r'src="viewer\.js\?v=\d+"', on) and "nationwide-viewer.css" in on

    off_switch = str(app._viewer_switch(False))
    assert 'class="easi-mode-toggle"' in off_switch        # keeps the header's middle column
    assert "viewer_on" not in off_switch and "Nationwide" not in off_switch
    on_switch = str(app._viewer_switch(True))
    assert 'id="viewer_on"' in on_switch and "Nationwide screening" in on_switch
    assert 'id="viewer_info"' in on_switch

    assert app._viewer_workspace_slot(False) is None
    assert 'id="viewer_workspace"' in str(app._viewer_workspace_slot(True))

    assert app._viewer_help(False) == ""
    assert "Nationwide screening" in app._viewer_help(True)
    assert "—" not in app._viewer_help(True)          # no em dashes in user-visible copy


def test_public_page_carries_no_viewer_markup_unless_the_flag_is_set():
    html = str(app.app_ui)   # the page body; head_content tags render separately
    if app.NATIONAL_VIEWER:
        assert 'id="viewer_on"' in html and "Nationwide screening" in html
        assert 'id="viewer_workspace"' in html
    else:
        assert not any(marker in html for marker in VIEWER_MARKERS)
        # the single-site page is otherwise intact
        assert 'id="nav_batch"' in html and 'id="nav_help"' in html
        assert 'class="easi-mode-toggle"' in html


def test_every_mount_point_reads_the_flag():
    assert "*_viewer_head_tags(NATIONAL_VIEWER)" in SRC
    assert "_viewer_switch(NATIONAL_VIEWER)," in SRC
    assert "_viewer_workspace_slot(NATIONAL_VIEWER)," in SRC
    assert "+ _viewer_help(NATIONAL_VIEWER) +" in SRC
    route = ('tiles_route = (session.dynamic_route("national-tiles", _national_tiles_handler)\n'
             '                   if NATIONAL_VIEWER else None)')
    assert route in SRC
    assert 'NATIONAL_VIEWER = _env_flag("EASI_NATIONAL_VIEWER")' in SRC
