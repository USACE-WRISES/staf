"""Downloads never open a page, workbooks download as a plain file, and the page warns before
work is lost (owner, 2026-10-03)."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = (APP / "app.py").read_text(encoding="utf-8")
WORKBOOKS = ("dl_calc_filled", "dl_calc_blank")


def test_no_shiny_download_button_is_left():
    assert "ui.download_button(" not in SRC and "ui.download_link(" not in SRC
    assert SRC.count("staf_web.download_button(") >= 6


def test_the_shared_button_has_no_target():
    from sfari._vendor.staf_workbook import web
    html = str(web.download_button("dl_x", "Excel Workbook"))
    assert "target" not in html and "download" in html and "shiny-download-link" in html


def test_every_workbook_download_is_sent_as_a_plain_file():
    for name in WORKBOOKS:
        m = re.search(r"@render\.download\(([^\n]*)\)\s+def " + name + r"\(\):", SRC)
        assert m and "media_type=staf_web.XLSX_MEDIA_TYPE" in m.group(1), name


def test_the_leave_page_guard_is_loaded_and_fed():
    assert 'ui.tags.script(src="staf/unsaved-guard.js?v=1", defer="")' in SRC
    assert (APP / "www" / "staf" / "unsaved-guard.js").is_file()
    assert "async def _publish_unsaved():" in SRC
    save = SRC.split("def save_session():", 1)[1].split("@render", 1)[0]
    assert "_saved_fp.set(_work_fp())" in save
    load = SRC.split("def _load_session():", 1)[1]
    assert "_saved_fp.set(_work_fp())" in load
