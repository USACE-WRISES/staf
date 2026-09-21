"""The national scale and stratifier analysis (rule STRAT-10), on synthetic data.

The decision rule is EASI's, adapted to a few hundred stations: the adjusted
rank eta-squared decides, the coarsest level that keeps the Level III signal is
the supported level, and a class split needs its own evidence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import scale_analysis as sa

HIGHER = {"higher_is_better": True, "metric_family": "continuous"}


# --------------------------------------------------------------------------- #
# the statistic
# --------------------------------------------------------------------------- #
def test_no_effect_gives_an_adjusted_eta_near_zero_however_many_groups():
    """H/(n-1) inflates with the number of groups at this sample size (68 Level
    III groups over a few hundred stations); the adjusted form does not."""
    raw_few, raw_many, adj_few, adj_many = [], [], [], []
    for seed in range(12):                           # one draw is noise; the mean is the claim
        rng = np.random.default_rng(seed)
        values = pd.Series(rng.normal(0, 1, 600))
        a = sa.rank_eta2(values, pd.Series(rng.integers(0, 4, 600)))
        b = sa.rank_eta2(values, pd.Series(rng.integers(0, 60, 600)))
        raw_few.append(a["eta2"]); raw_many.append(b["eta2"])
        adj_few.append(a["eta2_adj"]); adj_many.append(b["eta2_adj"])
    assert np.mean(raw_many) > np.mean(raw_few) + 0.05      # the raw form grows with k
    assert np.mean(adj_few) < 0.01 and np.mean(adj_many) < 0.02


def test_a_real_group_effect_is_found():
    rng = np.random.default_rng(2)
    groups = pd.Series(np.repeat(["a", "b", "c"], 80))
    values = pd.Series(np.r_[rng.normal(0, 1, 80), rng.normal(3, 1, 80), rng.normal(6, 1, 80)])
    got = sa.rank_eta2(values, groups)
    assert got["eta2_adj"] > 0.7 and got["k"] == 3 and got["n"] == 240


def test_small_groups_are_left_out_and_counted():
    values = pd.Series(np.arange(23, dtype=float))
    groups = pd.Series(["a"] * 10 + ["b"] * 10 + ["c"] * 3)
    got = sa.rank_eta2(values, groups, min_group=5)
    assert got["k"] == 2 and got["n"] == 20 and got["n_groups_dropped"] == 1


def test_one_group_is_not_a_comparison():
    got = sa.rank_eta2(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), pd.Series(["a"] * 5))
    assert got["eta2_adj"] is None


# --------------------------------------------------------------------------- #
# the level rule
# --------------------------------------------------------------------------- #
def _table(l3, l2, l1):
    return {"l3": {"eta2_adj": l3}, "l2": {"eta2_adj": l2}, "l1": {"eta2_adj": l1}}


def test_a_metric_that_varies_only_at_level_iii_resolves_to_level_iii():
    assert sa.supported_level(_table(0.40, 0.10, 0.05)) == "l3"


def test_the_rule_picks_the_coarsest_passing_level():
    assert sa.supported_level(_table(0.40, 0.36, 0.34)) == "l1"
    assert sa.supported_level(_table(0.40, 0.36, 0.10)) == "l2"


def test_a_weak_level_iii_signal_is_national():
    assert sa.supported_level(_table(0.015, 0.01, 0.0)) == "national"


def test_a_near_tie_goes_coarser():
    # 0.025 keeps only 0.31 of Level III's 0.08 but sits within 0.02 of... no:
    # 0.08 - 0.065 = 0.015 is inside the tie band, so Level I is supported
    assert sa.supported_level(_table(0.08, 0.07, 0.065)) == "l1"


def test_level_iii_is_never_compared_with_itself():
    """Its ratio is always one, which would make it unreachable."""
    assert sa.supported_level(_table(0.30, 0.0, 0.0)) == "l3"


# --------------------------------------------------------------------------- #
# one metric end to end
# --------------------------------------------------------------------------- #
def _national_frame(n_per=40, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for l1, l2s in (("5", ("5.2", "5.3")), ("8", ("8.1", "8.3", "8.4"))):
        for l2 in l2s:
            for j in range(2):
                l3 = f"{l2}-{j}"
                for i in range(n_per):
                    rows.append({"station_key": f"{l3}-{i}", "l1": l1, "l2": l2, "l3": l3,
                                 "nars9": "NAP" if l1 == "5" else "SAP",
                                 "slope_class": ("lt_0.5", "0.5_to_2", "ge_2")[i % 3],
                                 "da_class": ("le_10", "10_to_100", "gt_100")[(i // 3) % 3],
                                 "huc12": f"h{l3}{i // 2}", "huc8": f"h{l3}",
                                 "pass_strict": i % 4 != 0,
                                 "rt_nrsa": "R" if i % 10 == 1 else "Im" if i % 10 == 0 else None})
    return pd.DataFrame(rows), rng


def test_a_slope_driven_metric_gets_a_slope_split_and_a_coarse_level():
    frame, rng = _national_frame()
    shift = frame["slope_class"].map({"lt_0.5": 0.0, "0.5_to_2": 6.0, "ge_2": 12.0})
    values = pd.Series(20.0 + shift + rng.normal(0, 1.5, len(frame)), index=frame.index)
    # pressured stations read lower, so the yardstick has something to find
    values[~frame["pass_strict"]] -= 8.0
    rec = sa.analyze_metric("phab_LSUB_DMM", HIGHER, frame, values, n_boot=30)
    assert rec["status"] == "decided"
    assert rec["supported_level"] == "national"          # no ecoregion signal at all
    assert rec["split"] == "NhdSlopeClass"
    assert rec["incremental"]["NhdSlopeClass"]["increment"] > 0.5
    assert rec["incremental"]["NhdDrainageAreaClass"]["increment"] < 0.05
    assert rec["stability"]["verdict"] in ("accept", "exploratory")


def test_a_regional_metric_resolves_to_its_level_and_stays_unsplit():
    frame, rng = _national_frame()
    shift = frame["l2"].map({"5.2": 0.0, "5.3": 5.0, "8.1": 10.0, "8.3": 15.0, "8.4": 20.0})
    values = pd.Series(30.0 + shift + rng.normal(0, 1.0, len(frame)), index=frame.index)
    rec = sa.analyze_metric("chem_COND", HIGHER, frame, values, n_boot=10)
    assert rec["supported_level"] == "l2"
    assert rec["split"] is None
    assert "No class split" in rec["decision_basis"]


def test_too_few_reference_stations_is_recorded_not_guessed():
    frame, rng = _national_frame(n_per=3)
    values = pd.Series(rng.normal(0, 1, len(frame)), index=frame.index)
    rec = sa.analyze_metric("x", HIGHER, frame, values, n_boot=5)
    assert rec["status"] == "insufficient_data" and rec["split"] is None
    assert rec["supported_level"] == "l3"


# --------------------------------------------------------------------------- #
# classes and the registry
# --------------------------------------------------------------------------- #
def test_classify_uses_the_declared_breaks():
    slope = sa.STRATIFIERS["NhdSlopeClass"]
    got = list(sa.classify([0.0, 0.00499, 0.005, 0.0199, 0.02, 0.4, -9998.0, None], slope))
    assert got == ["lt_0.5", "lt_0.5", "0.5_to_2", "0.5_to_2", "ge_2", "ge_2", None, None]
    area = sa.STRATIFIERS["NhdDrainageAreaClass"]
    got = list(sa.classify([0.5, 10.0, 10.01, 100.0, 100.5, 0.0, None], area))
    assert got == ["le_10", "le_10", "10_to_100", "10_to_100", "gt_100", None, None]


def test_classify_agrees_with_the_station_table_classes():
    from streamcurves import reference_screen as rs
    slopes = [0.0001, 0.005, 0.0123, 0.02, 0.2]
    assert list(sa.classify(slopes, sa.STRATIFIERS["NhdSlopeClass"])) == \
        [rs.slope_class(v) for v in slopes]
    areas = [1.0, 10.0, 55.0, 100.0, 4000.0]
    assert list(sa.classify(areas, sa.STRATIFIERS["NhdDrainageAreaClass"])) == \
        [rs.da_class(v) for v in areas]


def test_the_registry_answers_what_a_build_and_deep_need():
    reg = sa.build_registry(
        {"m": {"status": "decided", "supported_level": "l2", "split": "NhdSlopeClass",
               "split_status": "accept"},
         "n": {"status": "decided", "supported_level": "l3", "split": None}},
        inputs={"n_reference_stations": 770}, decided_on="2026-09-20")
    assert sa.entry_for("m", reg)["supported_level"] == "l2"
    strat = sa.stratifier_for("m", reg)
    assert strat["variable"] == "nhd_slope" and strat["breaks"] == [0.005, 0.02]
    assert strat["labels"] == ["lt_0.5", "0.5_to_2", "ge_2"] and strat["right"] is False
    assert "percent" in strat["display"]["lt_0.5"]
    assert sa.stratifier_for("n", reg) is None and sa.stratifier_for("absent", reg) is None
