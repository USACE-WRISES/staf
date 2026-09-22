"""A build that applied a registry class split must reopen as stratified and current.

Rule STRAT-10 (methodology 0.12) turns a class split the national scale analysis
adopted into real curves wherever the region's own pool supports two classes.
The workspace decides whether a stored curve is current by recomputing its
phase-4 signature from the session's ``curve_stratification`` entry. If the two
disagree the metric reopens as "not current, recompute required" and its curves
do not render, which is exactly how every curve of an assessment was blanked
once before (see test_agent_reopen_contract.py).

The real pressure-screen pipeline, offline, with a small registry passed in so
the test does not depend on the committed registry's contents.
"""
from __future__ import annotations

import json

import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import deep_export, nrsa_dataset
from streamcurves import pressure_evidence as pe
from streamcurves import reference_screen as rscreen
from streamcurves import regional_agent as ra
from streamcurves import run_state, session_io
from views import summary_state as ss
from views.state import AppState

pytestmark = pytest.mark.skipif(
    not rscreen.station_screen_available()
    or nrsa_dataset.default_build_dataset_id() != nrsa_dataset.MULTI_CYCLE_DATASET_ID,
    reason="needs the committed station screen table and the pooled NRSA archive")

REGISTRY = {"version": 1, "analysis_version": "test",
            "metrics": {
                "phab_XEMBED": {"status": "decided", "supported_level": "l3",
                                "split": "NhdSlopeClass", "split_status": "accept"},
                "phab_XBKF_H": {"status": "decided", "supported_level": "l3",
                                "split": "NhdDrainageAreaClass", "split_status": "accept"}}}
SPLIT = {"phab_XEMBED": "NhdSlopeClass", "phab_XBKF_H": "NhdDrainageAreaClass"}


@pytest.fixture(scope="module")
def result() -> dict:
    max_order, protocols = nrsa_dataset.governed_frame("wadeable")
    # A fresh build, as the curves it splits must be fitted here: NEH v9 carries
    # both forward (methodology 0.14), and carry-forward is tested on its own.
    evidence = ra.run_evidence(
        "58", "Northeastern Highlands", reference_method=run_state.REFERENCE_METHOD_PRESSURE,
        nrsa_dataset_id=nrsa_dataset.MULTI_CYCLE_DATASET_ID,
        nrsa_max_stream_order=max_order, nrsa_protocols=protocols,
        diagnostics_enabled=False, scale_registry=REGISTRY, carry=False)
    applied = {mk for mk, rec in evidence["strata_applied"].items() if rec["applied"]}
    if applied != set(SPLIT):
        pytest.skip(f"the pool supports a split for {sorted(applied)} only")
    return ra.assemble(evidence)


@pytest.fixture(scope="module")
def restored(result) -> dict:
    payload = session_io.dump_session_fields(ra.session_fields(result),
                                             session_name="Northeastern Highlands")
    return session_io.decode_session_fields(json.loads(json.dumps(payload)))


@pytest.fixture(scope="module")
def state(restored) -> AppState:
    st = AppState.fresh()
    st.metric_config.set(restored["metric_config"])
    st.predictor_config.set(restored["predictor_config"] or {})
    st.strat_config.set(restored["strat_config"] or {})
    st.data.set(restored["data"])
    st.data_fingerprint.set(restored["data_fingerprint"])
    st.config_version.set(restored["config_version"] or 0)
    st.phase1_candidates.set(restored["phase1_candidates"] or {})
    st.all_layer1_results.set(restored["all_layer1_results"] or {})
    st.metric_phase_cache.set(restored["metric_phase_cache"] or {})
    st.curve_stratification.set(restored["curve_stratification"] or {})
    st.completed_metrics.set(restored["completed_metrics"] or {})
    st.reference_build.set(restored["reference_build"])
    st.app_data_loaded.set(True)
    return st


def test_the_session_names_the_stratifier_of_each_split_metric(restored):
    choice = restored["curve_stratification"]
    for mk, key in SPLIT.items():
        assert choice[mk] == key
    unsplit = [mk for mk in choice if mk not in SPLIT]
    assert unsplit and all(choice[mk] == "none" for mk in unsplit)


def test_the_stratifier_is_configured_on_a_column_the_data_carries(restored):
    data, config = restored["data"], restored["strat_config"]
    for mk, key in SPLIT.items():
        entry = config[key]
        assert entry["column_name"] in data.columns
        assert set(entry["levels"]) <= set(data[entry["column_name"]].dropna().unique())
        assert key in restored["metric_config"][mk]["allowed_stratifications"]


def test_the_workspace_allows_the_stratifier_for_the_split_metric(state):
    with reactive.isolate():
        data, strat_config = state.data(), state.strat_config()
    for mk, key in SPLIT.items():
        assert key in ss.get_metric_allowed_strats(state, mk)
        values = ss.get_stratification_values(data, key, strat_config)
        assert values.notna().sum() > 0


def test_the_cached_decision_agrees_with_the_stored_one(restored):
    """The workspace reads the cached decision before curve_stratification, and
    the advisory screen cached "none" for every metric."""
    for mk, key in SPLIT.items():
        cached = restored["metric_phase_cache"][mk]["strat_decision_user"]
        assert cached["decision_type"].iloc[0] == "single"
        assert cached["selected_strat"].iloc[0] == key


def test_every_stored_signature_is_the_one_the_workspace_recomputes(state, restored):
    for mk, entry in restored["completed_metrics"].items():
        recomputed = ss.build_metric_phase4_signature(state, mk)
        assert ss.phase4_signature_matches(entry["phase4_signature"], recomputed), mk
        assert ss.metric_phase4_entry_is_current(entry, recomputed), mk
    for mk, key in SPLIT.items():
        sig = restored["completed_metrics"][mk]["phase4_signature"]
        assert sig["decision_type"] == "single" and sig["selected_strat"] == key
        assert ss.get_metric_curve_stratification(state, mk) == key


def test_the_split_metric_carries_the_pooled_curve_and_its_class_curves(restored, result):
    for mk in SPLIT:
        rows = restored["completed_metrics"][mk]["phase4_curve_rows"]
        strata = [str(s) for s in rows["stratum"]]
        assert strata[0] == ""                                   # pooled first
        assert strata[1:] == result["strata_applied"][mk]["supported"]
        assert len(strata) >= 3
        floor = 15
        assert all(int(n) >= floor for n in rows["n_reference"].iloc[1:])
        assert int(rows["n_reference"].iloc[0]) >= int(rows["n_reference"].iloc[1:].max())


def test_a_bundle_rebuilt_from_the_reopened_session_equals_the_published_one(restored, result):
    rows = deep_export.deep_collect_curve_rows(restored["completed_metrics"])
    rows = {mk: r for mk, r in rows.items() if mk in result["intended_metrics"]}
    meta = {"assessmentId": result["assessment_id"],
            "assessmentName": result["meta"]["assessmentName"],
            "sourceCitation": result["meta"]["sourceCitation"], "region": result["region"]}
    rows, mapping, config = pe.apply_reference_build(
        restored["reference_build"], rows, restored["discipline_function_mapping"],
        restored["metric_config"], meta)
    rebuilt = deep_export.build_deep_assessment_bundle(rows, mapping, config, meta)

    def entries(bundle):
        return {m["metricId"]: m for blk in bundle["metricsByFunction"] for m in blk["metrics"]}
    got, want = entries(rebuilt), entries(result["bundle"])
    assert set(got) == set(want)
    for mk in SPLIT:
        mid = "spring-" + deep_export.deep_slug(mk)
        assert got[mid]["curveLayers"] == want[mid]["curveLayers"]
        assert got[mid]["activeStratum"] == want[mid]["activeStratum"] == ""
        assert got[mid]["stratifier"] == want[mid]["stratifier"]
        assert got[mid]["curve"]["points"] == want[mid]["curve"]["points"]
        layer_keys = [layer["stratum"] for layer in want[mid]["curveLayers"][1:]]
        with_curve = [c["key"] for c in want[mid]["stratifier"]["classes"] if c["hasCurve"]]
        assert sorted(layer_keys) == sorted(with_curve)
    unsplit = "spring-chem-cond"
    assert "curveLayers" not in got[unsplit] and "curveLayers" not in want[unsplit]
