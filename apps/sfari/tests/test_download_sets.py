"""Get Forms and the report offer the same downloads in EASI, SFARI and DEEP (owner, 2026-10-08):
Get Forms the field forms, the desktop metrics PDF, then the completed and blank workbooks; the
report PDF, CSV, GeoJSON and the completed workbook, then Close. Every download opens its own tab,
and the files carry plain names, with no site id. The modals are built inside server(), so they are
compiled from the source as tests/test_report_opening.py does."""
from __future__ import annotations

import re

import pytest

from test_report_opening import Value, function, scope

app = pytest.importorskip("app")
DOWNLOAD_ATTR = re.compile(r"\sdownload[\s=>]")


def _downloads(html: str) -> list:
    return re.findall(r'<a [^>]*class="[^"]*shiny-download-link[^"]*"[^>]*id="([a-z_]+)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+?)\s*</a>',
                      html, re.S)


def _forms_html() -> str:
    return str(function("_desktop_metrics_modal", dict(vars(app)))())


def _report_html() -> str:
    ns = scope()
    ns["ui"] = app.ui
    ns["metric_scores"] = Value({})
    ns["function_scores"] = Value({})
    ns["evidence"] = Value({})
    ns["scored"] = lambda: app.scoring.score_assessment({})
    ns["_summary_section"] = lambda: None
    ns["_report_title"] = lambda: "Report"
    return str(function("_report_modal", ns)(minimap_html="<svg></svg>"))


def test_get_forms_offers_the_field_forms_the_metrics_and_both_workbooks():
    html = _forms_html()
    assert ">Get Forms<" in html
    assert _downloads(html) == [("dl_field_forms", "Field forms PDF"), ("dl_desktop_metrics", "Desktop metrics PDF"),
                                ("dl_calc_filled", "Completed workbook"), ("dl_calc_blank", "Blank workbook")]
    assert html.count('class="staf-dl"') == 4


def test_the_report_footer_is_the_same_as_in_every_tool():
    footer = _report_html().split('class="modal-footer"', 1)[1]
    assert _downloads(footer) == [("dl_pdf", "PDF"), ("dl_csv", "CSV"), ("dl_geojson", "GeoJSON"),
                                  ("dl_calc_filled", "Completed workbook")]
    assert ">Close<" in footer and "dl_calc_blank" not in footer


def test_every_download_opens_its_own_tab():
    links = [a for page in (_forms_html(), _report_html()) for a in re.findall(r"<a [^>]*shiny-download-link[^>]*>", page)]
    assert len(links) == 8
    for a in links:
        assert 'target="_blank"' in a and 'rel="noopener"' in a and not DOWNLOAD_ATTR.search(a), a


def test_the_download_names_are_plain():
    from sfari import calculator, report
    assert calculator.calculator_filename() == "sfari-calculator.xlsx"
    assert report.desktop_metrics_filename() == "sfari-desktop-metrics.pdf"
    assert report.field_forms_filename() == "sfari-field-forms.pdf"
