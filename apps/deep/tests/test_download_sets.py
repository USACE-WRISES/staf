"""Get Forms and the report offer the same downloads in EASI, SFARI and DEEP (owner, 2026-10-08):
Get Forms the field forms, the metrics PDF, then the completed and blank workbooks (only when a
calculator is published for the loaded version; a note stands in their place otherwise); the report
PDF, CSV, GeoJSON and the completed workbook (when there is a calculator), then Close. Every download
opens its own tab. The modals are built inside server(), so they are compiled from the source as
tests/test_report_opening.py does."""
from __future__ import annotations

import re

import pytest

from test_report_opening import Value, function, scope

app = pytest.importorskip("app")
DOWNLOAD_ATTR = re.compile(r"\sdownload[\s=>]")


def _downloads(html: str) -> list:
    return re.findall(r'<a [^>]*class="[^"]*shiny-download-link[^"]*"[^>]*id="([a-z_]+)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+?)\s*</a>',
                      html, re.S)


def _forms_html(template) -> str:
    ns = dict(vars(app))
    ns["_calculator_template"] = lambda: template
    return str(function("_field_forms_modal", ns)())


def _report_html(template) -> str:
    ns = scope()
    ns["ui"] = app.ui
    la = app.assessments.LoadedAssessment.from_dict({
        "assessmentId": "fixture", "assessmentName": "Fixture", "metricsByFunction": []})
    ns["loaded_assessment"].set(la)
    ns["_fns"] = lambda: []
    ns["measured_values"] = Value({})
    ns["scored"] = lambda: app.curves.score_site(la, {})
    ns["_summary_section"] = lambda: None
    ns["_report_title"] = lambda: "Report"
    ns["_calculator_template"] = lambda: template
    return str(function("_report_modal", ns)(minimap_html="<svg></svg>"))


def test_get_forms_offers_the_field_forms_the_metrics_and_both_workbooks():
    html = _forms_html(b"PK")
    assert ">Get Forms<" in html
    assert _downloads(html) == [("dl_field_forms", "Field forms PDF"), ("dl_metrics_pdf", "Metrics PDF"),
                                ("dl_calc_filled", "Completed workbook"), ("dl_calc_blank", "Blank workbook")]


def test_get_forms_without_a_calculator_says_so_where_the_workbooks_would_be():
    html = _forms_html(None)
    assert [i for i, _label in _downloads(html)] == ["dl_field_forms", "dl_metrics_pdf"]
    assert html.index("dl_metrics_pdf") < html.index('class="staf-dl-note"')


def test_the_report_footer_is_the_same_as_in_every_tool():
    footer = _report_html(b"PK").split('class="modal-footer"', 1)[1]
    assert _downloads(footer) == [("dl_pdf", "PDF"), ("dl_csv", "CSV"), ("dl_geojson", "GeoJSON"),
                                  ("dl_calc_filled", "Completed workbook")]
    assert ">Close<" in footer
    none = _report_html(None).split('class="modal-footer"', 1)[1]
    assert [i for i, _label in _downloads(none)] == ["dl_pdf", "dl_csv", "dl_geojson"]


def test_every_download_opens_its_own_tab():
    pages = (_forms_html(b"PK"), _report_html(b"PK"))
    links = [a for page in pages for a in re.findall(r"<a [^>]*shiny-download-link[^>]*>", page)]
    assert len(links) == 8
    for a in links:
        assert 'target="_blank"' in a and 'rel="noopener"' in a and not DOWNLOAD_ATTR.search(a), a
