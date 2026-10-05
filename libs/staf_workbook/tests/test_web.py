"""The scenario controls and the report's summary block (owner, 2026-10-03, second pass): a
dropdown that only switches scenarios, then Add, Edit and Compare links; Delete in the Edit
dialog; each report names its own scenario and nothing else. Add asks where the scenario starts
(owner, 2026-10-05)."""
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


def test_one_scenario_shows_add_and_edit_and_nothing_else():
    html = str(web.scenario_bar(ScenarioSet()))
    menu, links = _menu_and_links(html)
    assert _actions(menu) == ["select"]
    assert _actions(links) == ["new", "edit"]                    # the action keeps its name
    assert "<span>Add</span>" in links and 'title="Add a scenario"' in links and "<span>New</span>" not in links
    assert 'title="Describe Existing Conditions"' in links and "staf-scen solo" in html
    assert "delete" not in html


def test_the_add_dialog_asks_where_the_scenario_starts():
    sset = ScenarioSet()
    sset.add("Restore riparian")                                 # shown now
    hint = "A copy brings its scores and notes; photos stay with the original. Blank starts with no scores."
    html = str(web.add_dialog(sset, name="Alternative 2", hint=hint))
    options = re.findall(r'<option value="([^"]*)"( selected="")?>([^<]*)</option>', html)
    assert [(v, label) for v, _s, label in options] == [
        ("existing", "Existing Conditions"), ("s2", "Restore riparian"), (web.START_BLANK, "Blank")]
    assert [v for v, s, _l in options if s] == ["existing"]      # Existing Conditions by default
    assert f'id="{web.START_INPUT}"' in html and "Add a scenario" in html and hint in html
    assert html.index(f'id="{web.NAME_INPUT}"') < html.index(f'id="{web.START_INPUT}"') < html.index(
        'class="staf-scen-hint"') < html.index(f'id="{web.DESC_INPUT}"')
    footer = html.split('class="modal-footer"', 1)[1]
    save = footer.split('id="staf_sc_save"', 1)[1].split("</button>", 1)[0]
    assert '<span class="action-label">Add</span>' in save and "Delete scenario" not in footer
    kept = str(web.add_dialog(sset, name="x", start=web.START_BLANK, error="Taken"))
    assert re.search(r'<option value="blank" selected="">', kept) and "Taken" in kept
    assert "—" not in html
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "assets" / "staf.css").read_text(encoding="utf-8")
    assert ".staf-dialog .form-select {" in css and ".staf-scen-hint {" in css    # shaped like the fields


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


def test_add_is_disabled_at_the_limit():
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


def test_a_tool_body_names_its_tool_and_its_id_prefix():
    from shiny import module, ui
    assert web.tool_root_attrs("sfari") == dict([("data-staf-tool", "sfari"), ("data-staf-ns", "")])
    body = module.ui(lambda: ui.div(web.tool_root_attrs("sfari")))
    html = str(body("sfari"))
    assert 'data-staf-tool="sfari"' in html and 'data-staf-ns="sfari"' in html


def test_the_shared_scripts_never_spell_an_input_id():
    """Every shared script posts through STAFNs.id (assets/staf-ns.js), so the same file serves a
    standalone app and the STAF app, where every id carries its tool's prefix."""
    from pathlib import Path
    assets = Path(__file__).resolve().parents[1] / "assets"
    scripts = sorted(assets.glob("*.js"))
    assert "staf-ns.js" in [js.name for js in scripts]
    for js in scripts:
        text = js.read_text(encoding="utf-8")
        assert not re.search(r"setInputValue\(\s*[\"']", text), js.name
        assert "getElementById(\"" not in text and "getElementById('" not in text, js.name


def test_every_tool_shows_the_same_header_actions():
    """Owner, 2026-10-05: EASI, SFARI and DEEP share one set of header actions, in one order: New,
    Open and Save (the assessment file), a hairline, then About and Help."""
    from shiny import module
    html = str(web.nav_actions())
    actions = ["nav_new", "load_session", "save_session", "nav_about", "nav_help"]
    assert [i for i in re.findall(r'id="([a-z_]+)"', html) if i in actions] == actions
    text = re.sub(r"<[^>]+>", " ", html)
    assert re.findall(r"\b(New|Open|Save|About|Help)\b", text) == ["New", "Open", "Save", "About", "Help"]
    assert 'accept=".json"' in html and "easi-nav-sep" in html and 'class="easi-nav"' in html
    assert "target" not in html                               # Save downloads in place
    inside = str(module.ui(web.nav_actions)("sfari"))          # the STAF app's module ids
    assert 'id="sfari-nav_new"' in inside and 'id="sfari-save_session"' in inside
    extra = str(web.nav_actions(web.download_link("x", "Local review")))
    assert extra.index("nav_help") < extra.index("Local review")


def test_new_asks_before_clearing_and_about_and_help_close_alike():
    new = str(web.new_dialog("the delineation and every score"))
    assert "Start a new assessment?" in new and "Clear the delineation and every score and start a new" in new
    assert "<strong>Save</strong>" in new and 'id="confirm_new"' in new and "Clear &amp; start new" in new
    assert "btn-danger" in new and "Cancel" in new
    about = str(web.info_dialog("About EASI", "**EASI**, the screening tier."))
    assert "About EASI" in about and "<strong>EASI</strong>" in about and "Close" in about
    assert "—" not in new + about


def test_the_zoom_cue_reads_the_same_in_every_tool_and_pulses_only_for_a_click():
    """Owner, 2026-10-05: zoomed out, every tool says to zoom in, and a map click out there pulses
    the cue instead of trying to snap."""
    slot = str(web.zoom_cue_output())
    assert 'class="staf-zoom-cue-slot"' in slot and 'role="status"' in slot and 'aria-live="polite"' in slot
    assert 'id="zoom_cue"' in slot
    assert web.ZOOM_CUE_TEXT == "Zoom in and click a stream"     # short, the apps' own phrase
    cue = str(web.zoom_cue(3, pulse=True))
    assert web.ZOOM_CUE_TEXT in cue and "is-nudged" in cue and 'data-nudge="3"' in cue
    assert "is-nudged" not in str(web.zoom_cue(3)) and "—" not in web.ZOOM_CUE_TEXT
    state = web.ZoomCue()
    assert "is-nudged" not in str(state.render(True, 0))
    assert "is-nudged" in str(state.render(True, 1))           # the render a click caused
    assert "is-nudged" not in str(state.render(True, 1))       # any later render of the same cue
    assert state.render(False, 1) is None                      # zoomed in, or another step


def test_the_header_actions_are_styled_once_for_every_tool():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "assets" / "staf.css").read_text(encoding="utf-8")
    for rule in (".easi-nav .easi-nav-btn.btn {", ".easi-nav .btn-file {", ".easi-nav .easi-nav-sep {",
                 ".easi-nav .input-group .form-control { display: none; }",
                 ".easi-nav .shiny-file-input-progress { display: none; }"):
        assert rule in css, rule
    # the zoom cue: centred on the page (under the header's switch) with both gutters the wider
    # inset, one line that starts at the left gutter only where it does not fit (safe centring);
    # never taking a click; still under reduced motion. The left inset follows each tool's own
    # pane width (EASI's pane keeps 352px where SFARI's and DEEP's narrow).
    gutter = "max(var(--staf-map-inset-left), var(--staf-map-inset-right));"
    for rule in (".staf-zoom-cue-slot {", f"left: {gutter}", f"right: {gutter}", "pointer-events: none",
                 "--staf-map-inset-left: calc(var(--staf-pane-width, 352px) + 24px);",
                 "justify-content: safe center;", "padding: 6px 14px; white-space: nowrap;",
                 "@keyframes staf-zoom-cue-nudge", "@media (prefers-reduced-motion: reduce)"):
        assert rule in css, rule
    assert "--staf-map-inset-left: 346px" not in css
