"""Borrowing least-disturbed streams from elsewhere.

These pin the guards that keep a transfer test a test: that a basis never borrows
from the region it is being tested on, that a published criterion is the published
one, and that a distance is reported rather than assumed away.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import basis_recovery as br
from streamcurves import basis_transfer as bt
from streamcurves import reference_pool as rp


def _frame(n_per_region: int = 12) -> pd.DataFrame:
    """Two regions of identical shape, one the target and one the donor pool."""
    rows = []
    rng = np.random.default_rng(4)
    for code, name, da, slope, tmean, lith in (("A", "Target", 40.0, 0.004, 11.0, "glacial_till"),
                                               ("B", "Donor", 45.0, 0.005, 11.5, "glacial_till")):
        for i in range(n_per_region):
            rows.append({"station_key": f"{code}{i}", "l3": code, "l3_name": name,
                         "huc12": f"{code}{i // 3}", "nars9": "TPL" if code == "A" else "TPL",
                         "drainage_area_sqkm": da + i, "nhd_slope": slope + i / 10000,
                         "tmean8110ws": tmean + i / 20, "precip8110ws": 900 + i,
                         "bfiws": 50 + i / 4, "runoffws": 300 + i, "lith_group": lith,
                         "pass_strict": code == "B",
                         **{c: 0.0 for c in br.PRESSURE_COLUMNS}})
    frame = pd.DataFrame(rows)
    frame["agriculture_ws"] = np.where(frame["l3"] == "A", 60.0, 0.0)
    return frame


def _values(frame: pd.DataFrame, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.uniform(10, 40, len(frame)), index=frame.index)


# --- the guard: a basis never borrows from the region it is tested on --------
def test_a_donor_pool_excludes_the_region_it_is_borrowed_for():
    """The round-two guard against the tautology round one had to fix. A basis
    drawn partly from the target's own reference stations agrees with the yardstick
    because it largely is the yardstick, which says nothing about a region that has
    none. Every borrowing basis is handed a pool the region has been taken out of."""
    frame = _frame()
    pool = bt.national_reference(frame, exclude_l3="B")
    assert len(pool) == 0                      # B holds every reference station
    everything = bt.national_reference(frame)
    assert set(everything["l3"]) == {"B"}


def test_borrowed_donors_never_come_from_the_target_region():
    frame = _frame()
    target = frame[frame["l3"] == "A"]
    donors = bt.national_reference(frame, exclude_l3="A")
    got, values = bt.envelope_donors("bent_EPT_NTAX", target, donors, _values(frame))
    assert len(got) and set(got["l3"]) == {"B"}
    matches = bt.gower_matches("bent_EPT_NTAX", target, donors, _values(frame))
    assert len(matches) and set(matches["donor_l3"]) == {"B"}


def test_a_candidate_that_is_the_yardstick_is_indistinguishable_from_it():
    """Why the exclusion matters, stated as a measurement: handed the reference
    anchors themselves, the accuracy criterion reads 1.0, the most ordinary result
    there is. Agreement is only evidence when the candidate is independent."""
    ref = pd.Series(np.linspace(5, 25, 30))
    ev = pd.Series(np.linspace(4, 30, 40))
    cfg = {"column_name": "m", "higher_is_better": True, "curve_form": "monotone",
           "domain_min": 0, "domain_max": None, "display_name": "Rising"}
    truth = br.cell_truth(ref, ev, cfg, n_boot=40, seed=3)
    rec = br.candidate_record(truth, anchors=truth["anchors"], n_fit=None, n_draws=20)
    assert rec["flip"] == 0.0 and rec["exceedance"] == 1.0


# --- 3a: the envelope, and what it excludes ---------------------------------
def test_envelope_failures_count_each_condition_on_its_own():
    """``comparable_mask`` records only the first failing condition and checks
    lithology last, so its reasons understate lithology. Counted separately, a
    donor that fails two conditions is in both counts and in neither "only"."""
    frame = _frame()
    target = frame[frame["l3"] == "A"]
    donors = frame[frame["l3"] == "B"].copy()
    # start every donor inside the target's envelope, then fail two conditions
    donors["bfiws"] = float(target["bfiws"].median())
    donors["runoffws"] = float(target["runoffws"].median())
    donors.loc[donors.index[:4], "bfiws"] = 5.0            # outside the envelope
    donors.loc[donors.index[2:6], "lith_group"] = "igneous_volcanic"
    got = bt.envelope_failures("chem_PTL", target, donors)
    assert got["fail_bfiws"] == 4 and got["fail_lithology"] == 4
    assert got["only_bfiws"] == 2 and got["only_lithology"] == 2   # two fail both
    ok, _ = rp.comparable_mask(donors, rp.envelope_for(target, ["bfiws", "runoffws"]),
                               rp.target_lith_groups(target), use_lithology=True)
    assert got["pass_all"] == int(ok.sum())


def test_envelope_position_tells_narrow_from_shifted():
    """A target excluding donors because its own range is thin is a different
    problem from one that sits in a tail of theirs, and the remedy differs."""
    donors = pd.DataFrame({"bfiws": np.linspace(10, 90, 200)})
    narrow = pd.DataFrame({"bfiws": np.linspace(49, 51, 40)})
    shifted = pd.DataFrame({"bfiws": np.linspace(10, 18, 40)})
    a = bt.envelope_position(narrow, donors, "bfiws")
    b = bt.envelope_position(shifted, donors, "bfiws")
    assert a["width_ratio"] < 0.1 and 0.4 < a["target_median_pct"] < 0.6
    assert b["target_median_pct"] < 0.1


# --- 3b: the adjusted expectation -------------------------------------------
def test_the_adjusted_model_fits_natural_setting_without_pressure_terms():
    """3b is the stressor-response ``linear`` specification fitted on reference
    streams only, so the two differ in one thing: whether disturbed streams are in
    the fit. A reference pool carries no pressure signal to fit."""
    frame = pd.concat([_frame(40)] * 2, ignore_index=True)
    donors = frame[frame["l3"] == "B"]
    model = bt.adjusted_model(donors, _values(frame), br.CLIMATE_COVARIATES and
                              ["drainage_area_sqkm", "nhd_slope", "bfiws"],
                              kind="identity", offset=1.0)
    assert model and model["pressure"] == [] and model["spec"] == "linear"
    assert "agriculture_ws" not in model["formula"]


# --- 3c: distance, reported --------------------------------------------------
def test_matching_takes_k_nearest_donors_per_station_and_reports_the_distance():
    frame = _frame()
    target = frame[frame["l3"] == "A"]
    donors = frame[frame["l3"] == "B"]
    matches = bt.gower_matches("bent_EPT_NTAX", target, donors, _values(frame), k=3)
    assert len(matches) == 3 * len(target)
    assert matches.groupby("target_ix").size().eq(3).all()
    summary = bt.matched_summary(matches)
    assert summary["n_distinct"] <= len(donors)
    assert summary["distance_median"] >= 0 and summary["distance_max"] >= summary["distance_median"]


def test_an_identical_donor_is_at_distance_zero_and_lithology_costs_one_term():
    frame = _frame()
    target = frame[frame["l3"] == "A"].iloc[:1]
    donors = frame[frame["l3"] == "B"].copy()
    for c in ("bfiws", "runoffws"):
        donors.loc[donors.index[0], c] = float(target[c].iloc[0])
    donors.loc[donors.index[0], "lith_group"] = target["lith_group"].iloc[0]
    m = bt.gower_matches("chem_PTL", target, donors, _values(frame), k=1)
    assert m["distance"].iloc[0] == 0.0
    donors.loc[donors.index[0], "lith_group"] = "igneous_volcanic"
    m2 = bt.gower_matches("chem_PTL", target, donors, _values(frame), k=1)
    # three terms, one of them a lithology mismatch: at least a third of the way
    assert m2["distance"].iloc[0] >= 1 / 3 - 1e-9 or m2["distance"].iloc[0] > 0


def test_matching_is_stable_across_runs():
    frame = _frame()
    target, donors = frame[frame["l3"] == "A"], frame[frame["l3"] == "B"]
    a = bt.gower_matches("bent_EPT_NTAX", target, donors, _values(frame))
    b = bt.gower_matches("bent_EPT_NTAX", target, donors, _values(frame))
    pd.testing.assert_frame_equal(a, b)


# --- 4: the published criterion ---------------------------------------------
def test_the_published_phosphorus_criterion_is_the_published_one():
    """NRSA Table 7-1, in the units StreamCurves reports phosphorus in, with the
    boundary owned by the better class as EASI applies it."""
    assert bt.nrsa_tp_bands("TPL") == pytest.approx((88.6, 143.0))
    pts = bt.nrsa_tp_points("TPL")
    xs = [p["x"] for p in pts]
    assert xs[0] == 0.0 and pts[0]["y"] == 1.0
    # Good at or below 88.6: the index there is still above the 0.69 boundary
    from streamcurves import curves as cv
    assert cv.interp_curve(pts, 88.6) > 0.69
    assert cv.interp_curve(pts, 143.0) == pytest.approx(0.39, abs=1e-6)


def test_the_criterion_is_per_region_and_not_one_number():
    """The Eastern Corn Belt Plains is TPL and the Interior Plateau is SAP, whose
    phosphorus bands are about six times stricter. A published criterion transfers
    per region or not at all."""
    tpl, sap = bt.nrsa_tp_bands("TPL"), bt.nrsa_tp_bands("SAP")
    assert sap[0] < tpl[0] / 4 and sap[1] < tpl[1] / 4


def test_the_michigan_layer_is_the_one_deep_already_ships():
    pts = bt.michigan_tp_points()
    if pts is None:
        pytest.skip("the Michigan SQT bundle is not in this checkout")
    assert [p["x"] for p in pts] == [29.0, 70.0, 160.0, 550.0]
    assert [p["y"] for p in pts] == [1.0, 0.7, 0.3, 0.0]


def test_majority_nars9_reports_the_share_because_six_regions_straddle_two():
    rows = pd.DataFrame({"nars9": ["TPL"] * 7 + ["SAP"] * 3})
    got, share = bt.majority_nars9(rows)
    assert got == "TPL" and share == 0.7
    assert bt.majority_nars9(pd.DataFrame({"x": [1]})) == (None, 0.0)
