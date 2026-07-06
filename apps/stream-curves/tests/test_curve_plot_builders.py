"""Smoke tests for the views/curve_plots.py builders used by phase 4 — each
figure must build from the OSAM fixture and save a real PNG (the headless
browser can't exercise savefig, so this is the render-path coverage)."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from streamcurves.cleaning import clean_data
from streamcurves.curves import build_reference_curve
from streamcurves.derive import derive_variables
from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR
from views.curve_plots import (
    build_overlay_bar_chart,
    build_overlay_curve_plot,
    build_reference_curve_plot,
    build_reference_distribution_plot,
    reference_values_from_data,
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
    return dat, bundle["metric_config"]


def _assert_saves(fig) -> int:
    assert fig is not None
    buf = io.BytesIO()
    fig.save(buf, width=8, height=6, dpi=100, verbose=False)
    assert len(buf.getvalue()) > 5000  # a real PNG, not a stub
    return len(buf.getvalue())


def test_reference_distribution_plot_builds_and_saves(osam):
    dat, mc = osam
    res = build_reference_curve(dat, "perRiffle", mc)
    vals = reference_values_from_data(dat, mc, "perRiffle")
    fig = build_reference_distribution_plot(vals, res["curve_row"], mc, "perRiffle")
    _assert_saves(fig)


def test_reference_curve_plot_builds_and_saves(osam):
    dat, mc = osam
    res = build_reference_curve(dat, "perRiffle", mc)
    fig = build_reference_curve_plot(res["curve_points"], res["curve_row"], mc, "perRiffle")
    _assert_saves(fig)


def test_overlay_builders_build_and_save(osam):
    dat, mc = osam
    strat = dat["Ecoregion"].astype(str)
    levels = sorted(v for v in strat.dropna().unique() if v and v != "nan")
    assert len(levels) >= 2

    rows = [
        build_reference_curve(dat[strat == lvl], "perRiffle", mc, stratum_label=lvl)[
            "curve_row"
        ]
        for lvl in levels
    ]
    curve_rows = pd.concat(rows, ignore_index=True)
    _assert_saves(build_overlay_curve_plot(curve_rows, mc))

    plot_data = dat.copy()
    plot_data[".summary_stratum"] = strat
    _assert_saves(build_overlay_bar_chart(plot_data, "perRiffle", mc, ".summary_stratum", levels))


def test_distribution_plot_none_when_too_few_values(osam):
    _, mc = osam
    res_row = build_reference_curve(
        pd.DataFrame({"perRiffle": [1.0]}), "perRiffle", mc
    )["curve_row"]
    assert build_reference_distribution_plot([1.0], res_row, mc, "perRiffle") is None
