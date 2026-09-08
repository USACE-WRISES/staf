"""The Field Forms dialog (2026-09-07): a static navset shell with both
downloads at the top, three keep-alive outputs inside (status, table,
preview), the packet previewed inline through a session route, the table
scrolling in its own box under a header that stays put. The dialog is Shiny
UI built inside the server function, so this reads the source and the
stylesheet, plus the pure packing helpers in report.py."""
from __future__ import annotations

from pathlib import Path

import pytest

app = pytest.importorskip("app")
from sfari import report  # noqa: E402

SRC = Path(app.__file__).read_text(encoding="utf-8")
CSS = (Path(app.__file__).parent / "www" / "styles.css").read_text(encoding="utf-8")
SHELL = SRC.split("def _desktop_metrics_modal():", 1)[1].split("# Field-form readiness", 1)[0]


def test_the_shell_is_static_with_both_downloads_in_the_tab_strip():
    assert "ui.navset_pill(" in SHELL and "ui.nav_spacer()" in SHELL
    assert 'ui.nav_panel("Desktop metrics"' in SHELL
    assert 'ui.nav_panel("Field forms preview"' in SHELL
    assert 'ui.download_button("dl_field_forms", "Field forms PDF"' in SHELL
    assert 'ui.download_button("dl_desktop_metrics"' in SHELL
    # wrapped, so Bootstrap's ``.nav-pills > li > a`` nav-link rule cannot strip
    # the button chrome (it did: link-blue text, dark hover behind it)
    assert SHELL.count('class_="ff-dl")') == 2
    assert SHELL.count('class_="btn-sm btn-primary"') == 2      # equals, styled alike
    assert 'class_="ff-modal-body"' in SHELL and 'size="xl"' in SHELL
    assert 'ui.output_ui("ff_site")' in SHELL and 'ui.output_ui("ff_status")' in SHELL
    assert 'ui.output_ui("ff_table")' in SHELL and 'ui.output_ui("ff_preview")' in SHELL
    assert 'class_="ff-table-wrap"' in SHELL
    # the old single output and its bottom-of-table download are gone
    assert "field_forms_body" not in SRC and "Download Field Forms PDF" not in SRC
    assert "ff-actions" not in SRC


def test_the_two_downloads_build_the_two_pdfs():
    assert "def dl_field_forms():" in SRC and "report.build_field_forms_pdf(" in SRC
    assert "def dl_desktop_metrics():" in SRC and "report.build_desktop_metrics_pdf(" in SRC
    assert "report.desktop_metrics_filename(" in SRC and "report.field_forms_filename(" in SRC


def test_the_preview_is_served_inline_by_a_session_route():
    assert 'session.dynamic_route("field-forms-preview", _ff_preview_route)' in SRC
    assert 'media_type="application/pdf"' in SRC and '"Content-Disposition": "inline' in SRC
    body = SRC.split("def ff_preview():", 1)[1].split("@reactive.effect", 1)[0]
    assert "ui.tags.iframe(" in body and "Open in a new tab" not in SRC
    assert "report.build_field_forms_pdf()" in SRC   # the blank worksheet, no site inputs
    assert "base64" not in SRC


def test_the_status_line_owns_the_polling_and_the_retry():
    body = SRC.split("def ff_status():", 1)[1].split("@output(suspend_when_hidden=False)", 1)[0]
    assert "reactive.invalidate_later(1.0)" in body
    assert 'ui.input_action_button("ff_retry", "Retry pull"' in body
    table = SRC.split("def ff_table():", 1)[1].split("# The preview", 1)[0]
    assert "invalidate_later" not in table and 'id="sfari-desktop-metrics"' in table
    assert "bieger" not in SRC.split("def _ff_status(", 1)[1].split("def _ff_rows(", 1)[0]


def test_the_stylesheet_pins_the_header_and_the_layout():
    assert ".modal-xl .modal-body.ff-modal-body { display: flex; flex-direction: column;" in CSS
    assert ".ff-table-wrap { flex: 1; min-height: 0; overflow: auto;" in CSS
    assert ".ff-table-wrap .ff-table { border-collapse: separate; border-spacing: 0; }" in CSS
    assert ".ff-table-wrap .ff-table thead th { position: sticky; top: 0;" in CSS
    assert "#sfari-desktop-metrics .easi-tbl thead th" not in CSS
    assert ".ff-preview-frame {" in CSS and ".ff-src-sub {" in CSS
    assert ".ff-preview-link" not in CSS
    for state in (".ff-dl .btn-primary {", ".ff-dl .btn-primary:hover,"):
        assert state in CSS, state
    assert "ff-dl-secondary" not in CSS and "ff-dl-secondary" not in SRC
    assert 'href="styles.css?v=21"' in SRC


def test_the_worksheet_button_names_the_dialog():
    assert 'ui.tags.button("Field Forms",' in SRC and "Get Field Forms" not in SRC
    assert "Print-ready field packet" not in SRC


def test_filename_helpers():
    assert report.field_forms_filename() == "sfari-field-forms.pdf"
    assert report.field_forms_filename({"delineation": {"comid": 5}}) == "sfari-field-forms.pdf"
    assert report._site_slug({"delineation": {"snapped_lat": 40.1, "snapped_lon": -83.2}}) \
        == "n40.10000-w83.20000"
    assert report._site_slug({}) == ""
    assert report.desktop_metrics_filename({"delineation": {"comid": 5}}) \
        == "sfari-desktop-metrics-comid-5.pdf"
