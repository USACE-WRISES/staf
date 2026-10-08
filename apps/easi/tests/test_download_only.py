"""A download never navigates the app's page, workbooks download as a plain file, and the page warns
before work is lost. Owner, 2026-10-03: a workbook click opened a viewer that showed blank formulas
and the page lost its progress. Owner, 2026-10-08: on work computers a download still replaced the
page, because a managed network redirected the same-origin <a download> and the browser then
navigates the page that clicked it. Every download now opens its own tab and carries no download
attribute; the browser closes the tab once the attachment starts."""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = (APP / "app.py").read_text(encoding="utf-8")
WORKBOOKS = ("dl_workbook", "dl_forms_filled", "dl_forms_blank", "dl_site_calc")
DOWNLOAD_ATTR = re.compile(r"\sdownload[\s=>]")      # the attribute, not the class name


def test_every_download_goes_through_the_shared_helpers():
    assert "ui.download_button(" not in SRC and "ui.download_link(" not in SRC
    assert SRC.count("staf_web.report_footer(") == 2            # the single-site and the batch report
    assert SRC.count("staf_web.forms_downloads(") == 1          # Get Forms
    assert SRC.count("staf_web.download_button(") == 2          # the batch ZIP and the dashboard CSV
    assert "download=" not in SRC


def test_the_shared_button_opens_its_own_tab():
    from easi._vendor.staf_workbook import web
    html = str(web.download_button("dl_x", "Completed workbook", class_="btn-sm btn-primary"))
    assert 'target="_blank"' in html and 'rel="noopener"' in html and not DOWNLOAD_ATTR.search(html)
    assert 'class="btn btn-default shiny-download-link disabled btn-sm btn-primary"' in html
    assert web.XLSX_MEDIA_TYPE == "application/octet-stream"


def test_every_workbook_download_is_sent_as_a_plain_file():
    for name in WORKBOOKS:
        m = re.search(r"@render\.download\(([^\n]*)\)\s+def " + name + r"\(\):", SRC)
        assert m and "media_type=staf_web.XLSX_MEDIA_TYPE" in m.group(1), name


def test_the_leave_page_guard_is_loaded_and_fed():
    assert 'ui.tags.script(src="staf/unsaved-guard.js?v=4", defer="")' in SRC
    assert (APP / "www" / "staf" / "unsaved-guard.js").is_file()
    assert "async def _publish_unsaved():" in SRC
    assert "send_custom_message(staf_web.UNSAVED_MESSAGE" in SRC


def test_the_shared_toolkit_stays_out_of_the_acquisition_digest():
    from easi import method_package
    rels = [p.as_posix() for p in method_package.acquisition_sources()]
    assert not any("staf_workbook" in r for r in rels)
    assert not any(r.endswith(("/easi/workbook.py", "/easi/scenario_state.py")) for r in rels)
