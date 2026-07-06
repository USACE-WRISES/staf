"""Consolidated golden-parity tests against the R pipeline fixtures.

Covers the modules whose dedicated golden tests are not embedded in their own
test files: derive (end-to-end), precheck, stability, feasibility, decision,
diagnostics, cross_metric, regional, profiler. Each test skips when its
fixture is absent (regenerate with scripts/export_golden.R).

The pipeline frame is built through the PYTHON stack (workbook -> clean ->
derive) so dtypes (factors) match the live app, then compared against R's
02_derived before being fed onward — any upstream drift fails here first.

NOTE: compute_strat_consistency (05d) has no golden fixture yet — add a
section to scripts/export_golden.R if consistency parity coverage is wanted.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves import cleaning, cross_metric, decision, derive, diagnostics
from streamcurves import feasibility, precheck, profiler, regional, stability, workbook
from tests.golden_io import (
    assert_frame_matches,
    has_golden,
    load_golden_df,
    load_golden_json,
)

FIXTURE_XLSX = Path(__file__).resolve().parent / "fixtures" / "OSAM_summarydata.xlsx"

_NEEDED = ("01_bundle_meta", "02_derived")


def _skip_unless(*names: str) -> None:
    missing = [n for n in names if not has_golden(n)]
    if missing:
        pytest.skip(f"golden fixtures missing: {missing} (run scripts/export_golden.R)")


@pytest.fixture(scope="module")
def pipeline():
    """Python-stack bundle + cleaned/derived frame (the live-app data path)."""
    _skip_unless(*_NEEDED)
    bundle = workbook.read_input_workbook(FIXTURE_XLSX)
    cleaned, qa_log = cleaning.clean_data(
        bundle["raw_data"],
        bundle["metric_config"],
        bundle["strat_config"],
        bundle["factor_recode_config"],
    )
    dat = derive.derive_variables(
        cleaned,
        bundle["factor_recode_config"],
        bundle["predictor_config"],
        bundle["strat_config"],
    )
    return {"bundle": bundle, "dat": dat, "qa_log": qa_log}


def test_derived_frame_matches_r(pipeline):
    golden = load_golden_df("02_derived")
    dat = pipeline["dat"]
    assert list(dat.columns) == list(golden.columns)
    assert_frame_matches(dat, golden, rtol=1e-9, atol=1e-12)


def test_precheck_golden(pipeline):
    _skip_unless("03_precheck")
    got = precheck.run_metric_precheck(pipeline["dat"], pipeline["bundle"]["metric_config"])
    golden = load_golden_df("03_precheck")
    assert_frame_matches(got, golden, keys=["metric"], rtol=1e-9, atol=1e-12)


def test_stability_golden(pipeline):
    _skip_unless("05c_stability")
    bundle = pipeline["bundle"]
    mc = bundle["metric_config"]
    pc = bundle["predictor_config"]
    frames = []
    for m, entry in mc.items():
        preds = entry.get("allowed_predictors") or list(pc.keys())
        if not preds:
            continue
        try:
            out = stability.assess_pattern_stability(
                pipeline["dat"], m, None, preds, mc, bundle["strat_config"], pc
            )
        except (ValueError, TypeError):
            continue  # R export loop tryCatch parity
        if isinstance(out, pd.DataFrame) and len(out):
            frames.append(out)
    got = pd.concat(frames, ignore_index=True)
    golden = load_golden_df("05c_stability")
    keys = [k for k in ("metric", "predictor") if k in golden.columns]
    assert len(got) == len(golden)
    # LOESS fitted values agree ~1e-8 via skmisc; give r^2 a 1e-6 cushion.
    assert_frame_matches(got, golden, keys=keys, rtol=1e-6, atol=1e-6)


def test_feasibility_golden(pipeline):
    _skip_unless("05e_feasibility")
    bundle = pipeline["bundle"]
    got = feasibility.assess_feasibility(
        pipeline["dat"], list(bundle["strat_config"].keys()), bundle["strat_config"]
    )
    golden = load_golden_df("05e_feasibility")
    assert_frame_matches(got, golden, keys=["stratification"], rtol=1e-9, atol=1e-12)


def test_decision_golden_from_golden_inputs(pipeline):
    """Isolates the decision logic: feeds R's own screening/effects/feasibility
    outputs and requires R's decisions back."""
    _skip_unless("04_screening", "04_pairwise", "05_effects", "05e_feasibility", "06_decisions")
    bundle = pipeline["bundle"]
    got = decision.make_stratification_decisions(
        load_golden_df("04_screening"),
        load_golden_df("04_pairwise"),
        bundle["metric_config"],
        bundle["strat_config"],
        effect_sizes=load_golden_df("05_effects"),
        feasibility=load_golden_df("05e_feasibility"),
    )
    golden = load_golden_df("06_decisions")
    assert_frame_matches(got, golden, keys=["metric"], rtol=1e-9, atol=1e-12)


def test_diagnostics_golden(pipeline):
    _skip_unless("06_decisions", "08_selection", "09_diagnostics")
    bundle = pipeline["bundle"]
    out = diagnostics.run_all_diagnostics(
        pipeline["dat"],
        load_golden_df("08_selection"),
        load_golden_df("06_decisions"),
        bundle["metric_config"],
    )
    got = out["summary_df"] if isinstance(out, dict) else out
    golden = load_golden_df("09_diagnostics")
    # Shapiro p-values agree ~1e-5 (log-transform approximations); statuses exact.
    assert_frame_matches(got, golden, keys=["metric"], rtol=1e-4, atol=1e-6)


def test_cross_metric_golden(pipeline):
    _skip_unless("11_crossmetric", "11_cor_matrix")
    out = cross_metric.run_cross_metric_analysis(
        pipeline["dat"], pipeline["bundle"]["metric_config"]
    )
    golden = load_golden_df("11_crossmetric")
    keys = [k for k in ("metric_1", "metric_2", "metric_a", "metric_b") if k in golden.columns]
    assert_frame_matches(out["results"], golden, keys=keys or None, rtol=1e-9, atol=1e-12)

    g_cor = load_golden_df("11_cor_matrix")
    p_cor = out["cor_matrix"]
    assert p_cor is not None
    # jsonlite writes the R matrix's rownames as a "_row" column.
    if "_row" in g_cor.columns:
        assert list(g_cor["_row"]) == list(g_cor.columns.drop("_row"))
        g_cor = g_cor.drop(columns="_row")
    assert list(p_cor.columns) == list(g_cor.columns)
    np.testing.assert_allclose(
        p_cor.to_numpy(dtype=float),
        g_cor.to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-12,
        err_msg="cor_matrix",
    )


def test_regional_golden(pipeline):
    _skip_unless("12_regional")
    out = regional.run_regional_curves(pipeline["dat"], pipeline["bundle"]["metric_config"])
    got = out["results"] if isinstance(out, dict) else out
    golden = load_golden_df("12_regional")
    keys = [k for k in ("response", "response_var", "stratification", "group", "stratum_level")
            if k in golden.columns]
    assert_frame_matches(got, golden, keys=keys or None, rtol=1e-9, atol=1e-12)


def test_profiler_golden():
    _skip_unless("30_profiler", "30_sanitize_keys")
    raw = pd.read_excel(FIXTURE_XLSX, sheet_name="data")
    got = profiler.profile_columns(raw)
    golden = load_golden_df("30_profiler")
    assert_frame_matches(got, golden, keys=["column"] if "column" in golden.columns else None,
                         rtol=1e-9, atol=1e-9)

    sk = load_golden_json("30_sanitize_keys")
    assert profiler.sanitize_keys(sk["input"]) == sk["output"]
