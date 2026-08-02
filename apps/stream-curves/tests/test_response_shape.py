"""A metric's response shape: which direction, and one-sided or two-sided.

Two fields carry this -- `higher_is_better` and `curve_form` -- and they used to be
independently settable, unvalidated, and unreachable. Three things went wrong:

  * The Metrics grid dropped `curve_form` on every save, so editing any cell turned
    every two-sided metric into a monotone higher-is-better one, silently.
  * Nothing in the app could set `curve_form` at all.
  * The manual curve editor never checked a hand-drawn shape against the metric's
    declared form, so a trapezoid drawn on a one-sided metric stored with band
    scalars describing only its rising limb.

`monotonic_linear` is NOT this setting. It has no consumers anywhere; a test at the
bottom pins that so nobody reaches for it again.
"""

from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import curves, profiler as pf
from streamcurves import workbook as wb
from streamcurves import workbook_tables as wt
from views import uihelpers as uh

# The shape the engine's own build_optimum_curve_points produces: x strictly
# ascending while the index score rises, plateaus, then falls.
TRAPEZOID = pd.DataFrame({
    "point_order": [1, 2, 3, 4, 5, 6, 7, 8],
    "metric_value": [0, 4.6, 10.75, 18, 20.175, 24, 28, 32],
    "index_score": [0, 0.3, 0.7, 1, 1, 0.7, 0.3, 0],
})

RISING = pd.DataFrame({
    "point_order": [1, 2, 3, 4],
    "metric_value": [0, 5, 10, 20],
    "index_score": [0, 0.3, 0.7, 1],
})


def _metric_config(curve_form=None, higher_is_better=True, key="m"):
    entry = {"column_name": key, "metric_family": "continuous",
             "higher_is_better": higher_is_better}
    if curve_form:
        entry["curve_form"] = curve_form
    return {key: entry}


# --- the error the user actually hit ------------------------------------------- #
def test_metric_axis_must_ascend_not_the_index_score():
    """The rejection was about the x axis, not the shape.

    A point that walks backwards in metric value is rejected; the same intent with
    the metric values kept ascending is fine. Nothing constrains index-score
    monotonicity on its own.
    """
    backwards = pd.DataFrame({
        "point_order": [1, 2, 3, 4, 5, 6],
        "metric_value": [0, 4.607142857142857, 10.75, 18, 20.175, 15],
        "index_score": [0, 0.3, 0.7, 1, 1, 0.5],
    })
    result = curves.validate_reference_curve_points(backwards, True)
    assert not result["valid"]
    assert any("non-decreasing" in e for e in result["errors"])

    assert curves.validate_reference_curve_points(
        TRAPEZOID, None, curve_form=curves.CURVE_FORM_OPTIMUM)["valid"]


# --- the declared form is now enforced on manual edits ------------------------- #
def test_two_sided_drawing_on_a_one_sided_metric_is_rejected_by_name():
    result = curves.validate_reference_curve_points(
        TRAPEZOID, True, curve_form=curves.CURVE_FORM_MONOTONE)
    assert not result["valid"]
    assert "Set Response shape to two-sided" in " ".join(result["errors"])


def test_one_sided_drawing_on_a_two_sided_metric_is_rejected_by_name():
    result = curves.validate_reference_curve_points(
        RISING, None, curve_form=curves.CURVE_FORM_OPTIMUM)
    assert not result["valid"]
    assert "Use the monotone form instead" in " ".join(result["errors"])


def test_a_plain_monotone_curve_is_unaffected():
    assert curves.validate_reference_curve_points(RISING, True)["valid"]
    falling = pd.DataFrame({
        "point_order": [1, 2, 3, 4],
        "metric_value": [0, 5, 10, 20],
        "index_score": [1, 0.7, 0.3, 0],
    })
    assert curves.validate_reference_curve_points(falling, False)["valid"]


def test_build_from_points_passes_the_declared_form():
    """The auto path already passed curve_form; this gate did not, so a manual
    curve was never checked against the metric's declared shape."""
    data = pd.DataFrame({"m": [float(i) for i in range(1, 34)]})
    with pytest.raises(ValueError, match="Response shape to two-sided"):
        curves.build_reference_curve_from_points(
            data, "m", _metric_config("monotone", True), TRAPEZOID)

    built = curves.build_reference_curve_from_points(
        data, "m", _metric_config("optimum", None), TRAPEZOID)
    assert built["curve_row"]["curve_status"].iloc[0] == "complete"


# --- the auto-built two-sided curve -------------------------------------------- #
def test_auto_optimum_curve_is_two_sided():
    data = pd.DataFrame({"m": [5.0 + i * 0.05 for i in range(60)]})
    built = curves.build_reference_curve(data, "m", _metric_config("optimum", None))
    points = built["curve_points"]
    assert list(points.columns) == ["point_order", "metric_value", "index_score"]
    assert list(points["index_score"]) == [0.0, 0.3, 0.7, 1.0, 1.0, 0.7, 0.3, 0.0]
    assert list(points["metric_value"]) == sorted(points["metric_value"])


def test_two_sided_bands_report_both_intervals():
    """A band that occupies two disjoint metric ranges cannot be described by one
    min/max pair, so the scalars go NaN and the display strings carry the truth."""
    data = pd.DataFrame({"m": [5.0 + i * 0.05 for i in range(60)]})
    row = curves.build_reference_curve(
        data, "m", _metric_config("optimum", None))["curve_row"]

    assert row["at_risk_min"].isna().iloc[0]
    assert row["at_risk_max"].isna().iloc[0]
    assert row["at_risk_ranges_display"].iloc[0].count(",") == 1
    assert row["not_functioning_ranges_display"].iloc[0].count(",") == 1
    # Functioning is a single interval spanning the plateau, so it keeps its scalars.
    assert not row["functioning_min"].isna().iloc[0]
    assert int(row["score_30_crossing_count"].iloc[0]) == 2
    assert int(row["score_70_crossing_count"].iloc[0]) == 2


def test_a_metric_with_no_direction_is_only_built_when_two_sided():
    """`higher_is_better: None` with the monotone form is the deliberate
    "direction under review" state, and the engine builds nothing for it."""
    data = pd.DataFrame({"m": [5.0 + i * 0.05 for i in range(60)]})
    two_sided = curves.build_reference_curve(data, "m", _metric_config("optimum", None))
    assert two_sided["curve_row"]["curve_status"].iloc[0] == "complete"
    assert "m" not in curves.run_all_reference_curves(data, _metric_config(None, None))


# --- the three-way control ------------------------------------------------------ #
def test_each_shape_writes_both_fields_coherently():
    assert uh.RESPONSE_SHAPE_CONFIG[uh.SHAPE_HIGHER] == (True, curves.CURVE_FORM_MONOTONE)
    assert uh.RESPONSE_SHAPE_CONFIG[uh.SHAPE_LOWER] == (False, curves.CURVE_FORM_MONOTONE)
    assert uh.RESPONSE_SHAPE_CONFIG[uh.SHAPE_OPTIMUM] == (None, curves.CURVE_FORM_OPTIMUM)
    assert set(uh.RESPONSE_SHAPE_CHOICES) == set(uh.RESPONSE_SHAPE_CONFIG)


def test_no_shape_can_produce_the_unbuildable_pair():
    """None + monotone means the metric is silently never built. The control must
    not be able to create it."""
    for higher_is_better, curve_form in uh.RESPONSE_SHAPE_CONFIG.values():
        assert not (higher_is_better is None and curve_form == curves.CURVE_FORM_MONOTONE)


@pytest.mark.parametrize("entry,expected", [
    ({"higher_is_better": True}, uh.SHAPE_HIGHER),
    ({"higher_is_better": False}, uh.SHAPE_LOWER),
    ({"higher_is_better": None, "curve_form": "optimum"}, uh.SHAPE_OPTIMUM),
    # curve_form_of lowercases, so a capitalised cell resolves rather than falling
    # back to monotone while the reader treats the blank direction as TRUE.
    ({"higher_is_better": None, "curve_form": "Optimum"}, uh.SHAPE_OPTIMUM),
    # The form wins over a stale direction left behind by an older edit.
    ({"higher_is_better": True, "curve_form": "optimum"}, uh.SHAPE_OPTIMUM),
    ({"higher_is_better": None}, None),
])
def test_response_shape_reads_back_from_config(entry, expected):
    assert uh.response_shape_of(entry) == expected


# --- the corruption bug --------------------------------------------------------- #
def _metrics_sheet(*rows) -> pd.DataFrame:
    base = {c: "" for c in wb.workbook_sheet_columns()["metrics"]}
    out = []
    for metric_key, curve_form, higher_is_better in rows:
        row = dict(base)
        row.update(metric_key=metric_key, display_name=metric_key,
                   column_name=metric_key, metric_family="continuous",
                   higher_is_better=higher_is_better, min_sample_size="10",
                   curve_form=curve_form)
        out.append(row)
    return pd.DataFrame(out)


@pytest.fixture
def two_sided_tables() -> dict:
    tables = {
        "data": pd.DataFrame({"site_id": ["a", "b"], "chem_PH": [7.0, 7.2],
                              "chem_PTL": [1.0, 2.0]}),
        "metrics": _metrics_sheet(("chem_PH", "optimum", ""),
                                 ("chem_PTL", "monotone", "FALSE")),
        "metric_predictors": pd.DataFrame(),
        "metric_stratifications": pd.DataFrame(),
        "predictors": pd.DataFrame(),
        "stratifications": pd.DataFrame(),
        "strat_groups": pd.DataFrame(),
        "factor_recodes": pd.DataFrame(),
    }
    return wb.normalize_workbook_tables(tables)


def _config(tables) -> dict:
    return wb.build_metric_config_from_workbook(
        tables["metrics"], tables["metric_predictors"], tables["metric_stratifications"])


def _assert_two_sided_survived(tables, key="chem_PH"):
    entry = _config(tables)[key]
    assert entry.get("curve_form") == "optimum", "curve_form was dropped"
    assert entry.get("higher_is_better") is None, (
        "a blank direction with no curve_form reads as TRUE, so losing curve_form "
        "silently turns a two-sided metric monotone-increasing"
    )


def test_editing_any_metrics_cell_preserves_curve_form(two_sided_tables):
    editor = wt.editor_df_for_tab(two_sided_tables, "metrics")
    assert "curve_form" not in editor.columns, "the grid is not the editor for this field"
    _assert_two_sided_survived(
        wt.apply_editor_df_to_tables(two_sided_tables, "metrics", editor))


def test_adding_a_metric_row_preserves_curve_form(two_sided_tables):
    _assert_two_sided_survived(wt.add_metric_row_to_tables(two_sided_tables))


def test_deleting_a_metric_row_preserves_curve_form(two_sided_tables):
    after = wt.delete_rows_from_tables(two_sided_tables, "metrics", [2])
    assert "chem_PTL" not in _config(after)
    _assert_two_sided_survived(after)


def test_unchecking_a_metric_column_preserves_curve_form(two_sided_tables):
    assignments = pd.DataFrame({
        "column": ["chem_PH", "chem_PTL"],
        "is_metric": [True, False],
        "is_predictor": [False, False],
        "is_stratifier": [False, False],
    })
    _assert_two_sided_survived(
        pf.reconcile_role_membership(two_sided_tables, assignments))


# --- the red herring ------------------------------------------------------------ #
def test_monotonic_linear_controls_nothing():
    """It is a workbook round-trip field with no consumer, so reaching for it to
    change a curve's shape does nothing at all."""
    import pathlib

    root = pathlib.Path(wb.__file__).resolve().parent
    engine = ("curves.py", "models.py", "decision.py", "screening.py",
              "diagnostics.py", "derive.py", "effects.py", "stability.py",
              "feasibility.py")
    for name in engine:
        assert "monotonic_linear" not in (root / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize("entry,expected", [
    ({"higher_is_better": True}, "Higher is better"),
    ({"higher_is_better": False}, "Lower is better"),
    ({"higher_is_better": None, "curve_form": "optimum"}, "Two-sided (best mid-range)"),
    ({"higher_is_better": None}, "Under review"),
])
def test_direction_labels_never_misdescribe_a_two_sided_curve(entry, expected):
    """`bool(higher_is_better)` coerced a deliberate None to False, so the Curve
    Details panel labelled a trapezoid "Lower is better" right beside the plot."""
    assert uh.response_shape_label(entry) == expected
