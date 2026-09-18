"""The Get Forms dialog (2026-09-18): SFARI's Field Forms shell on the EASI Assessment page.

The dialog is a module-level static modal built from an ``export_result()``
snapshot, so it renders offline; the wiring that lives in the server function
(the open handler, the three downloads) is pinned as source text, like
``apps/sfari/tests/test_field_forms_dialog.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import calculator_cases as cc
from easi import config

app = pytest.importorskip("app")

WWW = Path(app.__file__).parent / "www"
SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (WWW / "styles.css").read_text(encoding="utf-8")
JS = (WWW / "worksheet.js").read_text(encoding="utf-8")
DELINEATION = {"comid": 9327042, "gnis_name": "Mink Brook", "snapped_lat": 43.6858, "snapped_lon": -72.2367}


def _result(ratings=None, note=""):
    rep = cc.score_case({"record": {}, "observed": None, "ratings": ratings})
    rep["metricRows"] = [{**row, "userNote": note if row["metricId"] == cc.FUNCTIONS["m03"] else ""}
                         for row in rep["metricRows"]]
    return {"delineation": dict(DELINEATION), "report": rep}


def test_the_shell_is_sfaris_with_the_three_downloads_in_the_tab_strip():
    html = str(app._forms_modal(_result()))
    assert "modal-xl" in html and "ff-modal-body" in html and ">Get Forms<" in html
    assert re.findall(r'<a [^>]*class="nav-link[^"]*"[^>]*>\s*([^<]+?)\s*</a>', html) == ["Desktop metrics"]
    assert "bslib-nav-spacer" in html
    buttons = re.findall(r'<a [^>]*id="(dl_forms_[a-z]+)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+?)\s*</a>', html, re.S)
    assert buttons == [("dl_forms_pdf", "Desktop metrics PDF"), ("dl_forms_filled", "Completed workbook"),
                       ("dl_forms_blank", "Blank calculator")]
    # equal downloads: each the app's primary button inside its own wrapper (a bare anchor in
    # a nav strip renders as a link-blue nav link)
    assert html.count('class="ff-dl"') == 3 and html.count("btn-sm btn-primary") == 3
    assert "Mink Brook · COMID 9327042 · 43.68580, -72.23670" in html
    assert 'class="ff-table-wrap"' in html and 'id="easi-desktop-metrics"' in html
    # static: nothing under the backdrop can change, so the dialog carries no live output
    assert "shiny-html-output" not in html and 'output_ui("gf_' not in SRC


def test_the_list_is_the_twenty_metrics_in_the_order_of_the_rail_and_the_workbook():
    html = str(app._forms_modal(_result()))
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    rows = body.split("<tr>")[1:]
    assert len(rows) == 20
    names = [meta["functionName"] for meta in config.metrics_by_id().values()]
    for n, (row, name) in enumerate(zip(rows, names), 1):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        assert cells[0].strip() == str(n) and cells[2].strip() == name.replace("&", "&amp;"), (n, name)
    # the entries are the traced values the completed workbook carries, context inputs left out
    assert "Watershed impervious cover: </span>\n" in body or "Watershed impervious cover: " in body
    assert "<b>8%</b>" in body and "<b>1.2 km/km²</b>" in body
    assert "NHD feature code" not in body
    assert body.count("easi-rate-chip") == 20


def test_override_scores_and_notes_show_in_the_list_and_the_summary():
    plain = str(app._forms_modal(_result()))
    assert "20 of 20 desktop metrics rated." in plain and "carried into" not in plain
    assert "In Protected View, choose Enable Editing first." in plain
    html = str(app._forms_modal(_result({cc.FUNCTIONS["m03"]: "Poor"}, note="outfall <b>seen</b>")))
    assert "1 override score and 1 note are carried into the completed workbook." in html
    assert "override, computed Fair" in html and "rate-Poor" in html
    assert "outfall &lt;b&gt;seen&lt;/b&gt;" in html and "outfall <b>seen" not in html
    two = str(app._forms_modal(_result({cc.FUNCTIONS["m03"]: "Poor", cc.FUNCTIONS["m05"]: "Good"})))
    assert "2 override scores are carried into the completed workbook." in two
    for text in (plain, html, two):
        assert "— " not in text.split("<tbody>")[0]          # no em dashes in the dialog's copy


def test_the_rail_button_opens_the_dialog():
    worksheet = SRC.split("def worksheet():", 1)[1].split("@render.ui", 1)[0]
    assert 'ui.tags.button("Get Forms",' in worksheet and '"data-forms": "1"' in worksheet
    assert 'class_="sfari-btn sfari-nav-desktop"' in worksheet
    assert worksheet.index("_stepper(step)") < worksheet.index('"Get Forms"') < worksheet.index('"fn_nav"')
    assert 'var forms = t.closest("[data-forms]");' in JS and 'send("forms_evt", {});' in JS
    assert 'src="worksheet.js?v=9"' in SRC
    handler = SRC.split("def _open_forms():", 1)[1].split("@reactive", 1)[0]
    assert "@reactive.event(input.forms_evt)\n    def _open_forms():" in SRC
    assert 'if app_mode() != "single":' in handler and "res = export_result()" in handler
    assert "ui.notification_show(" in handler
    # a report still being prepared would open over the dialog and replace it
    assert handler.index("_cancel_report()") < handler.index("ui.modal_show(_forms_modal(res))")


def test_the_three_downloads_build_the_three_files():
    assert re.search(r"@render\.download\(filename=lambda: report\.desktop_metrics_filename\(export_result\(\)\)\)\s+"
                     r"def dl_forms_pdf\(\):\s+res = export_result\(\)\s+if res:\s+"
                     r"yield report\.build_desktop_metrics_pdf\(res\)", SRC)
    assert re.search(r"@render\.download\(filename=lambda: calculator\.filled_filename\(export_result\(\)\)\)\s+"
                     r"def dl_forms_filled\(\):", SRC)
    assert re.search(r"@render\.download\(filename=calculator\.blank_filename\(\)\)\s+def dl_forms_blank\(\):", SRC)


def test_the_stylesheet_pins_the_header_and_the_layout():
    for rule in (
        ".modal-dialog.modal-xl:has(.ff-modal-body) .modal-content { height: 100%; }",
        ".modal-xl .modal-body.ff-modal-body { display: flex; flex-direction: column;",
        ".ff-modal-body .nav-pills .bslib-nav-spacer { flex: 1; }",
        ".ff-modal-body .nav-pills .ff-dl .btn-primary { color: #fff; background: var(--easi-accent);",
        ".ff-modal-body .nav-pills .ff-dl .btn-primary:hover,",
        ".ff-table-wrap { flex: 1; min-height: 0; overflow: auto;",
        ".ff-table-wrap .ff-table { border-collapse: separate; border-spacing: 0; }",
        ".ff-table-wrap .ff-table thead th { position: sticky; top: 0;",
        ".ff-src-sub { display: block;",
        # two classes: .sfari-btn comes later in the sheet and would win on color and border
        ".sfari-btn.sfari-nav-desktop { display: block; width: calc(100% - 28px);",
    ):
        assert rule in CSS, rule
    assert ".ff-preview" not in CSS                     # EASI has no preview tab
    assert 'href="styles.css?v=60"' in SRC              # bumped with the CSS
