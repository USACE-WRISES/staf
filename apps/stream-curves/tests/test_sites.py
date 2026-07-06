"""Tests for streamcurves.sites (port of app/helpers/site_model.R)."""

import math

import numpy as np
import pandas as pd

from streamcurves import sites as S


def _stub_bieger(da_sqkm, division):
    return {"width_m": 2.0, "depth_m": 0.5, "area_m2": 1.0}


# --------------------------------------------------------------------------- #
# assemble_sites
# --------------------------------------------------------------------------- #
def test_assemble_sites_tags_source_and_binds():
    up = pd.DataFrame({"id": ["u1"], "lat": [40.0]})
    nr = pd.DataFrame({"id": ["n1"], "lat": [41.0]})
    out = S.assemble_sites(upload=up, nrsa=nr, pins=None)
    assert out[".source"].tolist() == ["upload", "nrsa"]
    assert out["id"].tolist() == ["u1", "n1"]


def test_assemble_sites_all_empty_returns_none():
    assert S.assemble_sites(None, None, None) is None
    assert S.assemble_sites(pd.DataFrame(), None, None) is None


# --------------------------------------------------------------------------- #
# dedup_sites
# --------------------------------------------------------------------------- #
def test_dedup_keeps_priority_and_missing_and_order():
    df = pd.DataFrame(
        {
            "id": ["A", "B", "C", "D"],
            "lon": [-83.0, -83.0, -90.0, np.nan],
            "lat": [40.0, 40.0, 45.0, np.nan],
            ".source": ["pin", "upload", "nrsa", "upload"],
        }
    )
    out = S.dedup_sites(df, "lon", "lat", tol_m=50)
    # A (pin) is a near-duplicate of B (upload); upload wins -> A dropped.
    # C is far (kept); D has missing coords (always kept). Original order preserved.
    assert out["id"].tolist() == ["B", "C", "D"]


def test_dedup_single_row_noop():
    df = pd.DataFrame({"id": ["A"], "lon": [1.0], "lat": [2.0]})
    assert S.dedup_sites(df, "lon", "lat").equals(df)


# --------------------------------------------------------------------------- #
# attach_by_comid
# --------------------------------------------------------------------------- #
def test_attach_by_comid_left_join_with_na():
    base = pd.DataFrame({"site": ["s1", "s2"], "COMID": ["1", "9"]})
    wide = pd.DataFrame({"COMID": [1, 2], "runoff": [10.0, 20.0]})
    out = S.attach_by_comid(base, "COMID", wide)
    assert out["runoff"].tolist()[0] == 10.0
    assert math.isnan(out["runoff"].tolist()[1])  # COMID 9 has no match


def test_attach_by_comid_no_comid_column_noop():
    base = pd.DataFrame({"site": ["s1"]})
    wide = pd.DataFrame({"NOPE": [1]})
    assert S.attach_by_comid(base, "COMID", wide) is base


# --------------------------------------------------------------------------- #
# streamcat_da_col / coverage_table
# --------------------------------------------------------------------------- #
def test_streamcat_da_col_case_insensitive():
    assert S.streamcat_da_col(pd.DataFrame(columns=["x", "WsAreaSqKm"])) == "WsAreaSqKm"
    assert S.streamcat_da_col(pd.DataFrame(columns=["wsareasqkmws"])) == "wsareasqkmws"
    assert S.streamcat_da_col(pd.DataFrame(columns=["x", "y"])) is None


def test_coverage_table():
    df = pd.DataFrame({"a": [1, 2, None, 4], "b": [None, None, None, None]})
    cov = S.coverage_table(df)
    assert list(cov.columns) == ["metric", "n_available", "n_total", "pct"]
    row_a = cov[cov["metric"] == "a"].iloc[0]
    assert row_a["n_available"] == 3 and row_a["n_total"] == 4 and row_a["pct"] == 75.0
    row_b = cov[cov["metric"] == "b"].iloc[0]
    assert row_b["n_available"] == 0 and row_b["pct"] == 0.0


# --------------------------------------------------------------------------- #
# regional_curve_predict / compile_site_table (injected bieger_geometry)
# --------------------------------------------------------------------------- #
def test_regional_curve_predict_units_and_rounding():
    pred = S.regional_curve_predict(10.0, "USA", bieger_geometry=_stub_bieger)
    assert pred["pred_BW_ft"] == round(2.0 * S.M_TO_FT, 2)
    assert pred["pred_BD_ft"] == round(0.5 * S.M_TO_FT, 3)
    assert pred["pred_BA_ft2"] == round(1.0 * S.M2_TO_FT2, 2)


def test_compile_site_table_join_da_division_and_preds():
    base = pd.DataFrame(
        {"site_id": ["s1", "s2"], "lat": [40.0, 41.0], "lon": [-83.0, -84.0], "COMID": ["1", "2"]}
    )
    wide = pd.DataFrame({"COMID": [1, 2], "WsAreaSqKm": [100.0, 200.0], "runoff": [5.0, 6.0]})
    out = S.compile_site_table(
        base, "lat", "lon", comid_col="COMID", streamcat_wide=wide,
        physio_path=None, bieger_geometry=_stub_bieger,
    )
    # StreamCAT metrics joined
    assert out["runoff"].tolist() == [5.0, 6.0]
    # DA_mi2 derived from WsAreaSqKm (sq km -> sq mi), rounded to 3
    assert out["DA_mi2"].tolist() == [
        round(100.0 * S.SQKM_TO_SQMI, 3),
        round(200.0 * S.SQKM_TO_SQMI, 3),
    ]
    # no physio path -> national division
    assert out["bieger_division"].tolist() == ["USA", "USA"]
    # predicted bankfull columns present + from the stub geometry
    assert out["pred_BW_ft"].tolist() == [round(2.0 * S.M_TO_FT, 2)] * 2
    assert out["pred_BD_ft"].tolist() == [round(0.5 * S.M_TO_FT, 3)] * 2
    assert out["pred_BA_ft2"].tolist() == [round(1.0 * S.M2_TO_FT2, 2)] * 2
