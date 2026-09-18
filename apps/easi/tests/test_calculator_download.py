"""The Excel calculator ships with the app: the committed workbook, served blank from Get Forms."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from easi import calculator

app = pytest.importorskip("app")
SRC = Path(app.__file__).read_text(encoding="utf-8")


def test_the_blank_is_the_committed_workbook():
    data = calculator.blank_bytes()
    assert data[:2] == b"PK" and len(data) > 20_000
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(calculator.TEMPLATE_PATH.read_bytes()).hexdigest()
    assert calculator.TEMPLATE_VERSION == "1.0"
    assert calculator.blank_filename() == "EASI_Calculator_1.0.xlsx"
    assert calculator.TEMPLATE_PATH.parent.name == "calculator"
    assert calculator.TEMPLATE_PATH.parent.parent.name == "www"     # a static asset, never vendored
    # one calculator is served: no other workbook sits beside it
    assert [p.name for p in calculator.TEMPLATE_PATH.parent.iterdir()] == ["EASI_Calculator_1.0.xlsx"]


def test_the_app_serves_the_calculator_from_get_forms_and_the_report_and_not_the_header():
    # 2026-09-18: the blank workbook lives only in the Get Forms dialog of the Assessment page;
    # the completed one is there and, after GeoJSON, in the report footer (the owner's call)
    assert re.search(r"@render\.download\(filename=calculator\.blank_filename\(\)\)\s+def dl_forms_blank\(\):", SRC)
    assert SRC.count("yield calculator.blank_bytes()") == 1
    assert re.search(r"def dl_forms_filled\(\):\s+res = export_result\(\)\s+if res:\s+"
                     r"yield calculator\.build_filled\(res\)", SRC)
    assert "dl_calc" not in SRC.replace("dl_site_calc", "")
    footer = str(app._dl_buttons())
    assert re.findall(r'id="([^"]+)"', footer) == ["dl_pdf", "dl_csv", "dl_geojson", "dl_workbook", "close_modal"]
    labels = [text.strip() for text in re.sub(r"<[^>]+>", "\n", footer).split("\n") if text.strip()]
    assert labels == ["PDF", "CSV", "GeoJSON", "Completed workbook", "Close"]
    # the visible labels, not the markup (every download link carries target="_blank")
    assert not any("calculator" in label.lower() or "blank" in label.lower() for label in labels)
    # the same file as Get Forms offers, named by the site
    assert re.search(r"@render\.download\(filename=lambda: calculator\.filled_filename\(export_result\(\)\)\)\s+"
                     r"def dl_workbook\(\):", SRC)
    body = SRC.split("def dl_workbook():", 1)[1].split("@render", 1)[0]
    assert "res = export_result()" in body and "yield calculator.build_filled(res)" in body
    # the owner removed the header link on 2026-09-16; the file itself stays reachable under www/
    html = str(app.app_ui)
    assert f'href="calculator/{calculator.blank_filename()}"' not in html
    assert ">Calculator<" not in html


def test_the_batch_popup_serves_the_site_completed_workbook():
    # batch has no Assessment page, so its per-site report offers that site's completed
    # calculator, behind the same stale-bundle guard as the other per-site exports
    assert 'ui.download_button("dl_site_calc", "Completed workbook"' in SRC
    assert "Excel calculator\"" not in SRC
    assert re.search(r'@render\.download\(filename=lambda: _modal_site_file\("xlsx", "calculator"\)\)\s+'
                     r"def dl_site_calc\(\):", SRC)
    body = SRC.split("def dl_site_calc():", 1)[1].split("@render", 1)[0]
    assert "base = _modal_download_base()" in body and "yield calculator.build_filled(base)" in body


def test_the_help_points_to_get_forms():
    help_text = SRC.split("def _help():", 1)[1].split("@reactive.calc", 1)[0]
    assert "**Get Forms** on the Assessment page" in help_text
    assert "**completed workbook**" in help_text and "**blank workbook**" in help_text
    assert "download beside them" not in help_text
    assert "—" not in help_text          # no em dashes in copy
