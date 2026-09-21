"""The discrimination check (rule CURVE-12): stations that are not reference
check a curve, they never fit one."""
from __future__ import annotations

import numpy as np
import pytest

from streamcurves import discrimination as dz

HIGHER = {"higher_is_better": True, "metric_family": "continuous"}
LOWER = {"higher_is_better": False, "metric_family": "continuous"}


def _points(values, entry):
    from streamcurves.curve_stability import _build_points
    pts, status = _build_points(values, entry)
    assert pts is not None, status
    return pts


def test_auc_is_the_chance_a_reference_station_outscores_a_pressured_one():
    assert dz.auc([0.9, 0.8, 0.7], [0.1, 0.2, 0.3]) == pytest.approx(1.0)
    assert dz.auc([0.1, 0.2], [0.8, 0.9]) == pytest.approx(0.0)
    assert dz.auc([0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.5)      # ties count half
    assert dz.auc([], [0.5]) is None


@pytest.mark.parametrize("value,verdict", [
    (0.9, dz.VERDICT_DISCRIMINATES), (0.65, dz.VERDICT_DISCRIMINATES),
    (0.6, dz.VERDICT_WEAK), (0.5, dz.VERDICT_NONE), (0.44, dz.VERDICT_INVERTED),
    (None, dz.VERDICT_NOT_EVALUABLE)])
def test_verdicts(value, verdict):
    assert dz.verdict_of(value) == verdict


def test_a_responsive_metric_discriminates():
    rng = np.random.default_rng(11)
    ref = rng.normal(30.0, 4.0, 40)             # EPT-like: reference streams hold more taxa
    pressured = rng.normal(12.0, 4.0, 40)
    rec = dz.curve_discrimination("bent_EPT_NTAX", HIGHER, _points(ref, HIGHER), ref, pressured)
    assert rec["verdict"] == dz.VERDICT_DISCRIMINATES
    assert rec["aucRefVsPressure"] > 0.9
    assert rec["medianRefIndex"] > rec["medianPressureIndex"]
    assert rec["nRef"] == 40 and rec["nPressure"] == 40


def test_a_metric_that_ignores_pressure_does_not():
    rng = np.random.default_rng(12)
    ref = rng.normal(20.0, 5.0, 60)
    pressured = rng.normal(20.0, 5.0, 60)
    rec = dz.curve_discrimination("x", HIGHER, _points(ref, HIGHER), ref, pressured)
    assert rec["verdict"] in (dz.VERDICT_NONE, dz.VERDICT_WEAK)
    assert 0.35 < rec["aucRefVsPressure"] < 0.65


def test_a_backwards_metric_is_called_inverted():
    rng = np.random.default_rng(13)
    ref = rng.normal(10.0, 2.0, 40)             # declared higher-is-better ...
    pressured = rng.normal(25.0, 2.0, 40)       # ... but pressured streams read higher
    rec = dz.curve_discrimination("x", HIGHER, _points(ref, HIGHER), ref, pressured)
    assert rec["verdict"] == dz.VERDICT_INVERTED


def test_lower_is_better_metrics_work_the_same_way():
    rng = np.random.default_rng(14)
    ref = np.abs(rng.normal(8.0, 3.0, 40))      # total phosphorus
    pressured = np.abs(rng.normal(60.0, 20.0, 40))
    rec = dz.curve_discrimination("chem_PTL", LOWER, _points(ref, LOWER), ref, pressured)
    assert rec["verdict"] == dz.VERDICT_DISCRIMINATES


def test_reference_stations_are_scored_leave_one_out():
    """Self-scoring would place the pool's own interquartile range at 0.70 or
    better and flatter every metric alike."""
    rng = np.random.default_rng(15)
    ref = rng.normal(20.0, 5.0, 30)
    loo = dz.loo_indices(ref, HIGHER)
    self_scored = dz.score_values(_points(ref, HIGHER), ref)
    assert loo.notna().sum() == 30
    assert not np.allclose(loo.to_numpy(), self_scored.to_numpy())


def test_too_few_pressured_stations_is_not_evaluable():
    ref = np.linspace(10, 30, 25)
    rec = dz.curve_discrimination("x", HIGHER, _points(ref, HIGHER), ref, [5.0, 6.0])
    assert rec["verdict"] == dz.VERDICT_NOT_EVALUABLE and rec["aucRefVsPressure"] is None
    assert "Too few" in dz.sentence(rec)


def test_the_epa_designation_is_reported_when_enough_stations_carry_it():
    rng = np.random.default_rng(16)
    ref = rng.normal(30.0, 4.0, 30)
    pts = _points(ref, HIGHER)
    rec = dz.curve_discrimination(
        "x", HIGHER, pts, ref, rng.normal(12.0, 4.0, 30),
        rt_values={"R": rng.normal(29.0, 4.0, 8), "Im": rng.normal(11.0, 4.0, 9)})
    assert rec["nR"] == 8 and rec["nIm"] == 9 and rec["aucRvsIm"] > 0.9
    thin = dz.curve_discrimination("x", HIGHER, pts, ref, rng.normal(12.0, 4.0, 30),
                                   rt_values={"R": [29.0, 30.0], "Im": [10.0]})
    assert thin["aucRvsIm"] is None


def test_the_sentence_has_no_em_dash():
    rng = np.random.default_rng(17)
    ref = rng.normal(30.0, 4.0, 30)
    rec = dz.curve_discrimination("x", HIGHER, _points(ref, HIGHER), ref,
                                  rng.normal(12.0, 4.0, 30))
    assert "—" not in dz.sentence(rec) and "separates" in dz.sentence(rec)
