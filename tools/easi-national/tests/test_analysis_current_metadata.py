"""Closing analysis describes the selected physical quantities and source routes."""
from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from builder import state
from builder.analysis import diagnostics, report, schemes
from builder.paths import DataRoot


def test_quantity_descriptors_follow_runtime_set_without_mutating_legacy(monkeypatch, tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    legacy_diag = dict(diagnostics.DIAG_QUANTITIES)
    legacy_report = dict(report.QUANTITY_OF)
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    assert diagnostics.diagnostic_quantities() == legacy_diag
    assert report.quantity_of() == legacy_report
    old_stamps = diagnostics.inputs(root), report.inputs(root)
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    quantities = diagnostics.diagnostic_quantities()
    assert "hyd_integrity" not in quantities and "sed_integrity" not in quantities
    assert quantities["flow_variability_cv"] == ("v__low_flow_baseflow_dynamics__flowCv", None, None)
    assert quantities["bed_agriculture"] == ("v__bed_composition_bedform_dynamics__agriculture", 1.0, 100.0)
    assert report.quantity_of()["population_support"] == "biological_model_probability"
    assert (diagnostics.inputs(root), report.inputs(root)) != old_stamps
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    assert diagnostics.diagnostic_quantities() == legacy_diag == diagnostics.DIAG_QUANTITIES
    assert report.quantity_of() == legacy_report == report.QUANTITY_OF
    assert (diagnostics.inputs(root), report.inputs(root)) == old_stamps


def _regional_table():
    return pa.table({
        "state": ["VA"] * 400,
        "v__low_flow_baseflow_dynamics__flowCv": np.linspace(1.1, 2.0, 400),
        "v__bed_composition_bedform_dynamics__agriculture": np.linspace(20.0, 80.0, 400),
        "v__population_support__prGBmmi": [0.75] * 400,
        "c__population_support": [0.75] * 200 + [0.05] * 200,
        "method_population_support": ["streamcat-prg-bmmi"] * 200 + ["streamcat-integrity-products"] * 200,
        # Stale legacy aliases must not control regional diagnostics.
        "c__low_flow_baseflow_dynamics": [1.0] * 400,
        "c__bed_composition_bedform_dynamics": [1.0] * 400,
    })


def test_regional_distributions_use_physical_scales_and_separate_biology_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    root = DataRoot(tmp_path / "data").ensure()
    rows, variance = diagnostics.distributions(_regional_table(), state.Progress(root, quiet=True))
    national = {r["quantity"]: r for r in rows if r["level"] == "national"}
    cv = national["flow_variability_cv"]
    assert cv["n"] == 400 and cv["p50"] == pytest.approx(1.55)
    assert cv["share_cap"] is None and not cv["c2_censored"]
    agriculture = national["bed_agriculture"]
    assert agriculture["p50"] == pytest.approx(50.0)
    assert agriculture["share_cap"] == 0.0 and not agriculture["c2_censored"]
    probability, fallback = national["biological_model_probability"], national["biological_integrity_fallback"]
    assert probability["n"] == fallback["n"] == 200
    assert probability["share_missing"] == fallback["share_missing"] == 0.5
    assert probability["p50"] == 0.75 and fallback["p50"] == 0.05
    assert {r["quantity"] for r in variance} == set(national)


def test_missing_biological_method_does_not_mix_unknown_source_values(monkeypatch, tmp_path):
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    root = DataRoot(tmp_path / "data").ensure()
    rows, _ = diagnostics.distributions(_regional_table().drop_columns("method_population_support"),
                                        state.Progress(root, quiet=True))
    assert not any(r["quantity"].startswith("biological_") for r in rows)


def test_route_flags_read_selected_quantity_and_scorecard_separates_fallback(monkeypatch, tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    diag = [{"quantity": q, "level": "national", "stratum": "national:national", "n": 200,
             "c2_censored": q in ("hyd_integrity", "sed_integrity"), "p50": median}
            for q, median in (("hyd_integrity", 1.0), ("sed_integrity", 1.0), ("flow_variability_cv", 1.55),
                              ("bed_agriculture", 50.0), ("biological_model_probability", 0.75),
                              ("biological_integrity_fallback", 0.05))]
    functions = ("low_flow_baseflow_dynamics", "bed_composition_bedform_dynamics", "population_support")
    pinned = [{"function": fk, "run": "S0", "pinned": False} for fk in functions]
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    assert all(r["flags"] == "" for r in report.route_rows(root, [], diag, pinned, [], "l2"))
    card = report.input_distribution_html("population_support", diag)
    assert "biological_model_probability" in card and "biological_integrity_fallback" in card
    assert "separate source routes" in card and "hyd_integrity" not in card
    monkeypatch.setenv("EASI_CRITERIA_SET", "legacy")
    assert all(r["flags"] == "censored" for r in report.route_rows(root, [], diag, pinned, [], "l2"))
    assert "biological_model_probability" not in report.input_distribution_html("population_support", diag)


def test_stability_quantity_metadata_includes_cv_and_excludes_retired_integrities():
    assert schemes.CURVE_INPUTS["low_flow_baseflow_dynamics"] == [
        ("q_cv_monthly", "c__low_flow_baseflow_dynamics", "erom-flow-variability")]
    quantities = {q for inputs in schemes.CURVE_INPUTS.values() for q, _column, _method in inputs}
    assert not {"hyd_min", "sed_min", "hyd_integrity", "sed_integrity"} & quantities
    assert "bed_composition_bedform_dynamics" not in schemes.CURVE_INPUTS
