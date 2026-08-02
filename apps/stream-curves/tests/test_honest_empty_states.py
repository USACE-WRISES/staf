"""What a metric with nothing to analyse should say.

The three analysis tabs used to collapse three different situations into one
"has not been run" message. A published assessment that defined no stratification
variables read exactly like one whose screening had failed, and because the
Exploratory message was a warning, every metric of every published assessment
carried a "Run (warnings)" badge for work that was never possible.
"""

from __future__ import annotations

import pandas as pd
import pytest

from views import summary_state as ss
from views.state import AppState

METRIC = "chem_PTL"

NOT_APPLICABLE_EXPLORATORY = (
    "This project has no stratification variables, so there is nothing to screen."
)
NOT_ENABLED_EXPLORATORY = (
    "No stratification is enabled for this metric, so exploratory screening does not apply."
)
COLUMNS_ABSENT_EXPLORATORY = (
    "Stratifications are configured for this metric but their columns are not in the data."
)
NEVER_RAN_EXPLORATORY = "Exploratory screening has not been run for this metric."


def _texts(state, metric=METRIC) -> dict[str, list[str]]:
    notes = ss.build_metric_notes(state, metric)
    return {group: [n["text"] for n in items] for group, items in notes.items()}


def _levels(state, group, metric=METRIC) -> list[str]:
    notes = ss.build_metric_notes(state, metric)
    return [n["level"] for n in notes[group]]


STRAT_CONFIG = {"DrainageAreaClass": {
    "display_name": "Drainage Area Class",
    "column_name": "DrainageAreaClass",
    "type": "single",
    "levels": ["Headwater", "Large"],
    "min_group_size": 5,
}}


def _data(with_class_column: bool = True) -> pd.DataFrame:
    frame = pd.DataFrame({
        "site_id": [f"S{i}" for i in range(12)],
        "chem_PTL": [float(i) for i in range(12)],
        "DrainageAreaClass": ["Headwater"] * 6 + ["Large"] * 6,
    })
    return frame if with_class_column else frame.drop(columns=["DrainageAreaClass"])


def _base_state(*, allowed=None, data=None, **overrides) -> AppState:
    """A published assessment: real data and curves, no analysis state."""
    state = AppState.fresh()
    state.data.set(_data() if data is None else data)
    metric = {
        "column_name": METRIC, "display_name": "Total phosphorus",
        "metric_family": "continuous", "higher_is_better": False,
        "include_in_summary": True,
    }
    if allowed is not None:
        metric["allowed_stratifications"] = list(allowed)
    state.metric_config.set({METRIC: metric})
    state.app_data_loaded.set(True)
    for key, value in overrides.items():
        getattr(state, key).set(value)
    return state


def _with_strat_config(**overrides) -> AppState:
    return _base_state(
        allowed=["DrainageAreaClass"], strat_config=STRAT_CONFIG, **overrides)


# --- the three not-applicable cases -------------------------------------------- #
def test_no_stratification_variables_says_so():
    state = _base_state()
    texts = _texts(state)
    assert NOT_APPLICABLE_EXPLORATORY in texts["Exploratory"]
    assert NEVER_RAN_EXPLORATORY not in texts["Exploratory"]
    assert (
        "Cross-metric analysis needs at least one stratification for this metric. "
        "None are available." in texts["Cross-Metric Analysis"]
    )
    assert (
        "Verification needs a candidate stratification. None are available for "
        "this metric." in texts["Verification"]
    )


def test_metric_with_no_enabled_stratification_says_so():
    """strat_config exists, but this metric enables none of it."""
    state = _base_state(strat_config={"DrainageAreaClass": {
        "column_name": "DrainageAreaClass", "levels": ["Headwater", "Large"],
    }})
    assert NOT_ENABLED_EXPLORATORY in _texts(state)["Exploratory"]


def test_configured_but_absent_columns_is_a_warning():
    """This one really is a misconfiguration, so it stays a warning."""
    state = _with_strat_config(data=_data(with_class_column=False))
    assert COLUMNS_ABSENT_EXPLORATORY in _texts(state)["Exploratory"]
    assert "warning" in _levels(state, "Exploratory")


def test_eligible_but_never_screened_keeps_the_original_warning():
    """The message is truthful here and only here."""
    state = _with_strat_config()
    texts = _texts(state)
    assert NEVER_RAN_EXPLORATORY in texts["Exploratory"]
    assert "Cross-metric analysis has not been run for this project." in (
        texts["Cross-Metric Analysis"]
    )


# --- levels drive the row badge ------------------------------------------------ #
def test_not_applicable_is_info_not_warning():
    """metric_summary_status flips the row badge to "Run (warnings)" on any
    warning, so a metric with nothing to analyse must not raise one."""
    state = _base_state()
    for group in ("Exploratory", "Cross-Metric Analysis", "Verification"):
        assert "warning" not in _levels(state, group), group


def test_never_ran_is_a_warning():
    state = _with_strat_config()
    assert "warning" in _levels(state, "Exploratory")


# --- discrimination helper ----------------------------------------------------- #
def test_metric_strat_eligibility_names_each_case():
    assert ss.metric_strat_eligibility(_base_state(), METRIC)[1] == ss.STRAT_NO_CONFIG

    no_allowed = _base_state(strat_config={"DrainageAreaClass": {
        "column_name": "DrainageAreaClass"}})
    assert ss.metric_strat_eligibility(no_allowed, METRIC)[1] == ss.STRAT_NONE_ALLOWED

    ok = _with_strat_config()
    allowed, reason = ss.metric_strat_eligibility(ok, METRIC)
    assert reason == ss.STRAT_OK
    assert allowed == ["DrainageAreaClass"]

    absent = _with_strat_config(data=_data(with_class_column=False))
    assert ss.metric_strat_eligibility(absent, METRIC)[1] == ss.STRAT_COLUMNS_ABSENT


def test_snapshot_signature_separates_the_cases():
    """All three leave the phase tuples empty, so without the reason key a row
    keeps the previous session's notes when one project is opened over another."""
    a = ss.build_metric_summary_snapshot_signature(_base_state(), METRIC)
    b = ss.build_metric_summary_snapshot_signature(_with_strat_config(), METRIC)
    assert a["phase1_selected"] == b["phase1_selected"] == ()
    assert a["phase2_selected"] == b["phase2_selected"] == ()
    assert a["strat_reason"] != b["strat_reason"]


def test_context_computes_usable_strats_once():
    state = _with_strat_config()
    context = ss.build_summary_snapshot_context(state)
    assert context["usable_strats"] == ["DrainageAreaClass"]
    # Passing the context must not change the answer.
    assert _texts(state) == {
        group: [n["text"] for n in items]
        for group, items in ss.build_metric_notes(state, METRIC, context=context).items()
    }


# --- guard against a malformed ranking ----------------------------------------- #
def test_ranking_without_tier_column_does_not_raise():
    """A restored frame missing the ranking columns used to raise a KeyError
    inside build_metric_notes and blank the whole summary row."""
    state = _with_strat_config()
    state.phase2_ranking.set(pd.DataFrame({"stratification": ["DrainageAreaClass"]}))
    assert ss.get_global_phase2_passed(state, METRIC) == []
    assert _texts(state)["Cross-Metric Analysis"]  # renders rather than raising
