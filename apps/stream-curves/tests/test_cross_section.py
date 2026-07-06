"""Tests for the Cross-Sections view layer (M6 part 2):
- detect_geo_cols on the OSAM fixture
- register_xs_metric (tables manipulation, idempotent)
- build_transect_plotly (plotly trace structure vs mod_cross_section.R:167-193)
- the commit -> rebuild_app_from_tables round-trip surfaces the new metric
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from shiny import reactive

from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR
from views.cross_section import (
    build_transect_plotly,
    detect_geo_cols,
    register_xs_metric,
    stale_stations,
)
from views.rebuild import rebuild_app_from_tables
from views.state import AppState

FIXTURE = GOLDEN_DIR.parent / "fixtures" / "OSAM_summarydata.xlsx"


def _tables():
    return read_input_workbook(FIXTURE)["metadata"]


# --------------------------------------------------------------------------- #
# detect_geo_cols
# --------------------------------------------------------------------------- #


def test_detect_geo_cols_on_osam():
    data = read_input_workbook(FIXTURE)["raw_data"]
    g = detect_geo_cols(data)
    assert g["id_col"] == "ID"
    assert g["lat_col"] == "US_Lat"
    assert g["lon_col"] == "US_Long"
    assert g["ok"] is True


def test_detect_geo_cols_no_coords():
    df = pd.DataFrame({"site": ["a", "b"], "value": [1, 2]})
    g = detect_geo_cols(df)
    assert g["ok"] is False
    assert g["id_col"] == "site"  # falls back to first column


# --------------------------------------------------------------------------- #
# stale_stations — degree-scale stations from pre-ad3e8e1 sessions
# --------------------------------------------------------------------------- #


def _cs(spans):
    """A stored site dict whose transects span the given station widths (m)."""
    return {
        "transects": [
            {"stations": [0.0, span / 2, span], "elevs": [1.0, 0.0, 1.0]}
            for span in spans
        ]
    }


def test_stale_stations_degree_scale_is_stale():
    assert stale_stations(_cs([0.0015, 0.0012, 0.0016])) is True


def test_stale_stations_metre_scale_is_fresh():
    assert stale_stations(_cs([160.0, 158.0, 161.0])) is False


def test_stale_stations_mixed_spans_counts_as_fresh():
    # one real transect is enough to treat the site as usable
    assert stale_stations(_cs([0.0015, 160.0])) is False


def test_stale_stations_empty_or_missing():
    assert stale_stations(None) is False
    assert stale_stations({}) is False
    assert stale_stations({"transects": []}) is False
    assert stale_stations({"transects": [{"stations": [], "elevs": []}]}) is True


# --------------------------------------------------------------------------- #
# register_xs_metric
# --------------------------------------------------------------------------- #


def test_register_xs_metric_adds_metric_and_links():
    tables = _tables()
    n_metrics = len(tables["metrics"])
    n_pred_links = len(tables["metric_predictors"])
    n_strat_links = len(tables["metric_stratifications"])
    n_preds = len(tables["predictors"])
    n_strats = len(tables["stratifications"])

    out = register_xs_metric(tables, "ER_xsec", "Entrenchment ratio (XS)")

    assert len(out["metrics"]) == n_metrics + 1
    new_row = out["metrics"][out["metrics"]["column_name"] == "ER_xsec"].iloc[0]
    assert new_row["display_name"] == "Entrenchment ratio (XS)"
    assert new_row["metric_family"] == "continuous"
    assert str(new_row["higher_is_better"]).upper() == "TRUE"
    assert new_row["notes"] == "cross-section derived"
    key = new_row["metric_key"]
    # linked to every predictor + stratification
    assert len(out["metric_predictors"]) == n_pred_links + n_preds
    assert len(out["metric_stratifications"]) == n_strat_links + n_strats
    assert set(out["metric_predictors"][out["metric_predictors"]["metric_key"] == key][
        "predictor_key"
    ]) == set(tables["predictors"]["predictor_key"])


def test_register_xs_metric_is_idempotent():
    tables = _tables()
    once = register_xs_metric(tables, "ER_xsec", "Entrenchment ratio (XS)")
    twice = register_xs_metric(once, "ER_xsec", "Entrenchment ratio (XS)")
    assert len(twice["metrics"]) == len(once["metrics"])


# --------------------------------------------------------------------------- #
# build_transect_plotly
# --------------------------------------------------------------------------- #


def test_build_transect_plotly_matches_r_traces():
    # simple V-shaped channel: thalweg at the middle station
    stations = list(np.linspace(0, 100, 41))
    elevs = list(np.abs(np.linspace(-5, 5, 41)) + 10.0)  # min 10 at center
    tr = {"stations": stations, "elevs": elevs, "bankfull_h": 1.0, "low_bank_h": 1.5}
    fig = build_transect_plotly(tr)
    assert isinstance(fig, go.Figure)

    # trace order + names as in R's add_lines sequence
    assert [t.name for t in fig.data] == [
        "Ground", "Bankfull", "Floodprone (2x)", "Low bank",
    ]
    assert [t.line.color for t in fig.data] == [
        "#6b4f3a", "#1f6fc0", "#9a6b3f", "#3a8a5c",
    ]
    assert [t.line.width for t in fig.data] == [2, 1.5, 1, 1.5]
    assert fig.data[1].line.dash is None  # bankfull solid
    assert fig.data[2].line.dash == "dot"
    assert fig.data[3].line.dash == "dash"

    # ground is stationed from the thalweg (min height exactly 0)
    assert round(float(np.min(fig.data[0].y)), 6) == 0.0
    assert float(fig.data[0].x[int(np.argmin(fig.data[0].y))]) == 0.0
    # floodprone sits at 2x bankfull; stage lines span the station range
    assert list(fig.data[2].y) == [2.0, 2.0]
    assert list(fig.data[1].x) == [float(np.min(fig.data[0].x)), float(np.max(fig.data[0].x))]
    # hover readout on the ground trace only
    assert fig.data[0].hovertemplate.startswith("station %{x:.0f} m")
    assert fig.data[1].hovertemplate is None

    # layout mirrors R: horizontal legend below, titled axes, tight margins
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.legend.y == -0.2
    assert fig.layout.xaxis.title.text == "Station from thalweg (m)"
    assert fig.layout.yaxis.title.text == "Height (m)"
    assert fig.layout.margin.l == 45 and fig.layout.margin.b == 35
    assert fig.layout.showlegend is True


# --------------------------------------------------------------------------- #
# commit -> rebuild round-trip
# --------------------------------------------------------------------------- #


def test_commit_rebuild_surfaces_new_metric():
    tables = _tables()
    site_id = str(tables["data"]["ID"].iloc[0])

    # emulate the commit block: add the 4 columns + register the metrics
    vals = {
        "ER_xsec": 1.8,
        "BHR_xsec": 1.1,
        "bankfull_ft_xsec": 24.5,
        "floodprone_ft_xsec": 60.0,
    }
    labels = {
        "ER_xsec": "Entrenchment ratio (XS)",
        "BHR_xsec": "Bank-height ratio (XS)",
        "bankfull_ft_xsec": "Bankfull width ft (XS)",
        "floodprone_ft_xsec": "Floodprone width ft (XS)",
    }
    mask = tables["data"]["ID"].astype(str) == site_id
    for nm, v in vals.items():
        tables["data"][nm] = np.nan
        tables["data"].loc[mask, nm] = v
        tables = register_xs_metric(tables, nm, labels[nm])

    state = AppState.fresh()
    with reactive.isolate():
        ok = rebuild_app_from_tables(state, tables, error_prefix="commit failed")
        assert ok is True
        mc = state.metric_config()
        derived = state.data()

    # the 4 committed columns are present in the rebuilt dataset
    for nm in vals:
        assert nm in derived.columns
    # and each registered as a metric
    registered = {cfg.get("column_name") for cfg in mc.values()}
    for nm in vals:
        assert nm in registered
    # the committed value survives the clean/derive round-trip
    er_key = next(k for k, cfg in mc.items() if cfg.get("column_name") == "ER_xsec")
    row = derived.loc[derived["ID"].astype(str) == site_id]
    assert float(row[mc[er_key]["column_name"]].iloc[0]) == 1.8
