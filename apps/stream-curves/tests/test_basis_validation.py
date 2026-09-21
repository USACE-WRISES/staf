"""The admission protocol every candidate scoring basis is judged by (2026-09-20).

Fifteen of the 75 Level III ecoregions with a usable panel hold no least-disturbed
station at all and 37 are under 15 percent, so methodology 0.13 has to decide what
else may anchor a curve. The decision is per metric and per region, on measured
agreement rather than preference, and these tests pin the measuring.

The engine is not mocked: a basis is a fitting population handed to the same
``build_reference_curve`` that ships, so a change to the curve geometry moves these
numbers exactly as it moves a published curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import basis_validation as bv

RISING = {"column_name": "m", "higher_is_better": True, "curve_form": "monotone",
          "domain_min": 0, "domain_max": None, "display_name": "Rising metric"}
FALLING = {**RISING, "higher_is_better": False, "display_name": "Falling metric"}


def _clean(n, lo, hi, seed=3):
    return pd.Series(np.random.default_rng(seed).uniform(lo, hi, n))


# --- the pieces --------------------------------------------------------------
def test_the_bands_are_deeps_published_breaks():
    assert bv.band_of(0.39) == "NF" and bv.band_of(0.3901) == "AR"
    assert bv.band_of(0.69) == "AR" and bv.band_of(0.6901) == "F"
    assert bv.band_of(None) is None and bv.band_of(float("nan")) is None


def test_a_curve_is_built_from_whatever_population_it_is_handed():
    """The point the whole comparison rests on: the engine is basis-agnostic, so a
    basis is a population and nothing else."""
    a, _ = bv.curve_for(_clean(40, 10, 30), RISING)
    b, _ = bv.curve_for(_clean(40, 50, 90, seed=4), RISING)
    assert a and b
    assert [p["x"] for p in a] != [p["x"] for p in b]


def test_disagreement_counts_class_changes_and_ignores_unscored():
    assert bv.disagreement(["F", "AR", "NF"], ["F", "AR", "NF"]) == 0.0
    assert bv.disagreement(["F", "AR"], ["AR", "AR"]) == 0.5
    assert bv.disagreement(["F", None], [None, "NF"]) is None      # nothing comparable


def test_a_shifted_population_moves_class_calls():
    """A curve anchored on a degraded population calls degraded sites healthy. This
    is the failure the protocol exists to measure."""
    good = _clean(60, 20, 40)                       # least-disturbed values
    poor = _clean(60, 2, 10, seed=5)                # the same region, all disturbed
    evald = pd.concat([good, poor], ignore_index=True)
    on_good = bv.calls_for(bv.curve_for(good, RISING)[0], evald)
    on_poor = bv.calls_for(bv.curve_for(poor, RISING)[0], evald)
    flip = bv.disagreement(on_good, on_poor)
    assert flip > 0.4, flip
    # and specifically: sites the reference curve calls Non-Functioning are
    # promoted by the degraded anchor
    promoted = sum(1 for g, p in zip(on_good, on_poor) if g == "NF" and p in ("AR", "F"))
    assert promoted > 0


# --- uncertainty is an admission requirement ---------------------------------
def test_a_basis_states_an_interval_on_its_own_index():
    iv = bv.index_interval(_clean(60, 10, 40), RISING, _clean(30, 5, 45, seed=9), n_boot=40)
    assert iv["n_boot_valid"] > 0
    assert iv["median_width"] is not None and iv["median_width"] > 0
    assert len(iv["low"]) == len(iv["high"]) == iv["n_eval"]
    assert all(lo <= hi for lo, hi in zip(iv["low"], iv["high"]))


def test_a_thin_population_cannot_state_one():
    iv = bv.index_interval(pd.Series([1.0, 2.0, 3.0]), RISING, _clean(10, 1, 3), n_boot=20)
    assert iv["median_width"] is None and iv["n_boot_valid"] == 0


def test_a_basis_that_cannot_state_its_uncertainty_is_refused_however_well_it_agrees():
    rec = {"has_curve": True, "quantified": False, "flip_vs_reference": 0.0}
    assert bv.verdict(rec, accept=0.045, exploratory=0.15) == "not_quantified"


# --- the comparison ----------------------------------------------------------
def test_the_yardstick_agrees_with_itself():
    """The reference row reads accept, not unevaluable: it is the thing being
    compared against, and a table where the yardstick fails its own test is
    unreadable."""
    ref = _clean(50, 10, 30)
    got = bv.compare_bases({"reference": ref, "other": _clean(50, 10, 30, seed=8)},
                           RISING, ref, n_boot=20)
    assert got[0]["basis"] == bv.REFERENCE_BASIS
    assert got[0]["flip_vs_reference"] == 0.0
    assert bv.verdict(got[0], accept=0.045, exploratory=0.15) == "accept"


def test_a_region_with_no_reference_pool_gets_no_verdict_rather_than_a_flattering_one():
    """The Eastern Corn Belt Plains case. With no yardstick there is nothing to
    agree with, and the protocol says so instead of scoring the candidate against
    itself."""
    got = bv.compare_bases({"all_sites": _clean(40, 5, 15)}, RISING,
                           _clean(40, 5, 15), n_boot=20)
    assert [r["basis"] for r in got] == ["all_sites"]
    assert got[0]["flip_vs_reference"] is None
    assert bv.verdict(got[0], accept=0.045, exploratory=0.15) == "not_evaluable"


def test_verdicts_sit_on_the_thresholds_they_are_given():
    base = {"has_curve": True, "quantified": True}
    assert bv.verdict({**base, "flip_vs_reference": 0.04}, accept=0.045, exploratory=0.15) == "accept"
    assert bv.verdict({**base, "flip_vs_reference": 0.10}, accept=0.045, exploratory=0.15) == "exploratory"
    assert bv.verdict({**base, "flip_vs_reference": 0.40}, accept=0.045, exploratory=0.15) == "reject"
    assert bv.verdict({"has_curve": False}, accept=0.045, exploratory=0.15) == "no_curve"


def test_the_summary_is_one_row_per_basis():
    ref = _clean(40, 10, 30)
    got = bv.compare_bases({"reference": ref, "all_sites": _clean(60, 2, 30, seed=2)},
                           FALLING, ref, n_boot=20)
    for r in got:
        r["region"], r["metric"] = "X", "m"
        r["verdict"] = bv.verdict(r, accept=0.045, exploratory=0.15)
    tab = bv.summarize(got)
    assert list(tab.basis) == ["reference", "all_sites"]
    assert "calls" not in tab.columns          # the per-site detail stays out of the table
    assert tab.n_fit.tolist() == [40, 60]


@pytest.mark.parametrize("cfg", [RISING, FALLING])
def test_direction_is_read_from_the_metric_config_not_assumed(cfg):
    """The scale registry's entry carries no direction. Handing that to the engine
    instead of the metric config builds the curve the wrong way up, and every flip
    rate computed from it would be meaningless."""
    vals = _clean(50, 10, 40)
    pts, _ = bv.curve_for(vals, cfg)
    ys = [p["y"] for p in pts]
    assert (ys[0] < ys[-1]) is bool(cfg["higher_is_better"])


# --------------------------------------------------------------------------- #
# A basis that is a rule or a model, not a population
# --------------------------------------------------------------------------- #
def _points(q25, q75):
    """A rising seed shaped like the engine's, for a basis that supplies anchors."""
    iqr = q75 - q25
    return [{"x": 0.0, "y": 0.0}, {"x": q25 * 3 / 7, "y": 0.30}, {"x": q25, "y": 0.70},
            {"x": q75, "y": 1.0}, {"x": q75 + iqr * 0.3, "y": 1.0}]


def test_a_percentile_rule_or_a_model_may_supply_its_own_curve():
    """A selected-percentile rule and a fitted model are not populations. They are
    judged on the same two outputs as every other basis."""
    ref = _clean(50, 20, 40)
    evald = _clean(40, 5, 45, seed=11)
    rec = bv.evaluate_basis("selected_percentile", None, RISING, evald,
                            reference_calls=bv.calls_for(bv.curve_for(ref, RISING)[0], evald),
                            points=_points(18.0, 34.0),
                            resample=lambda rng: _points(18.0 + rng.normal(0, 1.5),
                                                         34.0 + rng.normal(0, 1.5)),
                            n_boot=60)
    assert rec["has_curve"] and rec["curve_status"] == "supplied"
    assert rec["n_fit"] == 0                       # it consumed no population of its own
    assert rec["flip_vs_reference"] is not None
    assert rec["quantified"] and rec["median_index_width"] > 0
    assert bv.verdict(rec, accept=0.045, exploratory=0.15) in ("accept", "exploratory", "reject")


def test_a_supplied_curve_with_no_resampler_is_refused():
    """The uncertainty bar is the same one every basis meets. A model that states a
    point estimate and no interval does not clear it."""
    evald = _clean(40, 5, 45, seed=11)
    rec = bv.evaluate_basis("model_without_intervals", None, RISING, evald,
                            reference_calls=["F"] * 40, points=_points(18.0, 34.0))
    assert rec["has_curve"] and not rec["quantified"]
    assert bv.verdict(rec, accept=0.045, exploratory=0.15) == "not_quantified"


def test_the_resampler_interval_widens_with_the_models_own_spread():
    """The interval covers the basis's uncertainty, so a shakier rule reports a
    wider one rather than the same number with a caveat."""
    evald = _clean(40, 5, 45, seed=11)

    def rec_for(sd):
        return bv.evaluate_basis("m", None, RISING, evald, reference_calls=["F"] * 40,
                                 points=_points(18.0, 34.0),
                                 resample=lambda rng: _points(18.0 + rng.normal(0, sd),
                                                              34.0 + rng.normal(0, sd)),
                                 n_boot=80)
    tight, loose = rec_for(0.5), rec_for(6.0)
    assert loose["median_index_width"] > tight["median_index_width"]


# --------------------------------------------------------------------------- #
# The hold-out design, which is what makes the comparison a test (2026-09-20)
# --------------------------------------------------------------------------- #
def test_a_within_region_basis_is_built_blind_to_the_reference_stations():
    """Without this the comparison is a tautology.

    A basis drawn from the target region overlaps that region's reference set
    precisely where the reference set exists: measured across the regions that can
    be tested at all, the lowest-pressure fraction overlaps reference by a median
    of 0.80 and by 1.00 in the cleanest of them. It then agrees with the yardstick
    because it largely is the yardstick, which says nothing about a region that has
    none. The hold-out bases drop the reference stations before the screen runs, so
    they reproduce the Eastern Corn Belt Plains situation inside a region where the
    answer is known.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_basis_validation as drv

    rng = np.random.default_rng(0)
    n = 60
    region = pd.DataFrame({
        "pass_strict": [True] * 20 + [False] * (n - 20),
        "pass_relaxed": [True] * 30 + [False] * (n - 30),
        "huc12": [f"h{i // 3}" for i in range(n)],
        "l3": "58", "l3_name": "Test",
        "pctimp2019ws": rng.uniform(0, 5, n), "agriculture_ws": rng.uniform(0, 60, n),
        "rddensws": rng.uniform(0, 4, n), "dor": 0.0, "npdesdensws": 0.0, "mines_ws": 0.0,
    }, index=[f"s{i}" for i in range(n)])
    values = pd.Series(rng.uniform(5, 40, n), index=region.index)

    bases, _clusters = drv.bases_for("bent_EPT_NTAX", region, region, values, RISING)
    ref_ix = set(bases[bv.REFERENCE_BASIS].index)
    assert ref_ix, "the fixture must have a reference pool to hold out"

    for name in (drv.REGIONAL_SCREEN_HOLDOUT, drv.ALL_SITES_HOLDOUT):
        assert name in bases, name
        assert not (set(bases[name].index) & ref_ix), f"{name} leaked a reference station"

    # and the un-held-out screen does overlap, which is the whole point
    assert set(bases[drv.REGIONAL_SCREEN].index) & ref_ix
