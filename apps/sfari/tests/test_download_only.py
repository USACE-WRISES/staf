"""A download never navigates the app's page, workbooks download as a plain file, and the page warns
before work is lost (owner, 2026-10-03). Owner, 2026-10-08: on work computers a download still
replaced the page, because a managed network redirected the same-origin <a download>; every download
now opens its own tab and carries no download attribute."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = (APP / "app.py").read_text(encoding="utf-8")
WORKBOOKS = ("dl_calc_filled", "dl_calc_blank")
DOWNLOAD_ATTR = re.compile(r"\sdownload[\s=>]")      # the attribute, not the class name


def test_every_download_goes_through_the_shared_helpers():
    assert "ui.download_button(" not in SRC and "ui.download_link(" not in SRC
    assert SRC.count("staf_web.report_footer(") == 1 and SRC.count("staf_web.forms_downloads(") == 1
    assert "staf_web.download_button(" not in SRC and "download=" not in SRC


def test_the_shared_button_opens_its_own_tab():
    from sfari._vendor.staf_workbook import web
    html = str(web.download_button("dl_x", "Completed workbook"))
    assert 'target="_blank"' in html and 'rel="noopener"' in html and "shiny-download-link" in html
    assert not DOWNLOAD_ATTR.search(html)


def test_every_workbook_download_is_sent_as_a_plain_file():
    for name in WORKBOOKS:
        m = re.search(r"@render\.download\(([^\n]*)\)\s+def " + name + r"\(\):", SRC)
        assert m and "media_type=staf_web.XLSX_MEDIA_TYPE" in m.group(1), name


def test_the_leave_page_guard_is_loaded_and_fed():
    assert 'ui.tags.script(src="staf/unsaved-guard.js?v=4", defer="")' in SRC
    assert (APP / "www" / "staf" / "unsaved-guard.js").is_file()
    assert "async def _publish_unsaved():" in SRC
    save = SRC.split("def save_session():", 1)[1].split("@render", 1)[0]
    assert "_saved_fp.set(_work_fp())" in save
    load = SRC.split("def _load_session():", 1)[1]
    assert "_saved_fp.set(_work_fp())" in load
