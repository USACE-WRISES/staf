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


# --------------------------------------------------------------------------- metric rows
def test_a_row_button_is_outlined_with_an_icon_and_a_label():
    """Owner, 2026-10-04: the buttons under every metric are always visible, outlined, an icon and a
    label, in EASI, SFARI and DEEP alike."""
    html = str(web.metric_action("scoring", "Scoring", title="How this metric is scored"))
    assert html.startswith('<button type="button" data-staf-panel="scoring" aria-expanded="false"')
    assert 'class="staf-act"' in html and html.count("<svg") == 1
    assert '<span class="staf-act-label">Scoring</span>' in html
    assert 'title="How this metric is scored"' in html
    assert "staf-act-dot" not in html and "staf-act-count" not in html


def test_a_note_button_shows_a_dot_and_photos_a_count():
    note = str(web.metric_action("note", "Note", on=True, has=True))
    assert 'class="staf-act on has"' in note and 'aria-expanded="true"' in note
    assert '<span class="staf-act-dot"></span>' in note
    none = str(web.metric_action("photo", "Photo", count=0))
    assert 'class="staf-act"' in none and '<span class="staf-act-count"></span>' in none
    two = str(web.metric_action("photo", "Photo", on=True, count=2))
    assert 'class="staf-act on has"' in two and ">Photos</span>" in two
    assert '<span class="staf-act-count">2</span>' in two
    fn = str(web.metric_action("fnnote", "Note"))
    assert 'data-staf-panel="fnnote"' in fn and fn.count("<svg") == 1 and "staf-act-dot" in fn


def test_a_checkbox_button_keeps_the_apps_own_box():
    from htmltools import tags
    box = tags.input({"type": "checkbox", "data-mid-na": "m1", "checked": "checked"}, class_="deep-na")
    html = str(web.metric_check("N/A", box, on=True, title="Not applicable at this site",
                                extra_class="deep-na-toggle"))
    assert html.startswith("<label") and 'class="staf-act on deep-na-toggle"' in html
    assert 'data-mid-na="m1"' in html and html.count("<svg") == 1
    assert "data-staf-panel" not in html            # a checkbox, not a panel button


def test_the_actions_skip_missing_buttons_and_every_icon_is_a_line_icon():
    html = str(web.metric_actions(web.metric_action("note", "Note"), None))
    assert html.startswith('<div class="staf-metric-acts">') and html.count("<button") == 1
    for kind, svg in web.ROW_ICONS.items():
        assert 'stroke="currentColor"' in svg and 'fill="none"' in svg and 'aria-hidden="true"' in svg, kind
    assert set(web.ROW_ICONS) == {"scoring", "note", "photo", "na"}


def test_scoring_criteria_rows_carry_a_band_dot():
    html = str(web.scoring_criteria([("Strongly Agree", "good", "< 5% impervious"),
                                     ("Disagree", "poor", "10 to 20%")],
                                    title="Example scoring", sub="Illustrative only."))
    assert '<div class="staf-panel-title">Example scoring</div>' in html
    assert '<div class="staf-panel-sub">Illustrative only.</div>' in html
    assert html.count("staf-crit-dot good") == 1 and html.count("staf-crit-dot poor") == 1
    assert "&lt; 5% impervious" in html


def test_the_shared_row_stylesheet_and_script_ship_with_the_library():
    from pathlib import Path
    assets = Path(__file__).resolve().parents[1] / "assets"
    css = (assets / "metric-rows.css").read_text(encoding="utf-8")
    js = (assets / "metric-rows.js").read_text(encoding="utf-8")
    for rule in (".staf-metric {", ".staf-act {", ".staf-act.on {", ".staf-act.has .staf-act-dot",
                 '.staf-metric.show-scoring .staf-metric-panel[data-panel="scoring"]', ".staf-rate {",
                 ".staf-ev-tag {", ".staf-metric-warn {", "@media (max-width: 820px)"):
        assert rule in css, rule
    assert "(hover: hover)" not in css and ":hover .staf-act" not in css   # nothing waits for a hover
    assert "window.STAFMetricRows" in js and "button.staf-act[data-staf-panel]" in js
    assert "—" not in css + js
