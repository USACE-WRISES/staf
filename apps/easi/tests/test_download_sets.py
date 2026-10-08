"""Get Forms and the report offer the same downloads in EASI, SFARI and DEEP (owner, 2026-10-08):
Get Forms the metrics PDF, then the completed and blank workbooks (EASI has no field forms); the
report PDF, CSV, GeoJSON and the completed workbook, then Close, in a footer pinned under the body.
Every download opens its own tab, and the files carry plain names, with no site id."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from easi import calculator, report
from test_get_forms_dialog import _result

app = pytest.importorskip("app")
DOWNLOAD_ATTR = re.compile(r"\sdownload[\s=>]")


def _downloads(html: str) -> list:
    return re.findall(r'<a [^>]*class="[^"]*shiny-download-link[^"]*"[^>]*id="([a-z_]+)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+?)\s*</a>',
                      html, re.S)


def _links(html: str) -> list:
    return re.findall(r'<a [^>]*shiny-download-link[^>]*>', html)


def _footer(html: str) -> str:
    return html.split('class="modal-footer"', 1)[1]


def test_get_forms_offers_the_metrics_pdf_and_both_workbooks():
    html = str(app._forms_modal(_result()))
    assert ">Get Forms<" in html
    assert _downloads(html) == [("dl_forms_pdf", "Desktop metrics PDF"), ("dl_forms_filled", "Completed workbook"),
                                ("dl_forms_blank", "Blank workbook")]
    assert html.count('class="staf-dl"') == 3


def test_get_forms_without_a_calculator_says_so_where_the_workbooks_would_be(monkeypatch):
    monkeypatch.setattr(app.calculator, "available", lambda: False)
    html = str(app._forms_modal(_result()))
    assert _downloads(html) == [("dl_forms_pdf", "Desktop metrics PDF")]
    assert html.index("dl_forms_pdf") < html.index('class="staf-dl-note"')


def test_the_report_footer_is_pinned_and_the_same_as_in_every_tool():
    html = str(app._report_modal(_result(), {}))
    body, footer = html.split('class="modal-footer"', 1)
    assert _downloads(footer) == [("dl_pdf", "PDF"), ("dl_csv", "CSV"), ("dl_geojson", "GeoJSON"),
                                  ("dl_workbook", "Completed workbook")]
    assert ">Close<" in footer and "data-bs-dismiss" in footer
    assert "shiny-download-link" not in body.split('id="easi-report"', 1)[1]
    assert 'id="close_modal_x"' in body and "easi-modal-x" in body    # the header's close button stays


def test_the_report_without_a_calculator_drops_the_workbook(monkeypatch):
    monkeypatch.setattr(app.calculator, "available", lambda: False)
    footer = _footer(str(app._report_modal(_result(), {})))
    assert [i for i, _label in _downloads(footer)] == ["dl_pdf", "dl_csv", "dl_geojson"]


def test_the_batch_report_has_the_same_footer(monkeypatch):
    base = _result()
    footer = _footer(str(app._batch_report_modal("Site A", base)))
    assert _downloads(footer) == [("dl_site_pdf", "PDF"), ("dl_site_csv", "CSV"), ("dl_site_geojson", "GeoJSON"),
                                  ("dl_site_calc", "Completed workbook")]
    monkeypatch.setattr(app.calculator, "available", lambda: False)
    footer = _footer(str(app._batch_report_modal("Site A", base)))
    assert "dl_site_calc" not in footer


def test_every_download_opens_its_own_tab():
    pages = [str(app._forms_modal(_result())), str(app._report_modal(_result(), {})),
             str(app._batch_report_modal("Site A", _result())), str(app.app_ui)]
    links = [a for page in pages for a in _links(page)]
    assert len(links) >= 12 and any('id="save_session"' in a for a in links)
    for a in links:
        assert 'target="_blank"' in a and 'rel="noopener"' in a and not DOWNLOAD_ATTR.search(a), a


def test_the_download_names_are_plain():
    assert calculator.filled_filename() == "easi-calculator.xlsx"
    assert report.desktop_metrics_filename() == "easi-desktop-metrics.pdf"
    src = Path(app.__file__).read_text(encoding="utf-8")
    assert 'f"easi-report{staf_web.scenario_suffix(' in src and "easi_report" not in src
    assert '@render.download(filename="easi-report.pdf")' in src and "_modal_site_file" not in src
    assert not re.search(r"comid-|nhdplusid-", calculator.filled_filename() + report.desktop_metrics_filename())
