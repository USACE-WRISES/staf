"""The Field Forms dialog: SFARI's shell, opened from the worksheet rail.

Source assertions, the way SFARI's own dialog is pinned: the rail button is a
plain button that www/measure.js delegates to one event, the modal is the static
``ff-modal-body`` shell with two tabs and the downloads in the tab strip, every
dialog output binds while the modal is still hidden, and the preview is served
inline through a session route.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "www" / "measure.js").read_text(encoding="utf-8")
CSS = (ROOT / "www" / "styles.css").read_text(encoding="utf-8")
SFARI_CSS = (ROOT.parent / "sfari" / "www" / "styles.css")


def test_the_rail_button_is_a_delegated_plain_button():
    assert '"data-field-forms": "1"' in SRC
    assert 'ui.tags.button("Get Field Forms"' in SRC
    # no Shiny download sits in the rail any more: the dialog owns the downloads
    rail = SRC[SRC.index("def worksheet():"):SRC.index("def fn_nav():")]
    assert "download_button" not in rail
    assert 'closest("[data-field-forms]")' in JS
    assert 'send("field_forms_evt", {})' in JS


def test_the_event_opens_the_modal_and_cancels_a_pending_report():
    block = SRC[SRC.index("def _open_field_forms():"):SRC.index("def _calculator_template():")]
    assert "@reactive.event(input.field_forms_evt)" in SRC
    assert "_cancel_report()" in block and "ui.modal_show(_field_forms_modal())" in block


def test_the_modal_is_sfaris_shell():
    modal = SRC[SRC.index("def _field_forms_modal():"):SRC.index("_FF_BADGE = {")]
    assert 'title="Field Forms", easy_close=True, size="xl"' in modal
    assert 'class_="ff-modal-body"' in modal and "footer=ui.modal_button(\"Close\")" in modal
    assert "ui.navset_pill(" in modal and 'id="ff_tabs", selected="metrics"' in modal
    assert 'ui.nav_panel("Metrics",' in modal
    assert 'ui.nav_panel("Field forms preview", ui.output_ui("ff_preview")' in modal
    assert "ui.nav_spacer()" in modal
    assert 'class_="ff-table-wrap"' in modal


def test_the_four_downloads_are_equal_primary_buttons():
    modal = SRC[SRC.index("def _field_forms_modal():"):SRC.index("_FF_BADGE = {")]
    labels = re.findall(r'ui\.download_button\("(dl_[a-z_]+)", "([^"]+)"', modal)
    assert labels == [("dl_field_forms", "Field forms PDF"), ("dl_metrics_pdf", "Metrics PDF"),
                      ("dl_calc_filled", "Completed workbook"),
                      ("dl_calc_blank", "Blank workbook")]
    assert modal.count('class_="btn-sm btn-primary"') == 4
    assert modal.count('class_="ff-dl"') == 4
    # a version without a calculator says so in a plain sentence
    assert "No Excel calculator is published for this version of the assessment." in modal
    for handler in ("def dl_field_forms():", "def dl_metrics_pdf():", "def dl_calc_blank():",
                    "def dl_calc_filled():"):
        assert handler in SRC


def test_every_dialog_output_binds_while_the_modal_is_hidden():
    for name in ("ff_site", "ff_status", "ff_table", "ff_preview"):
        match = re.search(r"@output\(suspend_when_hidden=False\)\s+@render\.ui\s+def "
                          + name + r"\(\):", SRC)
        assert match, name


def test_the_status_line_owns_the_polling():
    status = SRC[SRC.index("def ff_status():"):SRC.index("def ff_table():")]
    table = SRC[SRC.index("def ff_table():"):SRC.index("def _ff_preview_route(request):")]
    assert "reactive.invalidate_later(1.0)" in status
    assert "invalidate_later" not in table            # the table never polls
    assert "with reactive.isolate():" in table


def test_the_preview_is_served_inline_by_a_session_route():
    assert 'session_.dynamic_route("field-forms-preview", _ff_preview_route)' in SRC
    route = SRC[SRC.index("def _ff_preview_route(request):"):SRC.index("_ff_preview_url =")]
    assert 'media_type="application/pdf"' in route and "inline; filename=" in route
    assert '"Cache-Control": "no-store"' in route
    assert "with reactive.isolate():" in route        # the route has no reactive context
    assert 'class_="ff-preview-frame"' in SRC


def test_the_dialog_styles_are_sfaris():
    assert ".modal-xl .modal-body.ff-modal-body" in CSS
    for rule in (".ff-table-wrap", ".ff-badge", ".ff-preview-frame", ".ff-status", ".ff-dl"):
        assert rule in CSS, rule
    if SFARI_CSS.is_file():
        sfari = SFARI_CSS.read_text(encoding="utf-8")
        start = sfari.index(".modal-dialog.modal-xl:has(.ff-modal-body)")
        end = sfari.index("/* Stream legend")
        ours = CSS[CSS.index(".modal-dialog.modal-xl:has(.ff-modal-body)"):CSS.index(
            "/* Stream legend")]
        assert " ".join(sfari[start:end].split()) == " ".join(ours.split())


def test_the_cache_bust_versions_moved_with_the_assets():
    assert 'href="styles.css?v=20"' in SRC and 'src="measure.js?v=5"' in SRC
