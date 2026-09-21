"""The committed station screen table (rule REF-04).

The table is what makes a regional build offline: reference membership is read
from it and from nothing else. These tests pin its contract, prove its verdicts
recompute from the variables it stores, and hold the three pilot regions to the
counts the owner was shown when he chose the screen (2026-09-19).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import reference_screen as rs

pytestmark = pytest.mark.skipif(
    not (rs.station_screen_available() and nd.multi_cycle_available()),
    reason="station_screen.parquet is not built (scripts/nrsa/build_station_screen.py)")


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    return rs.load_station_screen()


def test_the_table_covers_every_archive_station(table):
    stations = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).stations
    assert table["station_key"].is_unique
    assert set(table["station_key"]) == set(stations["station_key"].astype(str))
    assert len(table) == 4378


def test_the_columns_are_the_contract(table):
    must = ["station_key", "comid", "l3", "l2", "l1", "nars9", "huc8", "stream_order",
            "drainage_area_sqkm", "nhd_slope", "fcode_class", "slope_class", "da_class",
            "elevws", "precip8110ws", "tmean8110ws", "runoffws", "bfiws", "hydrlcondws",
            "kffactws", "lith_group", "pctimp2019ws", "agriculture_ws", "rddensws", "dor",
            "nabd_densws", "npdesdensws", "mines_ws", "screen_evaluable", "pass_strict",
            "pass_relaxed", "fail_strict", "fail_relaxed", "rt_nrsa", "source"]
    assert [c for c in must if c not in table.columns] == []
    assert table["pass_strict"].dtype == bool and table["pass_relaxed"].dtype == bool


def test_the_flags_recompute_from_the_stored_variables(table):
    for tier in rs.TIERS:
        ok, why = rs.evaluate(table, tier)
        assert ok.equals(table[f"pass_{tier}"]), tier
        assert why.astype(object).equals(table[f"fail_{tier}"].astype(object)), tier


def test_the_derived_variables_recompute(table):
    again = rs.derive_screen_variables(table.drop(columns=["agriculture_ws", "dor", "mines_ws"]))
    for col in ("agriculture_ws", "dor", "mines_ws"):
        assert np.allclose(again[col], table[col], equal_nan=True), col


def test_a_station_with_a_missing_variable_never_passes(table):
    unevaluable = table[~table["screen_evaluable"]]
    assert not unevaluable["pass_strict"].any()
    assert unevaluable["fail_strict"].str.contains("missing:").all()


def test_strict_is_never_looser_than_relaxed(table):
    assert not (table["pass_strict"] & ~table["pass_relaxed"]).any()


def test_the_classes_use_easis_boundaries(table):
    assert set(table["slope_class"].dropna()) <= set(rs.SLOPE_LABELS)
    assert set(table["da_class"].dropna()) <= set(rs.DA_LABELS)
    sample = table.dropna(subset=["nhd_slope"]).head(500)
    assert [rs.slope_class(v) for v in sample["nhd_slope"]] == list(sample["slope_class"])


def test_the_levels_come_from_the_vendored_crosswalk(table):
    from streamcurves._vendor.easi import geo
    for code in ("58", "55", "71"):
        want = geo.strata_for(code)
        got = table[table["l3"] == code].iloc[0]
        assert (got["l2"], got["l1"]) == (want["l2"], want["l1"]), code


def test_the_meta_file_pins_the_table(table):
    meta = json.loads(rs.STATION_SCREEN_META_PATH.read_text(encoding="utf-8"))
    assert meta["sha256"] == rs.file_sha256(rs.STATION_SCREEN_PATH)
    assert meta["rows"] == len(table) and meta["screenId"] == rs.SCREEN_ID
    assert rs.station_screen_identity()["sha256"] == meta["sha256"]


def test_the_table_is_small():
    assert rs.STATION_SCREEN_PATH.stat().st_size < 2_000_000


# --------------------------------------------------------------------------- #
# the owner's census (DATA-10 frame, non-canal)
# --------------------------------------------------------------------------- #
def _in_frame(table: pd.DataFrame, l3: str | None) -> pd.DataFrame:
    max_order, protocols = nd.governed_frame("wadeable")
    panel, _ = nd.resolve_site_panel(l3, dataset=nd.MULTI_CYCLE_DATASET_ID,
                                     max_stream_order=max_order, protocols=protocols)
    got = table[table["station_key"].isin(set(panel["station_key"]))]
    return got[got["fcode_class"] != "canal"]


@pytest.mark.parametrize("l3,n_frame,n_strict,n_relaxed", [
    ("58", 175, 66, 98),         # Northeastern Highlands: self-sufficient
    ("71", 41, 3, 8),            # Interior Plateau: must borrow
    ("55", 44, 0, 0),            # Eastern Corn Belt Plains: no reference streams of its own
])
def test_pilot_counts_under_the_runs_own_frame(table, l3, n_frame, n_strict, n_relaxed):
    """The counts the owner chose the screen on (2026-09-19). The table is
    committed, so a change here is a deliberate rebuild, never drift."""
    frame = _in_frame(table, l3)
    assert len(frame) == n_frame
    assert int(frame["pass_strict"].sum()) == n_strict
    assert int(frame["pass_relaxed"].sum()) == n_relaxed


def test_the_parent_regions_hold_the_reference_the_pilots_lack(table):
    frame = _in_frame(table, None)
    strict = frame[frame["pass_strict"]]
    assert len(frame) == 3267 and len(strict) == 770
    assert int((strict["l2"] == "8.3").sum()) == 46        # Interior Plateau's Level II
    assert int((strict["l2"] == "8.2").sum()) == 0         # ECBP's Level II has none
    assert int((strict["l1"] == "8").sum()) == 148         # ECBP's Level I
    assert int((strict["l2"] == "5.3").sum()) == 79        # the Northeastern Highlands' Level II


def test_the_frame_helper_agrees_with_the_panel_frame(table):
    """reference_pool.national_frame is the run's frame: DATA-10, then non-canal."""
    from streamcurves import reference_pool as rp
    max_order, protocols = nd.governed_frame("wadeable")
    frame, ledger = rp.national_frame(max_stream_order=max_order, protocols=protocols)
    assert set(frame["station_key"]) == set(_in_frame(table, None)["station_key"])
    assert ledger["reason"].astype(str).str.contains("canal").sum() == 34
    assert (frame["fcode_class"].astype(object) != "canal").all()
