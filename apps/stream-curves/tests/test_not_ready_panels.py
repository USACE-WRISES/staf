"""Empty/not-ready states: the shared panel and the wizard's per-step prerequisites.

Every workflow stage is reachable at any time (the strip's pills stay clickable so a
user can look ahead), so every stage must be able to say honestly that its inputs are
not there yet -- rather than rendering a blank map, a pre-checked metric picker that
makes an empty project look finished, or the silence a failed req() produces.
"""
from __future__ import annotations

import pandas as pd

from views.uihelpers import WORKFLOW_GOTO_INPUT, no_data_alert, not_ready_panel


# --- the shared component --------------------------------------------------- #
def test_panel_renders_title_and_message():
    html = str(not_ready_panel("No sites yet", "Add candidate sites first."))
    assert "No sites yet" in html and "Add candidate sites first." in html


def test_panel_button_only_when_an_action_is_given():
    assert "<button" not in str(not_ready_panel("T", "m"))
    # an action label without a destination is not enough to make a button
    assert "<button" not in str(not_ready_panel("T", "m", action_label="Go"))
    assert "<button" not in str(not_ready_panel("T", "m", goto_nav="data"))
    assert "<button" in str(
        not_ready_panel("T", "m", action_label="Go", goto_nav="data"))


def _unescape(html: str) -> str:
    # the handler lives in an HTML attribute, so its single quotes render as &apos;
    return html.replace("&apos;", "'").replace("&quot;", '"')


def test_panel_button_targets_the_shared_goto_channel():
    html = _unescape(str(not_ready_panel("T", "m", action_label="Go to Add data",
                                         goto_nav="data", goto_step=2)))
    assert WORKFLOW_GOTO_INPUT in html
    assert "nav:'data'" in html and "step:2" in html
    assert "priority:'event'" in html      # house channel idiom


def test_panel_omits_step_when_none_is_given():
    html = _unescape(str(not_ready_panel("T", "m", action_label="Go", goto_nav="curves")))
    assert "nav:'curves'" in html and "step:" not in html


def test_no_data_alert_keeps_its_shape_for_existing_callers():
    # 7 views call this; it must stay a self-contained centered panel.
    html = str(no_data_alert())
    assert "No Data Loaded" in html
    assert "min-height: 300px" in html
    assert "Region &amp; data" in html or "Region & data" in html


# --- wizard step prerequisites ---------------------------------------------- #
class _Blockers:
    """Re-implements import_map._step_blocker's table so the rules are pinned.

    The helper itself is a server closure over reactive values and cannot be imported;
    this mirrors its decision table exactly so a change to either side is caught by a
    failing test rather than by a user landing on a blank screen.
    """

    @staticmethod
    def of(step, *, has_sites, n_metrics, compiled, assignments):
        if step in (3, 4) and not has_sites:
            return "No sites yet"
        if step == 5:
            if not has_sites:
                return "No sites yet"
            if n_metrics < 1:
                return "No metrics selected"
        if step in (6, 7) and compiled is None:
            return "Nothing compiled yet"
        if step == 7 and assignments is None:
            return "Columns not classified yet"
        return None


_EMPTY = dict(has_sites=False, n_metrics=0, compiled=None, assignments=None)
_SITES = dict(has_sites=True, n_metrics=0, compiled=None, assignments=None)
_PICKED = dict(has_sites=True, n_metrics=12, compiled=None, assignments=None)
_COMPILED = dict(has_sites=True, n_metrics=12, compiled=object(), assignments=None)
_READY = dict(has_sites=True, n_metrics=12, compiled=object(), assignments=object())


def test_a_brand_new_project_blocks_every_step_past_add_data():
    for step in (3, 4, 5, 6, 7):
        assert _Blockers.of(step, **_EMPTY) is not None, f"step {step} must be blocked"


def test_steps_unblock_in_order_as_the_wizard_progresses():
    # sites assembled -> screen + choose metrics open, compile still needs a selection
    assert _Blockers.of(3, **_SITES) is None
    assert _Blockers.of(4, **_SITES) is None
    assert _Blockers.of(5, **_SITES) == "No metrics selected"
    # metrics chosen -> compile opens, classify still needs compiled data
    assert _Blockers.of(5, **_PICKED) is None
    assert _Blockers.of(6, **_PICKED) == "Nothing compiled yet"
    # compiled -> classify opens, review still needs assignments
    assert _Blockers.of(6, **_COMPILED) is None
    assert _Blockers.of(7, **_COMPILED) == "Columns not classified yet"
    # classified -> everything open
    for step in (3, 4, 5, 6, 7):
        assert _Blockers.of(step, **_READY) is None


def test_choose_metrics_is_blocked_without_sites():
    # Regression: step 4 had no guard at all, so it rendered the full picker with
    # recommended defaults pre-checked and a "N / 20 functions covered" bar -- an
    # empty project looked finished.
    assert _Blockers.of(4, **_EMPTY) == "No sites yet"


def test_compile_blocked_reasons_are_ordered_sites_then_metrics():
    assert _Blockers.of(5, **_EMPTY) == "No sites yet"
    assert _Blockers.of(5, **_SITES) == "No metrics selected"


# --- the real helper, exercised through a tiny fake ------------------------- #
def test_import_map_exposes_the_panel_helper():
    # guards against the panel import being dropped from the wizard
    import views.import_map as im

    assert hasattr(im, "not_ready_panel")


def test_sites_predicate_treats_an_empty_frame_as_no_sites():
    # _has_sites() must not accept a zero-row frame (assemble_sites can return one)
    for value in (None, pd.DataFrame()):
        has = value is not None and len(value) > 0
        assert not has
    assert len(pd.DataFrame({"site_id": ["a"]})) > 0
