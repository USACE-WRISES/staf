"""SFARI as one tool of the STAF app (apps/staf), and on its own.

Inside the STAF app SFARI is a Shiny module: every id carries its prefix and its body says which
(data-staf-ns), so the shared scripts post to SFARI's own inputs (www/staf/staf-ns.js). On its own
the page is the same body without the prefix.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import app

HERE = Path(__file__).resolve().parents[1]
IDS = re.compile(r'\bid="([^"]+)"')


def test_every_id_in_the_module_pieces_carries_the_prefix():
    for piece in (app.tool_nav_ui, app.tool_center_ui, app.tool_body_ui):
        ids = IDS.findall(str(piece("m", prefix="sfari/")))
        assert [i for i in ids if not i.startswith("m-")] == [], piece
    body = str(app.tool_body_ui("m", prefix="sfari/"))
    assert 'data-staf-tool="sfari"' in body and 'data-staf-ns="m"' in body
    assert 'id="m-map"' in body and 'id="m-leftpane"' in body and 'id="m-worksheet"' in body
    assert "easi-header" not in body                     # the STAF app draws the header
    assert app.tool_center_ui("m") is None


def test_on_its_own_the_page_is_the_module_body_without_the_prefix():
    standalone = str(app._tool_body())
    assert 'data-staf-tool="sfari"' in standalone and 'data-staf-ns=""' in standalone
    unprefixed = str(app.tool_body_ui("m")).replace('data-staf-ns="m"', 'data-staf-ns=""').replace('"m-', '"')
    assert unprefixed == standalone
    nav = str(app.tool_nav_ui("m")).replace('"m-', '"')
    assert nav == str(app._nav_actions())
    page = str(app.app_ui)
    assert 'class="easi-header"' in page and "easi-nav-sep" in page


def test_staf_ns_is_the_first_script():
    scripts = [tag.attrs.get("src") for tag in app.HEAD if tag.name == "script"]
    assert scripts[0] == "staf/staf-ns.js?v=1"
    assert all(src.startswith("staf/") for src in scripts[:-1]) and scripts[-1].startswith("field-review.js")


def test_sfari_scripts_never_spell_an_input_id():
    for js in (HERE / "www").glob("*.js"):
        assert not re.search(r"setInputValue\(\s*[\"']", js.read_text(encoding="utf-8")), js.name


def test_route_names_are_valid_module_ids():
    for name in re.findall(r'dynamic_route\("([^"]+)"', Path(app.__file__).read_text(encoding="utf-8")):
        assert re.fullmatch(r"\w+", name), name


def test_the_server_starts_as_a_module():
    """A separate process, so the module's effects never reach the other tests."""
    code = ("from shiny.express._stub_session import ExpressStubSession\n"
            "from shiny.session import session_context\n"
            "import app\n"
            "with session_context(ExpressStubSession()):\n"
            "    app.tool_server('m')\n"
            "print('started')\n")
    env = dict(os.environ, STAF_DATA_SOURCE="service", PYTHONPATH=str(HERE))
    run = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True,
                         timeout=300)
    assert run.returncode == 0 and "started" in run.stdout, run.stdout + run.stderr
