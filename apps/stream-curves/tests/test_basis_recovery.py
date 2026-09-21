"""The recovery test: can a method put back a reference level hidden from it?

Methodology 0.13's central question. These tests pin the guards that keep the
answer honest, each of which corresponds to a way the experiment could otherwise
flatter itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import basis_recovery as br

RISING = {"column_name": "m", "higher_is_better": True, "curve_form": "monotone",
          "domain_min": 0, "domain_max": None, "display_name": "Rising"}


def _vals(n, lo, hi, seed=3):
    return pd.Series(np.random.default_rng(seed).uniform(lo, hi, n))


# --- what is being recovered -------------------------------------------------
def test_recovery_error_is_scaled_by_the_reference_spread():
    """Absolute error is meaningless across taxa, microsiemens and percent, so the
    error is in units of the true reference interquartile range."""
    truth = (10.0, 20.0)                     # IQR 10
    assert br.recovery_error((10.0, 20.0), truth)["err_abs_mean"] == 0.0
    assert br.recovery_error((15.0, 25.0), truth)["err_abs_mean"] == 0.5
    got = br.recovery_error((5.0, 20.0), truth)
    assert got["err_q25"] == -0.5 and got["err_q75"] == 0.0


def test_a_degenerate_truth_yields_no_error_rather_than_a_divide_by_zero():
    assert br.recovery_error((1.0, 2.0), (5.0, 5.0))["err_abs_mean"] is None
    assert br.recovery_error(None, (1.0, 2.0))["err_abs_mean"] is None


# --- reference variability, matched to sample size ---------------------------
def test_reference_variability_is_measured_at_the_candidates_own_sample_size():
    """A curve on half a pool is less stable than one on the whole pool, so a
    split-half number overstates how variable reference really is and understates
    what a candidate must beat. The draw is the size the candidate was fitted on."""
    ref = _vals(60, 10, 40)
    ev = _vals(40, 5, 45, seed=9)
    small = br.reference_variability(ref, RISING, ev, n_draw=10, n_draws=40)
    large = br.reference_variability(ref, RISING, ev, n_draw=50, n_draws=40)
    assert small["n_draws_valid"] and large["n_draws_valid"]
    assert small["n_draw"] == 10 and large["n_draw"] == 50
    # fewer stations, a less settled answer
    assert small["median"] >= large["median"]


def test_variability_refuses_a_draw_larger_than_the_pool():
    ref = _vals(20, 10, 40)
    assert br.reference_variability(ref, RISING, _vals(20, 10, 40),
                                    n_draw=50, n_draws=10)["median"] is None


def test_exceedance_places_a_candidate_inside_or_outside_reference_variability():
    """The comparison a national median cannot make: is this candidate ordinary
    for a pool of this size, or outside anything reference does on its own?"""
    variability = {"draws": [0.05, 0.10, 0.15, 0.20, 0.25]}
    assert br.exceedance(0.05, variability) == 1.0      # every draw is at least this
    assert br.exceedance(0.30, variability) == 0.0      # nothing reference does
    assert br.exceedance(0.15, variability) == pytest.approx(0.6)
    assert br.exceedance(0.1, {}) is None


# --- selection happens before evaluation -------------------------------------
def test_regions_are_split_before_anything_is_measured_and_the_split_is_stable():
    codes = [str(i) for i in range(20)]
    dev, ev = br.split_regions(codes, seed=11)
    again, _ = br.split_regions(codes, seed=11)
    assert dev == again                       # a seed, not a shuffle per run
    assert not set(dev) & set(ev)
    assert sorted(dev + ev) == sorted(codes)


def test_percentiles_are_calibrated_only_on_the_cells_they_are_given():
    """The rule is learned from development cells; nothing here sees an evaluation
    region. A rule fitted on the region it is scored on is not a method."""
    rng = np.random.default_rng(5)
    cells = []
    for _ in range(6):
        blind = pd.Series(rng.uniform(0, 100, 200))
        truth = (float(blind.quantile(0.60)), float(blind.quantile(0.90)))
        cells.append({"blind": blind, "truth": truth})
    rule = br.calibrate_percentiles(cells, grid=np.round(np.arange(0.05, 1.0, 0.05), 2))
    assert rule["p_low"] == pytest.approx(0.60, abs=0.06)
    assert rule["p_high"] == pytest.approx(0.90, abs=0.06)
    assert rule["dev_err"] < 0.2 and rule["n_dev_cells"] == 6


def test_calibration_with_nothing_to_learn_from_returns_no_rule():
    assert br.calibrate_percentiles([])["p_low"] is None


# --- extrapolation is declared, not hidden -----------------------------------
def test_extrapolation_share_falls_when_the_target_is_unlike_the_clean_training_data():
    """A model asked for the value at zero disturbance, in a setting where nothing
    near zero disturbance was ever observed, is extrapolating. In the smoke test
    every region whose share read 0.00 returned errors of 16 to 59 reference IQRs,
    so this is the gate that catches an unusable prediction in advance.
    """
    rng = np.random.default_rng(1)
    n = 300
    train = pd.DataFrame({
        "drainage_area_sqkm": rng.uniform(1, 100, n), "nhd_slope": rng.uniform(0.001, 0.05, n),
        "tmean8110ws": rng.uniform(2, 12, n), "precip8110ws": rng.uniform(500, 1500, n),
        "bfiws": rng.uniform(20, 80, n),
        "pctimp2019ws": rng.uniform(0, 10, n), "agriculture_ws": rng.uniform(0, 80, n),
        "rddensws": rng.uniform(0, 5, n), "dor": 0.0, "nabd_densws": 0.0,
        "npdesdensws": 0.0, "mines_ws": 0.0})
    like = train.iloc[:40]
    unlike = like.copy()
    unlike["tmean8110ws"] = 40.0            # a climate the clean training data lacks
    nat = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]
    # Five covariates each trimmed to a 5th-to-95th window admit about 0.9**5 of
    # in-range points, so the meaning is in the contrast, not in an absolute level.
    in_range = br.extrapolation_share(train, like, nat)
    out_of_range = br.extrapolation_share(train, unlike, nat)
    assert out_of_range == 0.0
    assert in_range >= 0.4 and in_range > out_of_range


# --- the external labels are not assumed independent -------------------------
def test_label_dependence_reports_how_far_the_label_leans_on_the_metric():
    """EPA's RT_NRSA screen is built partly from chemistry and physical habitat, so
    for those metrics a strong separation is partly the label reading back its own
    input. Measured, not assumed."""
    lab = pd.Series(["R"] * 30 + ["Im"] * 30)
    implicated = pd.Series(list(np.linspace(10, 20, 30)) + list(np.linspace(80, 90, 30)))
    got = br.label_dependence(implicated, lab)
    assert got["n_R"] == 30 and got["n_Im"] == 30
    assert got["raw_auc"] == 0.0            # perfectly separated, one way

    rng = np.random.default_rng(2)
    unrelated = pd.Series(rng.normal(0, 1, 60))
    assert 0.3 < br.label_dependence(unrelated, lab)["raw_auc"] < 0.7


def test_label_dependence_needs_both_groups():
    assert br.label_dependence(pd.Series([1.0] * 20),
                               pd.Series(["R"] * 20))["raw_auc"] is None


# --- pooling across regions can manufacture separation -----------------------
def test_within_region_auc_exposes_what_pooling_hides():
    """Native fish richness pools to 0.402, an inversion, and reads 0.672 inside
    regions: the pooled number was regional offset, not discrimination. Both are
    reported so a reader can tell which they are looking at.
    """
    pts = [{"x": 0.0, "y": 0.0}, {"x": 50.0, "y": 0.5}, {"x": 100.0, "y": 1.0}]
    values, labels, points = {}, {}, {}
    for i, base in enumerate([0.0, 60.0]):          # two regions on different levels
        ix = [f"r{i}s{j}" for j in range(20)]
        # inside each region the reference sites really are higher
        vals = [base + 30 for _ in range(10)] + [base + 5 for _ in range(10)]
        values[i] = pd.Series(vals, index=ix)
        labels[i] = pd.Series(["R"] * 10 + ["Im"] * 10, index=ix)
        points[i] = pts
    got = br.within_region_auc(points, values, labels)
    assert got["n_regions"] == 2
    assert got["median"] == 1.0                     # perfect inside every region


# =========================================================================== #
# Round two (Pre-registration II)
# =========================================================================== #
FALLING = {"column_name": "m", "higher_is_better": False, "curve_form": "monotone",
           "domain_min": 0, "domain_max": None, "display_name": "Falling"}


def _frame(n: int = 60, seed: int = 5) -> pd.DataFrame:
    """A small national frame: two regions, a pressure that matters, clusters."""
    rng = np.random.default_rng(seed)
    rows = []
    for code in ("A", "B"):
        for i in range(n):
            rows.append({"l3": code, "huc12": f"{code}{i // 4}",
                         "drainage_area_sqkm": float(rng.uniform(5, 500)),
                         "nhd_slope": float(rng.uniform(0.001, 0.05)),
                         "tmean8110ws": float(rng.uniform(5, 20)),
                         "precip8110ws": float(rng.uniform(500, 1500)),
                         "bfiws": float(rng.uniform(20, 80)),
                         "agriculture_ws": float(rng.uniform(0, 90)),
                         "pctimp2019ws": float(rng.uniform(0, 10)),
                         **{c: 0.0 for c in br.PRESSURE_COLUMNS
                            if c not in ("agriculture_ws", "pctimp2019ws")}})
    frame = pd.DataFrame(rows)
    frame["pass_strict"] = frame["agriculture_ws"] < 10
    return frame


def _y(frame: pd.DataFrame, seed: int = 6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (30 - 0.2 * frame["agriculture_ws"] + 0.01 * frame["drainage_area_sqkm"]
            + rng.normal(0, 2, len(frame))).to_numpy()


NAT = ["drainage_area_sqkm", "nhd_slope", "tmean8110ws", "precip8110ws", "bfiws"]


# --- the seed of a cell ------------------------------------------------------
def test_a_cell_seed_comes_from_what_the_cell_is_not_when_it_runs():
    """So that running metrics in parallel, or restricting a run to one metric,
    writes the same table as a serial run over all of them."""
    a = br.cell_seed("chem_PTL", "55", "3a_envelope", "I", seed=11)
    assert a == br.cell_seed("chem_PTL", "55", "3a_envelope", "I", seed=11)
    assert a != br.cell_seed("chem_PTL", "55", "3a_envelope", "E", seed=11)
    assert a != br.cell_seed("chem_PTL", "55", "3a_envelope", "I", seed=12)


# --- the response transform --------------------------------------------------
def test_the_transform_is_fixed_by_what_the_quantity_is():
    """Concentrations on log10, pH and counts as measured. Chosen by metric type so
    the choice cannot follow a result."""
    assert br.transform_for("chem_COND") == "log10"
    assert br.transform_for("chem_PTL") == "log10"
    assert br.transform_for("chem_PH") == "identity"
    assert br.transform_for("bent_EPT_NTAX") == "identity"


def test_a_zero_becomes_half_the_smallest_positive_value_and_missing_stays_missing():
    v = pd.Series([0.0, 2.0, 8.0, np.nan])
    assert br.log_offset(v) == 1.0
    got = br.to_scale(v, "log10", br.log_offset(v))
    assert got[0] == pytest.approx(0.0)             # log10(1.0)
    assert np.isnan(got[3])
    assert br.from_scale(br.to_scale(v, "log10", 1.0), "log10")[1] == pytest.approx(2.0)
    assert br.to_scale(v, "identity")[1] == 2.0


# --- the prediction point and the curve --------------------------------------
def test_the_prediction_point_is_the_screened_pools_median_not_zero():
    frame = _frame()
    got = br.reference_pressure_vector(frame)
    ref = frame[frame["pass_strict"]]
    assert got["agriculture_ws"] == pytest.approx(float(ref["agriculture_ws"].median()))
    assert got["agriculture_ws"] > 0                # an observed combination, not zeros


def test_a_curve_from_anchors_carries_exactly_those_quartiles():
    pts = br.curve_from_anchors((5.0, 15.0), RISING)
    xs = [p["x"] for p in pts]
    assert 5.0 in xs and 15.0 in xs


def test_holding_the_synthetic_sample_in_the_domain_changes_no_class_call():
    """Round one's sample ran a full IQR below q25, which goes negative when q25 is
    smaller than the IQR, and the engine then chose its signed seed. That seed
    clipped at zero is the same function on the domain as the non-negative one, so
    no call changed. This pins that, because the two point lists differ."""
    from streamcurves import basis_validation as bv
    lo, hi = 5.0, 15.0
    span = hi - lo
    raw = pd.Series(np.concatenate([np.linspace(lo - span, lo, 25),
                                    np.linspace(lo, hi, 50),
                                    np.linspace(hi, hi + span, 25)]))
    old, _ = bv.curve_for(raw, RISING)
    new = br.curve_from_anchors((lo, hi), RISING)
    grid = pd.Series(np.linspace(0, 30, 200))
    assert bv.calls_for(old, grid) == bv.calls_for(new, grid)


# --- the models --------------------------------------------------------------
@pytest.mark.parametrize("spec", br.MODEL_SPECS)
def test_every_specification_fits_and_predicts(spec):
    frame = _frame()
    model = br.fit_model(frame, _y(frame), NAT, spec=spec, groups=frame["l3"])
    assert model and model["spec"] == spec
    got = br.model_anchors(model, frame[frame["l3"] == "A"],
                           br.reference_pressure_vector(frame), group="A",
                           rng=np.random.default_rng(1))
    assert got["anchors"] and got["anchors"][1] > got["anchors"][0]


def test_an_unknown_specification_is_a_programming_error_not_a_missing_fit():
    frame = _frame()
    with pytest.raises(ValueError):
        br.fit_model(frame, _y(frame), NAT, spec="wishful")


def test_a_fit_with_too_little_data_returns_nothing_rather_than_guessing():
    frame = _frame(n=8)
    assert br.fit_model(frame, _y(frame), NAT, spec="linear", groups=frame["l3"]) is None


def test_fitting_without_pressures_leaves_them_out_of_the_formula():
    frame = _frame()
    model = br.fit_model(frame, _y(frame), NAT, spec="linear", pressures=False,
                         groups=frame["l3"])
    assert model["pressure"] == [] and "agriculture_ws" not in model["formula"]


def test_the_mixed_specification_adds_the_regions_own_level_at_prediction():
    """Round one fitted a region intercept and then predicted the fixed effects
    alone, throwing away the one thing a region without clean streams can still
    supply."""
    frame = _frame()
    y = _y(frame) + np.where(frame["l3"] == "A", 8.0, 0.0)     # A really does sit higher
    model = br.fit_model(frame, y, NAT, spec="mixed_local", groups=frame["l3"])
    target = frame[frame["l3"] == "A"]
    vec = br.reference_pressure_vector(frame)
    with_level = br.predict_model(model, target, vec, group="A")
    without = br.predict_model(model, target, vec, group=None)
    assert with_level["blup"] is not None and with_level["blup"] > 0
    assert np.mean(with_level["mid"]) > np.mean(without["mid"])
    unseen = br.predict_model(model, target, vec, group="never-seen")
    assert unseen["blup"] is None                  # and says so rather than inventing one


def test_predictive_anchors_are_wider_than_the_quartiles_of_fitted_values():
    """The withheld anchors are quartiles of streams, not of their conditional
    means. Round one's estimator dropped the residual spread, which matters most
    where a region's streams share one natural setting."""
    frame = _frame()
    model = br.fit_model(frame, _y(frame), NAT, spec="linear", groups=frame["l3"])
    same = frame[frame["l3"] == "A"].head(6).copy()
    for c in NAT:                                   # one natural setting, six streams
        same[c] = float(same[c].median())
    vec = br.reference_pressure_vector(frame)
    wide = br.model_anchors(model, same, vec, rng=np.random.default_rng(2))["anchors"]
    narrow = br.model_anchors(model, same, vec, fitted_only=True)["anchors"]
    # one setting means one fitted value, so the old estimator has no spread at all
    # and yields no curve; the predictive one still describes the streams
    assert narrow is None and wide is not None and wide[1] > wide[0]
    varied = frame[frame["l3"] == "A"].head(20)
    wide2 = br.model_anchors(model, varied, vec, rng=np.random.default_rng(2))["anchors"]
    narrow2 = br.model_anchors(model, varied, vec, fitted_only=True)["anchors"]
    assert (wide2[1] - wide2[0]) > (narrow2[1] - narrow2[0])


# --- a basis states its own interval ----------------------------------------
def test_a_bootstrap_moves_whole_catchments_and_is_stable_for_a_seed():
    values = pd.Series(np.linspace(1, 100, 40))
    clusters = pd.Series([f"h{i // 4}" for i in range(40)])

    def estimate(pos):
        return br.anchors_of(values.iloc[pos])

    a = br.cluster_bootstrap(estimate, clusters, n_boot=40, rng=np.random.default_rng(3))
    b = br.cluster_bootstrap(estimate, clusters, n_boot=40, rng=np.random.default_rng(3))
    assert a["q25"] == b["q25"] and a["n_boot_valid"] == 40
    assert a["q25"][0] <= values.quantile(0.25) <= a["q25"][1]


def test_a_basis_that_cannot_estimate_states_no_interval():
    """A basis whose anchors fail on more than half its resamples has not stated an
    uncertainty, and Pre-registration II refuses it however well it agrees."""
    clusters = pd.Series([f"h{i // 4}" for i in range(40)])
    got = br.cluster_bootstrap(lambda pos: None, clusters, n_boot=20,
                              rng=np.random.default_rng(3))
    assert got["q25"] is None and got["n_boot_valid"] == 0


# --- the accuracy yardstick --------------------------------------------------
def test_at_the_pools_own_size_the_draws_must_be_taken_with_replacement():
    """Without replacement a draw of the whole pool is the pool, so every draw
    agrees perfectly and no candidate could ever look ordinary. Every model and
    every external criterion is compared at that size."""
    ref = _vals(40, 10, 40)
    ev = _vals(30, 5, 45, seed=9)
    without = br.reference_variability(ref, RISING, ev, n_draw=40, n_draws=30, replace=False)
    with_rep = br.reference_variability(ref, RISING, ev, n_draw=40, n_draws=30, replace=True)
    assert without["p90"] == 0.0 and not any(without["draws"])
    assert with_rep["p90"] > 0.0 and any(with_rep["draws"]) and with_rep["replace"] is True


def test_the_yardstick_is_drawn_at_the_smaller_of_the_candidates_size_and_the_pools():
    ref = _vals(40, 10, 40)
    truth = br.cell_truth(ref, _vals(30, 5, 45, seed=9), RISING, n_boot=30, seed=2)
    small = br.variability_for(truth, 12, n_draws=10)
    full = br.variability_for(truth, None, n_draws=10)
    assert small["n_draw"] == 12 and small["replace"] is False
    assert full["n_draw"] == 40 and full["replace"] is True
    assert br.variability_for(truth, 12, n_draws=10) is small        # cached per size


def test_a_candidate_far_outside_reference_variability_reads_near_zero():
    ref = _vals(40, 10, 40)
    ev = _vals(30, 5, 45, seed=9)
    truth = br.cell_truth(ref, ev, RISING, n_boot=30, seed=2)
    good = br.candidate_record(truth, anchors=truth["anchors"], n_fit=None, n_draws=20)
    bad = br.candidate_record(truth, anchors=(0.5, 1.5), n_fit=None, n_draws=20)
    assert good["exceedance"] == 1.0 and bad["exceedance"] < 0.1


def test_an_external_criterion_is_not_asked_for_anchors_it_does_not_have():
    """A band curve has no quartile anchors, so containment cannot apply to it and
    the record says so rather than recording a false answer."""
    ref = _vals(40, 10, 40)
    truth = br.cell_truth(ref, _vals(30, 5, 45, seed=9), RISING, n_boot=30, seed=2)
    pts = [{"x": 0.0, "y": 0.0}, {"x": 20.0, "y": 0.5}, {"x": 45.0, "y": 1.0}]
    rec = br.candidate_record(truth, points=pts, external=True, n_draws=20)
    assert rec["both_in"] is None and rec["interval_ok"] is None
    assert rec["flip"] is not None and rec["exceedance"] is not None


# --- the domain --------------------------------------------------------------
def test_coverage_by_covariate_agrees_with_the_joint_share_it_decomposes():
    """The joint column has to equal round one's C3 exactly, or the decomposition
    is explaining a different number."""
    frame = _frame()
    train, target = frame[frame["l3"] == "A"], frame[frame["l3"] == "B"]
    got = br.coverage_by_covariate(train, target, NAT)
    assert got["joint"] == br.extrapolation_share(train, target, NAT)
    assert set(got["by"]) == set(NAT)


def test_the_climate_free_share_drops_the_climate_covariates():
    frame = _frame()
    train, target = frame[frame["l3"] == "A"], frame[frame["l3"] == "B"].copy()
    target["tmean8110ws"] = 99.0                    # a climate no clean stream shares
    got = br.coverage_by_covariate(train, target, NAT)
    assert got["joint"] == 0.0 and got["joint_no_climate"] > 0.0


def test_the_disturbance_gap_says_how_far_below_the_region_a_prediction_sits():
    frame = _frame()
    target = frame[frame["l3"] == "A"].copy()
    target["agriculture_ws"] = 40.0                 # nothing cleaner than 40 percent
    got = br.disturbance_gap(target, {"agriculture_ws": 0.0, "pctimp2019ws": 0.0})
    assert got["gap_agriculture_ws"] == 40.0


# --- selection ---------------------------------------------------------------
def test_selection_reports_every_candidate_and_not_only_the_winner():
    errs = {"a": [0.5, 0.4, 0.6], "b": [0.2, 0.3, 0.1], "c": [None, None, None]}
    got = br.select_by_leave_one_out(["a", "b", "c"], [0, 1, 2],
                                     lambda cand, r: errs[cand][r])
    assert got["best"] == "b"
    assert got["table"]["a"]["median"] == 0.5 and got["table"]["c"]["median"] is None


def test_selection_needs_enough_regions_to_have_learned_anything():
    got = br.select_by_leave_one_out(["a"], [0, 1], lambda cand, r: 0.1)
    assert got["best"] is None                      # two regions, three required


# --- verdicts ----------------------------------------------------------------
def _cells(n, *, both, exceed, net, kind="population", interval=True, extrap=None):
    return pd.DataFrame([{"metric": "m", "basis": "b", "regime": "I", "kind": kind,
                          "flip": 0.1, "both_in": both[i], "exceedance": exceed[i],
                          "net_optimism": net[i], "interval_ok": interval,
                          "err_abs_mean": 0.2,
                          "extrapolation_ok": None if extrap is None else extrap[i]}
                         for i in range(n)])


def test_validated_needs_containment_accuracy_and_no_systematic_promotion():
    six = _cells(6, both=[True] * 5 + [False], exceed=[0.5] * 6, net=[0.0] * 6)
    assert br.score_verdicts(six)["verdict"].iloc[0] == br.VALIDATED


def test_promising_is_accuracy_without_containment():
    six = _cells(6, both=[False] * 6, exceed=[0.5] * 6, net=[0.0] * 6)
    assert br.score_verdicts(six)["verdict"].iloc[0] == br.PROMISING


def test_a_systematic_promotion_fails_whatever_the_containment():
    six = _cells(6, both=[True] * 6, exceed=[0.9] * 6, net=[0.3] * 6)
    assert br.score_verdicts(six)["verdict"].iloc[0] == br.UNSUPPORTED


def test_too_few_cells_is_not_a_verdict():
    three = _cells(3, both=[True] * 3, exceed=[0.9] * 3, net=[0.0] * 3)
    assert br.score_verdicts(three)["verdict"].iloc[0] == br.NOT_EVALUATED


def test_a_basis_without_an_interval_cannot_reach_a_tier():
    six = _cells(6, both=[True] * 6, exceed=[0.9] * 6, net=[0.0] * 6, interval=False)
    assert br.score_verdicts(six)["verdict"].iloc[0] == br.NOT_QUANTIFIED


def test_a_model_outside_its_coverage_is_refused_whatever_its_error():
    six = _cells(6, both=[True] * 6, exceed=[0.9] * 6, net=[0.0] * 6, kind="model",
                 extrap=[0.0] * 5 + [0.9])
    got = br.score_verdicts(six).iloc[0]
    assert got["verdict"] == br.UNSUPPORTED_COVERAGE
    assert "covered cells" in got["info"]


def test_an_external_criterion_is_judged_without_containment():
    six = _cells(6, both=[None] * 6, exceed=[0.5] * 6, net=[0.0] * 6, kind="external",
                 interval=None)
    assert br.score_verdicts(six)["verdict"].iloc[0] == br.VALIDATED_EXTERNAL
