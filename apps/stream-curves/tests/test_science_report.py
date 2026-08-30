"""Tests for the Science Support Document HTML builder (M6 part 3) and the
summary-export note/label helpers. The full build_summary_export_context is
exercised end-to-end in the summary_export browser verification.
"""

from __future__ import annotations

import io

import pandas as pd
from plotnine import aes, geom_point, ggplot

from streamcurves.science_report import build_science_support_html
from views.summary_state import (
    flatten_summary_note_text,
    metric_curve_summary_label,
    render_summary_plot_png,
)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    (ggplot(pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 3]}), aes("x", "y")) + geom_point()).save(
        buf, format="png", width=3, height=2, dpi=72, verbose=False
    )
    return buf.getvalue()


def _mock_context() -> dict:
    return {
        "session_meta": {
            "generated_at": "2026-07-05 12:00:00",
            "metric_count": 2,
            "complete_metrics": 1,
            "review_metrics": 1,
        },
        "metrics": {
            "perRiffle": {  # Geomorphology / Bedform Diversity (covered)
                "display_name": "Percent Riffle",
                "n_obs": 39,
                "status_label": "Complete",
                "selected_curve_stratification_label": "Ecoregion",
                "warning_summary": None,
                "threshold_table": pd.DataFrame(
                    {"stratum": ["all"], "0.30": [12.0], "0.70": [30.0]}
                ),
                "plot_png": _png_bytes,
            },
            "WDR": {  # Hydraulics / Width/Depth Ratio (covered)
                "display_name": "Width/Depth Ratio",
                "n_obs": 30,
                "status_label": "Needs review",
                "selected_curve_stratification_label": "None",
                "warning_summary": "Phase 3: multiple threshold crossings",
                "threshold_table": pd.DataFrame(),
                "plot_png": None,
            },
        },
    }


# --------------------------------------------------------------------------- #
# science report
# --------------------------------------------------------------------------- #


def test_science_report_structure():
    html = build_science_support_html(_mock_context())
    assert html.startswith("<!DOCTYPE html>")
    assert "Science Support Document" in html
    # static chapters present
    for chapter in [
        "Background and Introduction",
        "Reference Curve Development Methodology",
        "Limitations and Data Gaps",
        "References",
    ]:
        assert chapter in html
    # all five SFPF categories always render
    for cat in ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]:
        assert f">{cat}</h1>" in html
    # covered metrics + their function-based parameters render
    assert "Percent Riffle" in html
    assert "Width/Depth Ratio" in html
    assert "Bedform Diversity" in html
    # the session snapshot surfaces
    assert "2026-07-05 12:00:00" in html
    # a TOC is built
    assert 'id="TOC"' in html


def test_science_report_embeds_plot_data_uri():
    html = build_science_support_html(_mock_context())
    # perRiffle has a plot_png callable -> embedded as a base64 data URI
    assert "data:image/png;base64," in html


def test_science_report_renders_threshold_table_and_missing_note():
    html = build_science_support_html(_mock_context())
    assert '<table class="sqt-table">' in html  # perRiffle threshold table
    # WDR has an empty threshold_table -> the "not available" note
    assert "Threshold table not available" in html


def test_science_report_empty_context():
    html = build_science_support_html({"session_meta": {}, "metrics": {}})
    assert html.startswith("<!DOCTYPE html>")
    # categories with no covered metrics fall back to notes, never crash
    assert "Physicochemistry" in html


def test_science_report_states_the_predictor_source():
    # StreamCat default renders by name.
    ctx = _mock_context()
    ctx["session_meta"]["predictor_source"] = "streamcat"
    html = build_science_support_html(ctx)
    assert "Predictor source:" in html
    assert "EPA StreamCat" in html
    # An engine-built session names the engine and its version stamp.
    ctx = _mock_context()
    ctx["session_meta"]["predictor_source"] = "site-engine v0.1.0"
    html = build_science_support_html(ctx)
    assert "STAF site engine (site-engine v0.1.0), exact-watershed values" in html
    # A context without the stamp (older callers) renders no provenance row.
    html = build_science_support_html(_mock_context())
    assert "Predictor source:" not in html


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_flatten_summary_note_text_levels():
    notes = {
        "Phase 1": [{"level": "info", "text": "ok"}, {"level": "warning", "text": "small n"}],
        "Phase 3": [{"level": "warning", "text": "crossings"}],
    }
    warnings = flatten_summary_note_text(notes, level="warning")
    assert warnings == ["Phase 1: small n", "Phase 3: crossings"]
    infos = flatten_summary_note_text(notes, level="info")
    assert infos == ["Phase 1: ok"]
    assert flatten_summary_note_text(None) == []


def test_metric_curve_summary_label_variants():
    single = {"curve_rows": pd.DataFrame({"q25": [12.0], "q75": [30.0], "stratum": ["all"]})}
    assert metric_curve_summary_label(single) == "12.00 - 30.00"
    multi = {"curve_rows": pd.DataFrame({"stratum": ["a", "b", "c"]})}
    assert metric_curve_summary_label(multi) == "3 stratified curves"
    assert metric_curve_summary_label({"curve_rows": pd.DataFrame()}) == "Not available"


def test_render_summary_plot_png_none():
    assert render_summary_plot_png(None) is None
    assert len(render_summary_plot_png(
        ggplot(pd.DataFrame({"x": [1], "y": [1]}), aes("x", "y")) + geom_point()
    )) > 1000
