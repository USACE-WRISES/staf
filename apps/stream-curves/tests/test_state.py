"""Tests for views/state.py — AppState + the phase_tracker.R port."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from shiny import reactive

from views import state as st
from views.state import AppState


@pytest.fixture()
def state() -> AppState:
    return AppState.fresh()


def _get(state, name):
    with reactive.isolate():
        return state.get(name)


def test_fresh_state_matches_startup_configs(state):
    with reactive.isolate():
        assert state.metric_config() == {}
        assert state.strat_config() == {}
        assert state.output_config()  # output_registry.yaml loads eagerly
        assert state.startup_output_config() == state.output_config()
        assert state.startup_output_config() is not state.output_config()
        assert state.current_metric() == "perRiffle"
        assert state.config_version() == 0
        assert state.analysis_tab_status() == {
            "exploratory": "pending",
            "cross_metric": "pending",
            "verification": "pending",
            "reference_curves": "pending",
        }


def test_field_names_cover_r_rv_inventory(state):
    names = state.field_names()
    # spot-check the load-bearing fields from app.R:144-242
    for f in [
        "data", "metric_phase_cache", "workspace_modal_nonce", "decision_log",
        "discipline_function_mapping", "custom_groupings", "import_inject_nonce",
        "analysis_tab_preload_completed_nonce", "stratum_results",
    ]:
        assert f in names
    assert len(names) >= 60


def test_save_restore_metric_phase_state(state):
    screening = pd.DataFrame({"metric": ["m1"], "p_value": [0.01]})
    state.phase1_screening.set(screening)
    state.reference_curve.set(pd.DataFrame({"point_order": [1, 2]}))
    state.config_version.set(3)
    state.stratum_results.set({"m1": {"A": {"reference_curve": None}}})

    st.save_metric_phase_state(state, "m1")

    with reactive.isolate():
        cache = state.metric_phase_cache()
    assert "m1" in cache
    assert cache["m1"]["config_version"] == 3
    assert cache["m1"]["phase1_screening"] is screening
    assert cache["m1"]["stratum_results"] == {"A": {"reference_curve": None}}

    # switch away: restore of an uncached metric nulls the phase fields
    st.restore_metric_phase_state(state, "m2")
    assert _get(state, "phase1_screening") is None
    assert _get(state, "reference_curve") is None

    # switch back: everything returns
    st.restore_metric_phase_state(state, "m1")
    assert _get(state, "phase1_screening") is screening
    assert _get(state, "current_stratum_level") is None


def test_save_metric_phase_state_ignores_blank_metric(state):
    st.save_metric_phase_state(state, None)
    st.save_metric_phase_state(state, "")
    with reactive.isolate():
        assert state.metric_phase_cache() == {}


def test_stratum_state_roundtrip(state):
    curve = pd.DataFrame({"point_order": [1], "metric_value": [1.0], "index_score": [0.5]})
    state.reference_curve.set(curve)
    st.save_stratum_state(state, "m1", "Level A")

    state.reference_curve.set(None)
    st.restore_stratum_state(state, "m1", "Level A")
    assert _get(state, "reference_curve") is curve

    st.restore_stratum_state(state, "m1", "missing-level")
    assert _get(state, "reference_curve") is None


def test_reset_all_analysis_cleans_custom_groupings(state):
    state.data.set(pd.DataFrame({"x": [1], "cg_col": [2]}))
    state.metric_config.set({"m1": {"allowed_stratifications": ["cg1", "Ecoregion"]}})
    state.strat_config.set({"cg1": {"column_name": "cg_col"}, "Ecoregion": {}})
    state.custom_groupings.set({"cg1": {"column_name": "cg_col"}})
    state.phase1_screening.set(pd.DataFrame({"a": [1]}))
    state.metric_phase_cache.set({"m1": {"config_version": 0}})

    st.reset_all_analysis(state)

    with reactive.isolate():
        assert list(state.data().columns) == ["x"]
        assert "cg1" not in state.strat_config()
        assert state.metric_config()["m1"]["allowed_stratifications"] == ["Ecoregion"]
        assert state.custom_groupings() == {}
        assert state.phase1_screening() is None
        assert state.metric_phase_cache() == {}
        assert isinstance(state.decision_log(), pd.DataFrame)
        assert state.phase2_settings()["sig_threshold"] == 0.05


def test_reset_app_to_startup(state):
    state.data.set(pd.DataFrame({"x": [1]}))
    state.app_data_loaded.set(True)
    state.current_metric.set("other")
    state.metric_config.set({"m": {}})
    with reactive.isolate():
        nonce0 = state.app_reset_nonce()

    st.reset_app_to_startup(state)

    with reactive.isolate():
        assert state.data() is None
        assert state.app_data_loaded() is False
        assert state.current_metric() == "perRiffle"
        assert state.metric_config() == {}
        assert state.app_reset_nonce() == nonce0 + 1


def test_launch_workspace_modal_sets_machine_state(state):
    rid = st.launch_workspace_modal(state, "phase1", metric="m1")
    with reactive.isolate():
        assert rid == 1
        assert state.workspace_modal_type() == "phase1"
        assert state.workspace_modal_metric() == "m1"
        assert state.workspace_modal_stage() == "loading"
        assert state.workspace_modal_nonce() == 1

    rid2 = st.launch_workspace_modal(state, "phase2")
    assert rid2 == 2


def test_launch_analysis_resets_tab_state(state):
    st.set_analysis_tab_status  # noqa: B018 — imported name sanity
    rid = st.launch_workspace_modal(state, "analysis", metric="m1")
    with reactive.isolate():
        assert state.analysis_tab_request_id() == rid
        assert state.analysis_tab_status() == st.empty_analysis_tab_status("pending")


def test_analysis_tab_status_machinery(state):
    rid = st.reset_analysis_tab_state(state, request_id=7)
    assert st.set_analysis_tab_status(state, "exploratory", "ready") is True
    assert st.get_analysis_tab_status(state, "exploratory") == "ready"
    # same status again -> no-op
    assert st.set_analysis_tab_status(state, "exploratory", "ready") is False
    # unknown tab -> no-op
    assert st.set_analysis_tab_status(state, "nope", "ready") is False
    # stale request id -> no-op
    assert st.set_analysis_tab_status(state, "cross_metric", "ready", request_id=99) is False

    assert st.request_analysis_tab_preload(state, "verification") is True
    with reactive.isolate():
        assert state.analysis_tab_preload_tab() == "verification"

    assert st.complete_analysis_tab_preload(state, "verification", status="ready") is True
    with reactive.isolate():
        assert state.analysis_tab_preload_completed_tab() == "verification"
        assert state.analysis_tab_preload_completed_status() == "ready"
    assert st.get_analysis_tab_status(state, "verification") == "ready"


def test_workspace_scope_is_active(state):
    state.workspace_modal_type.set("phase2")
    assert st.workspace_scope_is_active(
        state, "standalone", standalone_modal_type="phase2", isolate_state=True
    )
    assert not st.workspace_scope_is_active(state, "analysis", isolate_state=True)
    state.workspace_modal_type.set("analysis")
    assert st.workspace_scope_is_active(state, "analysis", isolate_state=True)


def test_config_version_warning_on_restore(state, caplog):
    state.config_version.set(1)
    st.save_metric_phase_state(state, "m1")
    state.config_version.set(2)
    with caplog.at_level("WARNING", logger="streamcurves"):
        st.restore_metric_phase_state(state, "m1")
    assert any("Config changed" in r.message for r in caplog.records)
