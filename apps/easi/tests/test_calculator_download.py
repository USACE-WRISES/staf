"""The Excel calculator ships with the app: the committed workbook, served blank."""
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
    assert calculator.blank_filename() == f"EASI_Calculator_{calculator.TEMPLATE_VERSION}.xlsx"
    assert calculator.TEMPLATE_PATH.parent.name == "calculator"
    assert calculator.TEMPLATE_PATH.parent.parent.name == "www"     # a static asset, never vendored


def test_the_app_serves_the_calculator_from_both_modals_and_the_header():
    assert 'ui.download_button("dl_calc", "Excel calculator"' in SRC
    assert 'ui.download_button("dl_site_calc", "Excel calculator"' in SRC
    assert re.search(r"@render\.download\(filename=calculator\.blank_filename\(\)\)\s+def dl_calc\(\):", SRC)
    assert re.search(r"@render\.download\(filename=calculator\.blank_filename\(\)\)\s+def dl_site_calc\(\):", SRC)
    assert 'href=f"calculator/{calculator.blank_filename()}"' in SRC
    html = str(app.app_ui)
    assert f'href="calculator/{calculator.blank_filename()}"' in html
    assert "Excel calculator" in app._help_markdown_probe() if hasattr(app, "_help_markdown_probe") else True
