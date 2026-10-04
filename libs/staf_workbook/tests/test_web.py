"""The scenario controls and the report's summary block (owner, 2026-10-03, second pass): a
dropdown that only switches scenarios, then New, Edit and Compare links; Delete in the Edit
dialog; each report names its own scenario and nothing else."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("htmltools")
pytest.importorskip("shiny")

from staf_workbook import web  # noqa: E402
from staf_workbook.model.scenarios import MAX_SCENARIOS, ScenarioSet  # noqa: E402
from staf_workbook.model.summary import SummaryInfo, regions_for_l3  # noqa: E402


def _menu_and_links(html: str):
    menu = html.split('class="staf-scen-menu"', 1)[1].split('class="staf-scen-actions"', 1)[0]
    links = html.split('class="staf-scen-actions"', 1)[1]
    return menu, links


def _actions(fragment: str) -> list:
    return re.findall(r'data-sc-action="([a-z]+)"', fragment)


def test_one_scenario_shows_new_and_edit_and_nothing_else():
    html = str(web.scenario_bar(ScenarioSet()))
    menu, links = _menu_and_links(html)
    assert _actions(menu) == ["select"]
    assert _actions(links) == ["new", "edit"]
    assert 'title="Describe Existing Conditions"' in links and "staf-scen solo" in html
    assert "delete" not in html


def test_alternatives_add_compare_and_the_menu_only_switches():
    sset = ScenarioSet()
    sset.add("Restore riparian", "Replant the buffer")
    html = str(web.scenario_bar(sset))
    menu, links = _menu_and_links(html)
    assert _actions(menu) == ["select", "select"]
    assert _actions(links) == ["new", "edit", "compare"]
    assert 'title="Rename or describe"' in links
    assert '<span class="staf-scen-item-desc">Replant the buffer</span>' in menu      # description under its name
    assert menu.count("staf-scen-item-desc") == 1                                     # none for an undescribed one
    assert "staf-scen-chip alt" in html and "delete" not in html
    assert links.count("<svg") == 3


def test_new_is_disabled_at_the_limit():
    sset = ScenarioSet()
    for _ in range(MAX_SCENARIOS - 1):
        sset.add()
    links = _menu_and_links(str(web.scenario_bar(sset)))[1]
    new = links.split("</button>", 1)[0]
    assert 'data-sc-action="new"' in new and "disabled" in new and 'title="Up to 10 scenarios"' in new


def test_the_edit_dialog_offers_delete_only_for_an_alternative():
    alt = str(web.scenario_dialog(title="Rename or describe", name="A", description="", name_locked=False,
                                  can_delete=True))
    base = str(web.scenario_dialog(title="Describe Existing Conditions", name="Existing Conditions",
                                   description="", name_locked=True))
    footer = alt.split('class="modal-footer"', 1)[1]
    assert footer.index("Delete scenario") < footer.index("Cancel")                    # left of the others
    assert 'data-sc-action="delete"' in footer and "me-auto" in footer
    assert "Delete scenario" not in base


def test_every_scenario_dialog_uses_the_apps_buttons():
    """Owner, 2026-10-03 (third pass): Cancel, Save and Delete look like the page's Open report
    button (each app's own .sfari-btn, so DEEP's smaller one too), and Delete reads as a button."""
    edit = str(web.scenario_dialog(title="Rename or describe", name="A", description="", name_locked=False,
                                   can_delete=True))
    footer = edit.split('class="modal-footer"', 1)[1]
    buttons = re.findall(r'<button[^>]*class="([^"]+)"', footer)
    assert buttons == ["btn sfari-btn staf-btn danger me-auto", "btn btn-default sfari-btn staf-btn",
                       "btn btn-default action-button sfari-btn primary staf-btn"]
    assert "btn-link" not in edit and "btn-primary" not in edit
    assert 'class="modal-body staf-dialog"' in edit
    confirm = str(web.delete_dialog("A"))
    assert 'class="modal-body staf-dialog"' in confirm and "sfari-btn staf-btn danger-solid" in confirm
    assert "btn-danger" not in confirm
    from staf_workbook.model.compare import ScenarioScores, build
    compare = str(web.compare_dialog(build(["Existing Conditions"], [ScenarioScores()], [])))
    assert 'class="modal-body staf-dialog"' in compare and "sfari-btn staf-btn" in compare


def test_report_scenario_and_file_suffix_rules():
    sset = ScenarioSet()
    assert web.report_scenario(sset) is None and web.scenario_suffix(sset) == ""
    sset.describe("existing", "Before the restoration")
    assert web.report_scenario(sset) == ("Existing Conditions", "Before the restoration")
    sset.add("Restore riparian (phase 1)", "")
    assert web.report_scenario(sset) == ("Restore riparian (phase 1)", "")
    assert web.scenario_suffix(sset) == "-restore-riparian-phase-1"
    sset.select("existing")
    assert web.scenario_suffix(sset) == ""


def test_the_summary_block_names_the_scenario_first():
    info = SummaryInfo("SFARI", "Mink Brook", 1000, 12.3, 3, regions_for_l3("58"))
    plain = str(web.summary_block(info))
    named = str(web.summary_block(info, scenario=("Restore riparian", "Replant the buffer")))
    assert "Scenario" not in plain
    labels = re.findall(r"<th>([^<]+)</th>", named)
    assert labels[:3] == ["Scenario", "Description", "Reach name"]
    assert "Description" not in str(web.summary_block(info, scenario=("Restore riparian", "")))
