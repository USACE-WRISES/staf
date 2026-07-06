"""Tests for the Regional Curves view layer (M6 part 1):
- build_regional_boxplot_spec (domain) — group labels + KW p-value + <3 guard
- the plotnine renderers build and save real PNGs from the OSAM fixture
- build_model_summary_display — the 7-column dom='t' table shape
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.regional import build_regional_boxplot_spec, fit_regional_curve
from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR
from views.regional_curve import (
    build_model_summary_display,
    render_regional_boxplot,
    render_regional_curve_plot,
)

FIXTURE = GOLDEN_DIR.parent / "fixtures" / "OSAM_summarydata.xlsx"


@pytest.fixture(scope="module")
def osam():
    bundle = read_input_workbook(FIXTURE)
    cleaned, _ = clean_data(
        bundle["raw_data"], bundle["metric_config"],
        bundle["strat_config"], bundle["factor_recode_config"],
    )
    dat = derive_variables(
        cleaned, bundle["factor_recode_config"],
        bundle["predictor_config"], bundle["strat_config"],
    )
    return dat, bundle


def _assert_saves(fig) -> int:
    assert fig is not None
    buf = io.BytesIO()
    fig.save(buf, width=8, height=6, dpi=100, verbose=False)
    assert len(buf.getvalue()) > 5000  # a real PNG, not a stub
    return len(buf.getvalue())


# --------------------------------------------------------------------------- #
# build_regional_boxplot_spec
# --------------------------------------------------------------------------- #


def test_boxplot_spec_builds_with_group_labels_and_kw(osam):
    dat, _ = osam
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    assert spec is not None
    assert spec["type"] == "regional_boxplot"
    assert spec["y_col"] == "BW_ft"
    # group labels carry the per-level n counts
    labels = list(spec["data"]["group_label"].cat.categories)
    assert labels == spec["x_levels"]
    assert all("(n=" in lab for lab in labels)
    # KW omnibus p-value present + finite for a real multi-group column
    assert spec["kw_p"] is not None and np.isfinite(spec["kw_p"])
    assert spec["kw_label"].startswith("Kruskal-Wallis, p =")


def test_boxplot_spec_returns_none_when_too_few_rows(osam):
    dat, _ = osam
    tiny = dat[["BW_ft", "Ecoregion"]].dropna().head(2)
    assert build_regional_boxplot_spec(
        tiny, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    ) is None


def test_boxplot_spec_returns_none_for_missing_column(osam):
    dat, _ = osam
    assert build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "NotAColumn", "Nope"
    ) is None


def test_boxplot_spec_levels_are_alphabetical(osam):
    dat, _ = osam
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    # R factor() default orders levels alphabetically; labels prefix with level.
    raw_levels = [lab.split("\n")[0] for lab in spec["x_levels"]]
    assert raw_levels == sorted(raw_levels)


def test_boxplot_spec_brackets_empty_by_default(osam):
    dat, _ = osam
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    assert spec["brackets"] == []


def test_boxplot_spec_pairwise_brackets(osam):
    dat, _ = osam
    base = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    raw_levels = [lab.split("\n")[0] for lab in base["x_levels"]]
    assert len(raw_levels) >= 2
    pairs = [[raw_levels[0], raw_levels[1]], [raw_levels[0], "not-a-level"]]
    if len(raw_levels) >= 3:
        pairs.insert(1, [raw_levels[1], raw_levels[2]])
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion",
        pairwise_comparisons=pairs,
    )
    brackets = spec["brackets"]
    # the pair naming an absent level is dropped (R Filter(all(!is.na)))
    assert len(brackets) == len(pairs) - 1

    b0 = brackets[0]
    assert (b0["x1"], b0["x2"]) == (1, 2)
    # p matches R wilcox.test(exact = FALSE) — mannwhitneyu asymptotic w/ cc
    df = spec["data"]
    d1 = df.loc[df["Ecoregion"] == raw_levels[0], "BW_ft"].to_numpy(dtype=float)
    d2 = df.loc[df["Ecoregion"] == raw_levels[1], "BW_ft"].to_numpy(dtype=float)
    expected = stats.mannwhitneyu(
        d1, d2, alternative="two-sided", method="asymptotic", use_continuity=True
    ).pvalue
    assert b0["p_value"] == pytest.approx(float(expected), abs=1e-12)
    # ggpubr p.format label: a bare 2-significant-digit number, no prefix/stars
    assert "p" not in b0["p_label"] and "=" not in b0["p_label"]
    assert float(b0["p_label"]) == pytest.approx(b0["p_value"], rel=0.06)

    # stacked brackets sit strictly above one another (and above the data max)
    ys = [b["y"] for b in brackets]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)
    assert min(ys) > float(np.nanmax(df["BW_ft"].to_numpy(dtype=float)))
    for b in brackets:
        assert b["label_y"] > b["y"] > b["y"] - b["tip"]


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #


def test_render_boxplot_saves_png(osam):
    dat, _ = osam
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    _assert_saves(render_regional_boxplot(spec))


def test_render_boxplot_with_brackets_saves_png(osam):
    dat, _ = osam
    base = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion"
    )
    raw_levels = [lab.split("\n")[0] for lab in base["x_levels"]]
    spec = build_regional_boxplot_spec(
        dat, "BW_ft", "Bankfull Width (ft)", "Ecoregion", "Ecoregion",
        pairwise_comparisons=[[raw_levels[0], raw_levels[1]]],
    )
    assert spec["brackets"]
    _assert_saves(render_regional_boxplot(spec))


def test_render_boxplot_none_spec_returns_none():
    assert render_regional_boxplot(None) is None


def test_render_unstratified_curve_saves_png(osam):
    dat, _ = osam
    res = fit_regional_curve(dat, "BW_ft", "DA_km2", group_var=None)
    assert res["plot_spec"]["type"] == "regional_curve"
    _assert_saves(render_regional_curve_plot(res["plot_spec"]))


def test_render_stratified_curve_saves_png(osam):
    dat, _ = osam
    res = fit_regional_curve(dat, "BW_ft", "DA_km2", group_var="Ecoregion")
    assert res["plot_spec"]["type"] == "regional_curve_stratified"
    _assert_saves(render_regional_curve_plot(res["plot_spec"]))


def test_render_curve_plot_none_returns_none():
    assert render_regional_curve_plot(None) is None


# --------------------------------------------------------------------------- #
# build_model_summary_display
# --------------------------------------------------------------------------- #


def test_model_summary_display_columns_and_equation(osam):
    dat, _ = osam
    res = fit_regional_curve(dat, "BW_ft", "DA_km2", group_var=None)
    disp = build_model_summary_display(res["model_summary"])
    assert list(disp.columns) == [
        "group_level", "equation", "n_obs", "r_squared", "adj_r2",
        "p_value", "fit_status",
    ]
    assert disp["equation"].iloc[0].startswith("BW_ft = ")
    assert "× DA_km2^" in disp["equation"].iloc[0]
    assert disp["fit_status"].iloc[0] == "complete"


def test_model_summary_display_handles_insufficient_data():
    ms = pd.DataFrame(
        [{
            "response": "BW_ft", "predictor": "DA_km2", "group_var": "G",
            "group_level": "x", "n_obs": 2, "intercept": np.nan, "slope": np.nan,
            "coefficient_a": np.nan, "exponent_b": np.nan, "r_squared": np.nan,
            "adj_r2": np.nan, "p_value": np.nan, "fit_status": "insufficient_data",
        }]
    )
    disp = build_model_summary_display(ms)
    assert disp["equation"].iloc[0] == "N/A"
    assert disp["r_squared"].iloc[0] == "NA"
    assert disp["fit_status"].iloc[0] == "insufficient_data"
