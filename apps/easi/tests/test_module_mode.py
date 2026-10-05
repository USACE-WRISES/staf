"""EASI as one tool of the STAF app (apps/staf), and on its own.

Inside the STAF app EASI is a Shiny module: every id carries its prefix and its body says which
(data-staf-ns), so the shared scripts post to EASI's own inputs (www/staf/staf-ns.js). On its own
the page is the same body without the prefix. The Nationwide screening switch is EASI's piece of
the STAF header, shown beside the tool switch while EASI is shown.
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


def _run(code: str, **env) -> subprocess.CompletedProcess:
    """A separate process: the viewer flag is read at import, and a module's effects must never
    reach the other tests."""
    environ = dict(os.environ, EASI_ADOPTED_METHOD="0", STAF_DATA_SOURCE="service", PYTHONPATH=str(HERE), **env)
    return subprocess.run([sys.executable, "-c", code], cwd=HERE, env=environ, capture_output=True, text=True,
                          timeout=300)


def test_every_id_in_the_module_pieces_carries_the_prefix():
    for piece in (app.tool_nav_ui, app.tool_center_ui, app.tool_body_ui):
        ids = IDS.findall(str(piece("m", prefix="easi/")))
        assert [i for i in ids if not i.startswith("m-")] == [], piece
    body = str(app.tool_body_ui("m", prefix="easi/"))
    assert 'data-staf-tool="easi"' in body and 'data-staf-ns="m"' in body
    assert 'id="m-map"' in body and 'id="m-leftpane"' in body and 'id="m-batch_workspace"' in body
    assert "easi-header" not in body                     # the STAF app draws the header


def test_on_its_own_the_page_is_the_module_body_without_the_prefix():
    standalone = str(app._tool_body())
    assert 'data-staf-tool="easi"' in standalone and 'data-staf-ns=""' in standalone
    unprefixed = str(app.tool_body_ui("m")).replace('data-staf-ns="m"', 'data-staf-ns=""').replace('"m-', '"')
    assert unprefixed == standalone
    page = str(app.app_ui)
    assert 'class="easi-header"' in page and 'id="save_session"' in page and "documentation.html" not in page


def test_the_header_has_the_five_actions_of_every_tool_and_local_review_stays_out():
    """Owner, 2026-10-05: New, Open, Save, About and Help, as in SFARI and DEEP (Batch and
    Documentation left the bar; About and Help link the EASI guide)."""
    nav = str(app.tool_nav_ui("m", prefix="easi/"))
    actions = ["m-nav_new", "m-load_session", "m-save_session", "m-nav_about", "m-nav_help"]
    assert [i for i in IDS.findall(nav) if i in actions] == actions
    assert "nav_batch" not in nav and "documentation.html" not in nav
    assert "local-review/" not in nav
    from easi._vendor.staf_workbook import web
    assert nav.replace('"m-', '"') == str(web.nav_actions())           # the shared markup, prefixed


def test_the_nationwide_switch_is_easis_piece_of_the_header():
    code = ("import app\n"
            "assert app.NATIONAL_VIEWER\n"
            "html = str(app.tool_center_ui('m', prefix='easi/'))\n"
            "assert 'id=\"m-viewer_on\"' in html and 'id=\"m-viewer_info\"' in html, html\n"
            "assert 'Nationwide screening' in html and 'easi-mode-toggle' in html\n"
            "body = str(app.tool_body_ui('m', prefix='easi/'))\n"
            "assert 'id=\"m-viewer_workspace\"' in body\n"
            "print('ok')\n")
    run = _run(code, EASI_NATIONAL_VIEWER="1")
    assert run.returncode == 0 and "ok" in run.stdout, run.stdout + run.stderr


def test_scripts_using_staf_ns_load_after_it():
    scripts = [tag.attrs.get("src") for tag in app.HEAD if tag.name == "script"]
    first = scripts.index("staf/staf-ns.js?v=1")
    viewer = {"vendor/", "viewer"}
    assert all(any(src.startswith(v) for v in viewer) for src in scripts[:first])   # the viewer's own, if on
    for name in ("staf/legend-dock.js", "report-edit.js", "worksheet.js", "staf/coord-entry.js"):
        assert first < next(i for i, src in enumerate(scripts) if src.startswith(name)), name


def test_easi_scripts_never_spell_an_input_id():
    for js in (HERE / "www").glob("*.js"):
        assert not re.search(r"setInputValue\(\s*[\"']", js.read_text(encoding="utf-8")), js.name
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert "Shiny.setInputValue('batch_open_report'" not in src   # the batch link resolves its id


def test_route_names_are_valid_module_ids():
    for name in re.findall(r'dynamic_route\("([^"]+)"', Path(app.__file__).read_text(encoding="utf-8")):
        assert re.fullmatch(r"\w+", name), name


def test_the_server_starts_as_a_module_with_the_viewer_on():
    code = ("from shiny.express._stub_session import ExpressStubSession\n"
            "from shiny.session import session_context\n"
            "import app\n"
            "with session_context(ExpressStubSession()):\n"
            "    app.tool_server('m')\n"
            "print('started')\n")
    run = _run(code, EASI_NATIONAL_VIEWER="1")
    assert run.returncode == 0 and "started" in run.stdout, run.stdout + run.stderr
