"""The Rules tool chip, page wiring, and deep-link channel.

Source-scan style (the test_stagebar_nav idiom): the wiring is declarative
enough that reading the declarations beats spinning up a Shiny session, and a
missing declaration is exactly the class of bug that ships silently."""
from __future__ import annotations

import json
import re
from pathlib import Path

from streamcurves import run_state as rs
from streamcurves import rules_view
from streamcurves import session_io as sio

APP_DIR = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (APP_DIR / rel).read_text(encoding="utf-8")


def test_the_rules_tool_is_in_every_strip_vocabulary():
    assert "rules" in rs.TOOL_KEYS
    assert rs.TOOL_LABELS["rules"] == "Rules"
    assert "rules" in rs.TOOLS_WITHOUT_DATA, "the page reads configs, never the project"


def test_the_stagebar_declares_the_chip_and_a_guarded_handler():
    text = _source("views/stagebar.py")
    assert '"rules": "tool_rules"' in text
    assert re.search(r"@reactive\.event\(input\.tool_rules\)\s*\n\s*@guard", text), \
        "the tool_rules handler is missing or unguarded"


def test_every_tool_icon_is_vendored_so_bi_cannot_raise():
    text = _source("views/stagebar.py")
    m = re.search(r"_TOOL_ICON = \{(.*?)\}", text, re.S)
    icons = dict(re.findall(r'"(\w+)":\s*"([\w-]+)"', m.group(1)))
    assert set(icons) == set(rs.TOOL_KEYS)
    vendored = json.loads(_source("www/vendor/bs-icons.json"))
    for key, icon in icons.items():
        assert icon in vendored, f"{key}: icon {icon} is not in bs-icons.json"


def test_the_app_mounts_the_panel_and_consumes_the_goto_channel():
    text = _source("app.py")
    assert 'value="rules"' in text
    assert 'rules_server("rules", state' in text
    assert "RULES_GOTO_INPUT" in text
    assert "rules_anchor_nonce" in text


def test_the_chip_targets_the_fixed_channel():
    text = _source("views/uihelpers.py")
    assert 'RULES_GOTO_INPUT = "rules_goto"' in text
    chip = text[text.index("def _rules_goto_onclick"):]
    assert "RULES_GOTO_INPUT" in chip[:400]


def test_every_rules_page_effect_is_guarded():
    """An unguarded exception in a reactive effect closes the Shiny session and
    loses the project (the guard() contract)."""
    text = _source("views/rules.py")
    effects = re.findall(r"@reactive\.effect\s*\n(.*?)def ", text, re.S)
    assert effects, "rules.py declares no effects?"
    for decorators in effects:
        assert "@guard(" in decorators, f"unguarded effect: {decorators!r}"


def test_the_region_builder_reads_the_selection_instead_of_its_own_checkboxes():
    text = _source("views/region_builder.py")
    assert "input.build_policies" not in text, \
        "the Rules page is the single writer of the opt-in selection"
    assert "state.rule_selections()" in text
    assert "validate_selections" in text
    assert "policy_summary" in text


def test_the_rule_selection_survives_save_and_reopen():
    assert "rule_selections" in sio.SESSION_FIELDS
    payload = sio.dump_session_fields(
        {"session_name": "rules", "rule_selections": ["ref02-accept-best-available"]},
        session_name="rules")
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert back["rule_selections"] == ["ref02-accept-best-available"]
    old = sio.dump_session_fields({"session_name": "old"}, session_name="old")
    old["fields"].pop("rule_selections", None)
    assert sio.decode_session_fields(
        json.loads(sio.dumps_session(old))).get("rule_selections") is None


def test_the_restore_path_validates_the_selection():
    text = _source("views/data_overview.py")
    assert "rules_view.validate_selections" in text


def test_the_rules_page_renders_every_optional_entry_as_a_checkbox():
    text = _source("views/rules.py")
    assert "_checkbox_id" in text and 'replace("-", "_")' in text, \
        "policy ids carry hyphens; Shiny input ids cannot"
    # the checkbox loop is driven by the policy, not a hand-kept list
    assert "optional_policy_ids" in text
    assert rules_view.optional_policy_ids(), "no opt-in entries to render?"


def test_the_search_input_renders_outside_the_table_output():
    """The load-bearing three-output split: the filter inputs live in the
    shell render, so typing in the search box cannot re-render (and blank)
    the box itself."""
    text = _source("views/rules.py")
    assert 'ui.output_ui("rules_table")' in text
    assert 'ui.output_ui("rules_enabled_line")' in text
    shell = text[text.index("def rules_page"):text.index("def rules_enabled_line")]
    assert 'ns("rules_search")' in shell and 'ns("rules_adjustable_only")' in shell, \
        "the filter inputs must live in the shell render"


def test_a_rules_deep_link_clears_filters_and_expands_the_row():
    """A chip click must land on a visible, opened row even when a filter was
    set: reset both filters, open the detail row, then scroll."""
    text = _source("views/rules.py")
    scroll = text[text.index("async def _scroll_to_rule"):]
    for needle in ("ui.update_checkbox", "ui.update_text",
                   '"rulesExpandRow"', '"scrollToElement"'):
        assert needle in scroll, f"deep-link flow is missing {needle}"
    js = _source("www/curves.js")
    assert js.count('"rulesExpandRow"') >= 1
    handler = js[js.index('"rulesExpandRow"'):]
    assert "offsetParent" in handler, "the expand handler must wait for layout"
    assert "rules-detail-row" in handler


def test_the_jump_bar_and_family_anchors_share_the_dom_id_helper():
    """The chips and the heading rows must agree on the anchor ids."""
    assert _source("views/rules.py").count("family_dom_id") >= 2
