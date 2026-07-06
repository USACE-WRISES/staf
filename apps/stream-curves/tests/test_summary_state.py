"""Integration tests for views/summary_state.py — the 4-phase engine driven
through AppState on the bundled OSAM workbook (headless, no browser)."""

from __future__ import annotations

import pandas as pd
import pytest
from shiny import reactive

from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.precheck import run_metric_precheck
from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR
from views import summary_state as ss
from views.state import AppState

FIXTURE = GOLDEN_DIR.parent / "fixtures" / "OSAM_summarydata.xlsx"


@pytest.fixture(scope="module")
def loaded_state() -> AppState:
    state = AppState.fresh()
    bundle = read_input_workbook(FIXTURE)
    cleaned, qa = clean_data(
        bundle["raw_data"], bundle["metric_config"],
        bundle["strat_config"], bundle["factor_recode_config"],
    )
    dat = derive_variables(
        cleaned, bundle["factor_recode_config"],
        bundle["predictor_config"], bundle["strat_config"],
    )
    state.metric_config.set(bundle["metric_config"])
    state.strat_config.set(bundle["strat_config"])
    state.predictor_config.set(bundle["predictor_config"])
    state.factor_recode_config.set(bundle["factor_recode_config"])
    state.data.set(dat)
    state.qa_log.set(qa)
    state.precheck_df.set(run_metric_precheck(dat, bundle["metric_config"]))
    state.data_fingerprint.set("test-fp")
    state.current_metric.set("perRiffle")
    state.app_data_loaded.set(True)
    return state


def test_eligible_metrics_and_allowed_strats(loaded_state):
    with reactive.isolate():
        mc = loaded_state.metric_config()
    eligible = ss.eligible_summary_metrics(mc)
    assert "perRiffle" in eligible
    assert "BEHI_NBS" not in eligible  # categorical
    allowed = ss.get_metric_allowed_strats(loaded_state, "perRiffle")
    assert len(allowed) > 0


def test_phase1_backfill_produces_screening_and_candidates(loaded_state):
    steps = ss.count_metric_phase1_backfill_steps(loaded_state, "perRiffle")
    assert steps == len(ss.get_metric_allowed_strats(loaded_state, "perRiffle")) + 1

    backfill = ss.build_metric_phase1_backfill(loaded_state, "perRiffle", mode="full")
    ss.commit_metric_phase1_backfill(loaded_state, "perRiffle", backfill)

    with reactive.isolate():
        l1 = loaded_state.all_layer1_results()
        cands = loaded_state.phase1_candidates()
    assert "perRiffle" in l1 and len(l1["perRiffle"]) > 0
    assert "perRiffle" in cands and len(cands["perRiffle"]) > 0
    assert set(cands["perRiffle"]["candidate_status"]).issubset(
        {"promising", "possible", "not_promising"}
    )
    # recommendation now resolves to a real strat or none
    rec = ss.get_metric_curve_strat_recommendation(loaded_state, "perRiffle")
    assert rec == "none" or rec in ss.get_metric_allowed_strats(loaded_state, "perRiffle")


def test_phase2_recompute_after_two_metrics(loaded_state):
    backfill = ss.build_metric_phase1_backfill(loaded_state, "WDR", mode="full")
    ss.commit_metric_phase1_backfill(loaded_state, "WDR", backfill)
    out = ss.recompute_phase2_shared(loaded_state)
    assert out is not None
    with reactive.isolate():
        ranking = loaded_state.phase2_ranking()
    assert ranking is not None and len(ranking) > 0
    assert set(ranking["tier"]).issubset(
        {"Broad-Use Candidate", "Metric-Specific Candidate", "Weak Candidate"}
    )


def test_phase3_backfill(loaded_state):
    backfill = ss.build_metric_phase3_backfill(loaded_state, "perRiffle", mode="full")
    ss.commit_metric_phase3_backfill(loaded_state, "perRiffle", backfill)
    display = ss.get_metric_phase3_display_state(loaded_state, "perRiffle")
    assert display is not None
    assert isinstance(display["feasibility"], pd.DataFrame)


def test_phase4_preload_and_status(loaded_state):
    # unstratified path (curve_stratification defaults via recommendation)
    ss.set_metric_curve_stratification(loaded_state, "perRiffle", "none")
    steps = ss.count_metric_phase4_preload_steps(loaded_state, "perRiffle")
    assert steps == 2
    built = ss.preload_metric_phase4_workspace(loaded_state, "perRiffle")
    assert built is True
    # cached now: preload becomes a no-op with 0 steps
    assert ss.count_metric_phase4_preload_steps(loaded_state, "perRiffle") == 0
    assert ss.preload_metric_phase4_workspace(loaded_state, "perRiffle") is False

    phase4 = ss.get_metric_phase4_display_state(loaded_state, "perRiffle")
    assert phase4["source"] in ("cache", "completed")
    assert len(phase4["curve_rows"]) == 1

    status = ss.metric_summary_status(loaded_state, "perRiffle")
    assert status["summary_label"] in ("Run", "Run (warnings)")

    snapshot = ss.build_metric_summary_snapshot(loaded_state, "perRiffle")
    assert snapshot["display_name"]
    assert snapshot["curve_strat_used"] == "none"
    assert len(snapshot["curve_rows"]) == 1


def test_phase4_stratified_recompute(loaded_state):
    allowed = ss.get_metric_allowed_strats(loaded_state, "perRiffle")
    target = "Ecoregion" if "Ecoregion" in allowed else allowed[0]
    ss.set_metric_curve_stratification(loaded_state, "perRiffle", target)
    entry = ss.recompute_metric_phase4(loaded_state, "perRiffle", artifact_mode="summary")
    assert entry.get("stratified") is True
    rows = ss.get_metric_curve_rows(loaded_state, "perRiffle")
    assert len(rows) >= 2  # one curve per stratum level
    assert set(rows["stratum"].astype(str)) >= {str(s) for s in rows["stratum"]}


def test_recompute_metric_from_summary_full_chain(loaded_state):
    events = []
    ss.recompute_metric_from_summary(
        loaded_state, "WDR", mode="summary",
        progress_cb=lambda phase, m, i, n, stage: events.append((phase, stage)),
    )
    phases = [p for p, s in events if s == "start"]
    assert phases == ["phase1", "phase2", "phase3", "phase4"]
    assert ss.metric_has_official_curve(loaded_state, "WDR")


def test_config_change_invalidates_phase4(loaded_state):
    assert ss.metric_has_phase4_cache(loaded_state, "WDR", artifact_mode="summary")
    with reactive.isolate():
        loaded_state.config_version.set((loaded_state.config_version() or 0) + 1)
    assert not ss.metric_has_phase4_cache(loaded_state, "WDR", artifact_mode="summary")
    assert ss.count_metric_phase4_preload_steps(loaded_state, "WDR") > 0
