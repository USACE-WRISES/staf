"""What a reopened agent-published assessment must show.

A completed Northeastern Highlands assessment opened in the app reported
"Exploratory screening has not been run for this metric.", "No broad-use
candidates are currently highlighted for this metric." and "Verification
diagnostics have not been run yet." on every metric, while the workflow strip
read complete. The messages were true: the agent published curves and nothing
else, and stamped every stage done regardless.

Nothing covered session_fields(), publish(), or the reopen path, which is why it
shipped. These run the real offline pipeline all the way through the session
round trip into a headless AppState and assert on what a reviewer would actually
read.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from shiny import reactive

from streamcurves import regional_agent as ra
from streamcurves import run_state, session_io
from views import summary_state as ss
from views.state import AppState

NOT_RUN_STRINGS = (
    "Exploratory screening has not been run for this metric.",
    "No broad-use candidates are currently highlighted for this metric.",
    "Verification diagnostics have not been run yet.",
)


@pytest.fixture(scope="module")
def offline_result() -> dict:
    """The real pipeline, no network: L3 58 unscreened, StreamCat skipped."""
    # This fixture tests session reopen, not the resampling diagnostics, so it
    # skips them to keep the module fast. Provenance and determinism tests keep
    # them on.
    return ra.run("58", "Northeastern Highlands", do_screen=False,
                  use_streamcat=False, diagnostics_enabled=False)


@pytest.fixture(scope="module")
def restored(offline_result) -> dict:
    """session_fields -> dump -> load -> decode, as Open does it."""
    payload = session_io.dump_session_fields(
        ra.session_fields(offline_result), session_name="Northeastern Highlands")
    round_tripped = json.loads(json.dumps(payload))
    return session_io.decode_session_fields(round_tripped)


@pytest.fixture(scope="module")
def restored_state(restored) -> AppState:
    """A headless AppState hydrated the way restore_session_into_state does."""
    state = AppState.fresh()
    state.metric_config.set(restored["metric_config"])
    state.predictor_config.set(restored["predictor_config"] or {})
    state.strat_config.set(restored["strat_config"] or {})
    state.data.set(restored["data"])
    state.data_fingerprint.set(restored["data_fingerprint"])
    state.config_version.set(restored["config_version"] or 0)
    state.phase1_candidates.set(restored["phase1_candidates"] or {})
    state.all_layer1_results.set(restored["all_layer1_results"] or {})
    state.all_layer2_results.set(restored["all_layer2_results"] or {})
    state.phase2_ranking.set(restored["phase2_ranking"])
    state.cross_metric_consistency.set(restored["cross_metric_consistency"])
    state.phase2_settings.set(restored["phase2_settings"] or {})
    state.phase3_verification.set(restored["phase3_verification"] or {})
    state.metric_phase_cache.set(restored["metric_phase_cache"] or {})
    state.curve_stratification.set(restored["curve_stratification"] or {})
    state.completed_metrics.set(restored["completed_metrics"] or {})
    state.column_functions.set(restored["column_functions"] or {})
    state.app_data_loaded.set(True)
    return state


def _screened_metrics(restored) -> list[str]:
    return sorted(restored["all_layer1_results"])


# --- the acceptance gate ----------------------------------------------------- #
def test_reopened_metrics_do_not_claim_nothing_was_run(restored_state, restored):
    """The bug, stated executably."""
    metrics = _screened_metrics(restored)
    assert len(metrics) >= 2, "fixture produced no screened metrics"

    for metric in metrics:
        notes = ss.build_metric_notes(restored_state, metric)
        texts = [n["text"] for group in notes.values() for n in group]
        for claim in NOT_RUN_STRINGS:
            assert claim not in texts, f"{metric} still reports: {claim}"


def test_reopened_metrics_still_show_unstratified_curves(restored_state, restored):
    """Advisory mode, and the trap that would have broken every curve.

    get_metric_curve_stratification falls back to the phase-1 recommendation when
    a metric has no stored choice. Once screening is persisted that fallback
    recomputes a "single" phase-4 signature, it stops matching the stored "none"
    one, and every curve reopens as "not current, recompute required".
    """
    for metric in _screened_metrics(restored):
        assert ss.get_metric_curve_stratification(restored_state, metric) == "none"
        notes = ss.build_metric_notes(restored_state, metric)
        texts = [n["text"] for n in notes["Reference Curves"]]
        assert "Stratification used for curves: None" in texts
        assert "Reference curve outputs are not current. Recompute is required." not in texts


def test_phase4_signature_still_matches_after_screening_is_persisted(restored_state, restored):
    for metric, entry in (restored["completed_metrics"] or {}).items():
        recomputed = ss.build_metric_phase4_signature(restored_state, metric)
        assert ss.phase4_signature_matches(entry["phase4_signature"], recomputed), metric


# --- what the tabs now say --------------------------------------------------- #
def test_exploratory_reports_the_stratifications_it_screened(restored_state, restored):
    metric = _screened_metrics(restored)[0]
    notes = ss.build_metric_notes(restored_state, metric)
    texts = " ".join(n["text"] for n in notes["Exploratory"])
    assert "Stratifications available for analysis:" in texts


def test_verification_reports_real_diagnostics(restored_state, restored):
    verified = [m for m, v in (restored["phase3_verification"] or {}).items() if v["finalists"]]
    assert verified, "no metric reached verification"
    notes = ss.build_metric_notes(restored_state, verified[0])
    texts = " ".join(n["text"] for n in notes["Verification"])
    assert "Verification diagnostics available for" in texts


# --- advisory mode ----------------------------------------------------------- #
def test_advisory_mode_never_stratifies_a_curve(restored):
    assert restored["curve_stratification"], "curve_stratification was not pinned"
    for metric, choice in restored["curve_stratification"].items():
        assert choice == "none", metric
    for metric, entry in (restored["completed_metrics"] or {}).items():
        assert entry["strat_decision"]["decision_type"].iloc[0] == "none", metric
        assert entry["phase4_signature"]["selected_strat"] is None, metric
    for metric, verification in (restored["phase3_verification"] or {}).items():
        assert verification["selected_strat"] == "none", metric


def test_verification_is_advisory_not_a_decision(offline_result):
    """A candidate can pass STRAT-00 and still leave the curves unstratified."""
    strat = offline_result["stratifiers"]
    ranking = strat["phase2_ranking"]
    assert ranking is not None and len(ranking) > 0
    assert (ranking["tier"] == "Broad-Use Candidate").any(), (
        "fixture produced no passing candidate, so advisory mode is untested here"
    )


# --- session shapes the app reads -------------------------------------------- #
def test_session_carries_every_diagnostic_field(restored):
    for field in ("strat_config", "all_layer1_results", "all_layer2_results",
                  "phase1_candidates", "phase2_ranking", "cross_metric_consistency",
                  "phase2_settings", "phase3_verification", "metric_phase_cache",
                  "curve_stratification", "metric_redundancy"):
        assert restored[field] is not None, f"{field} round-tripped as null"


def test_layer_frames_match_the_engine_column_contracts(restored):
    from streamcurves.effects import _COLUMNS as EFFECT_COLUMNS
    from streamcurves.screening import _RESULT_COLUMNS

    for metric, frame in restored["all_layer1_results"].items():
        assert list(frame.columns) == list(_RESULT_COLUMNS), metric
    for metric, frame in restored["all_layer2_results"].items():
        assert list(frame.columns) == list(EFFECT_COLUMNS), metric
    for metric, frame in restored["phase1_candidates"].items():
        assert set(frame["candidate_status"]).issubset(
            {"promising", "possible", "not_promising"}), metric


def test_phase3_entries_have_the_shape_the_display_reads(restored):
    for metric, entry in restored["phase3_verification"].items():
        assert isinstance(entry["finalists"], list), metric
        assert isinstance(entry["pattern_results"], dict), metric
        assert "results" in entry["pattern_results"], metric
        assert isinstance(entry["feasibility_results"], pd.DataFrame), metric


def test_phase1_artifacts_are_summary_so_the_app_rebuilds_plots(restored):
    """run_all_stratification_screening keys plot specs "{metric}_{strat}" while
    the views key them by bare strat. Persisting them wholesale renders nothing;
    summary mode makes the app rebuild them correctly on first open."""
    for metric, entry in restored["metric_phase_cache"].items():
        assert entry["phase1_artifact_mode"] == "summary", metric
        assert entry["phase1_screening"]["plot_specs"] == {}, metric


def test_strat_config_is_workbook_round_trippable(restored):
    """input_metadata is rebuilt with strat_config on both the publish and the
    restore path; a list-valued pairwise_comparisons used to raise outright."""
    from streamcurves import workbook as wb
    tables = restored["input_metadata"]
    assert tables is not None
    rebuilt = wb.build_input_bundle_from_tables(tables)["strat_config"]
    assert set(rebuilt) == set(restored["strat_config"])


# --- honest stage status ------------------------------------------------------ #
def test_stage_status_is_derived_not_stamped(offline_result):
    status = ra.stage_status_for(offline_result)
    # This fixture runs unscreened, so claiming the screening stage is done is
    # exactly the lie the old unconditional stamp told.
    assert status["candidate_screening"]["status"] != run_state.STAGE_DONE
    assert status["publish"]["status"] != run_state.STAGE_DONE
    # enrichment_build must stay done: derive_stage_status gates stages 4 to 6 on
    # it, so demoting it would show "Build a dataset first" over a full dataset.
    assert status["enrichment_build"]["status"] == run_state.STAGE_DONE


def test_stage_status_records_the_stratifier_screen(offline_result):
    screen = ra.stage_status_for(offline_result)["enrichment_build"]["stratifier_screen"]
    assert screen["mode"] == "advisory"
    assert screen["candidates_registered"] >= screen["candidates_eligible"] >= 1
    assert screen["metrics_screened"] >= 2


def test_flagged_curves_surface_as_attention(offline_result):
    status = ra.stage_status_for(offline_result)
    flagged = run_state.flagged_metrics(offline_result["curve_review"])
    expected = run_state.STAGE_ATTENTION if flagged else run_state.STAGE_DONE
    assert status["curve_review"]["status"] == expected


# --- the eligibility ledger --------------------------------------------------- #
def test_every_registered_candidate_is_accounted_for(offline_result):
    """Included or excluded, each candidate carries a reason, so "why was slope
    not screened in this region" is answerable from the run record."""
    ledger = offline_result["stratifiers"]["eligibility"]
    registry = ra.stratifiers.load_national_registry()
    assert list(ledger["stratification"]) == list(registry["candidates"])
    for _, row in ledger.iterrows():
        if row["eligible"]:
            assert row["exclusion_reason"] is None
        else:
            assert row["exclusion_reason"], row["stratification"]


def test_redundancy_is_persisted_not_discarded(restored, offline_result):
    persisted = restored["metric_redundancy"]
    assert persisted is not None
    assert len(persisted) == len(offline_result["redundancy"])
    if len(persisted):
        assert {"metric_a", "metric_b", "spearman", "pearson",
                "red01_spearman_flag"} <= set(persisted.columns)
