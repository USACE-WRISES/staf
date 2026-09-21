"""REF-09: a reference expectation for an ecoregion with no reference stream."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import basis_recovery as br
from streamcurves import curve_basis as cb
from streamcurves import curves as cv
from streamcurves import modeled_reference as mr


# --------------------------------------------------------------------------- #
# a small synthetic country: eight clean ecoregions and one that is not
# --------------------------------------------------------------------------- #
def _frame(seed: int = 3, n_per: int = 40, dirty: str = "55") -> tuple[pd.DataFrame, pd.Series]:
    """Regions "01".."08" span the disturbance gradient; region ``dirty`` holds
    nothing below 30 percent agriculture, the way the Eastern Corn Belt Plains
    holds nothing below 30.7."""
    rng = np.random.default_rng(seed)
    rows = []
    codes = [f"{i:02d}" for i in range(1, 9)] + [dirty]
    for code in codes:
        for i in range(n_per):
            ag = (rng.uniform(30.0, 80.0) if code == dirty else rng.uniform(0.0, 80.0))
            rows.append({
                "station_key": f"{code}-{i}", "l3": code, "l2": "8.1", "l1": "8",
                "l3_name": f"Region {code}", "huc12": f"{code}{i // 4:03d}",
                "pass_strict": (code != dirty) and ag < 5.0,
                "drainage_area_sqkm": float(rng.uniform(5, 400)),
                "nhd_slope": float(rng.uniform(0.001, 0.03)),
                "tmean8110ws": float(rng.uniform(4, 20)),
                "precip8110ws": float(rng.uniform(700, 1400)),
                "bfiws": float(rng.uniform(30, 70)),
                "agriculture_ws": ag, "pctimp2019ws": float(rng.uniform(0, 8)),
                "rddensws": float(rng.uniform(0, 6)), "dor": 0.0,
                "nabd_densws": 0.0, "npdesdensws": 0.0, "mines_ws": 0.0,
            })
    frame = pd.DataFrame(rows).reset_index(drop=True)
    # a real response: the metric falls with agriculture, and each region has its
    # own level, which is exactly what the specification claims to recover
    level = {c: float(v) for c, v in zip(codes, np.linspace(-1.5, 1.5, len(codes)))}
    values = pd.Series(
        20.0 - 0.15 * frame["agriculture_ws"]
        + frame["l3"].map(level).astype(float)
        + np.random.default_rng(seed + 1).normal(0, 0.8, len(frame)),
        index=frame.index)
    return frame, values


DIRTY = "55"


# --------------------------------------------------------------------------- #
# what the model is allowed to learn from
# --------------------------------------------------------------------------- #
def test_the_target_never_trains_on_its_own_reference_stations():
    frame, _ = _frame()
    train = mr.training_frame(frame, "01")
    local = train[train["l3"] == "01"]
    assert len(local), "the target must still contribute its disturbed stations"
    assert not local["pass_strict"].any(), "and none of its reference ones"


def test_every_other_ecoregion_contributes_everything_it_has():
    frame, _ = _frame()
    train = mr.training_frame(frame, "01")
    other = frame[frame["l3"] != "01"]
    assert len(train[train["l3"] != "01"]) == len(other)


def test_a_region_with_no_clean_stream_still_contributes_its_level():
    frame, _ = _frame()
    train = mr.training_frame(frame, DIRTY)
    assert len(train[train["l3"] == DIRTY]) == 40, "all of them; none is reference"


# --------------------------------------------------------------------------- #
# the population it produces
# --------------------------------------------------------------------------- #
def test_it_produces_a_population_a_curve_can_be_built_from():
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    assert got["values"] is not None and len(got["values"]) > 100
    assert got["anchors"] and got["anchors"][1] > got["anchors"][0]
    assert got["n_train"] > 0
    assert got["blup"] is not None, "the target's own level must be estimated and used"


def test_the_population_is_what_the_validated_estimator_measured():
    """The harness scored anchors from ``model_anchors``. A curve built from this
    module's population must therefore have the same anchors, or the thing
    published is not the thing validated."""
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    train = mr.training_frame(frame, DIRTY)
    y = values.reindex(train.index)
    keep = y.notna().to_numpy()
    train = train[keep]
    model = br.fit_model(train, y[keep].to_numpy(), mr.NATURAL, spec=mr.SPEC,
                         kind=br.transform_for("bent_HPRIME"),
                         offset=br.log_offset(values), groups=train["l3"].astype(str))
    region = frame[frame["l3"] == DIRTY]
    rng = np.random.default_rng(br.cell_seed("bent_HPRIME", DIRTY, mr.SPEC, "production", seed=11))
    direct = br.model_anchors(model, region, br.reference_pressure_vector(frame),
                              group=DIRTY, rng=rng, n_resid=mr.N_RESID)["anchors"]
    assert got["anchors"] == pytest.approx(direct, rel=1e-9)


def test_the_curve_is_built_by_the_ordinary_engine_not_a_special_case():
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    cfg = {"bent_HPRIME": {"column_name": "bent_HPRIME", "display_name": "Shannon diversity",
                           "higher_is_better": True, "metric_family": "continuous",
                           "domain_min": 0}}
    data = pd.DataFrame({"bent_HPRIME": got["values"]})
    built = cv.build_reference_curve(data, "bent_HPRIME", cfg)
    row = built["curve_row"].iloc[0]
    assert row["curve_status"] == "complete"
    pts = cv.normalize_reference_curve_points(built["curve_points"])
    assert len(pts) >= 3
    ys = pts["index_score"].astype(float).tolist()
    xs = pts["metric_value"].astype(float).tolist()
    assert min(ys) >= 0.0 and max(ys) <= 1.0
    assert xs == sorted(xs)
    # the curve's own quartiles ARE the population's quartiles: no special case
    assert float(row["n_reference"]) == float(len(got["values"]))
    assert (float(row["q25"]), float(row["q75"])) == pytest.approx(got["anchors"], rel=1e-9)


def test_it_reports_how_far_below_the_regions_cleanest_stream_it_predicted():
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    gap = got["gap"]["gap_agriculture_ws"]
    assert gap > 25, "the synthetic dirty region starts at 30 percent agriculture"
    clean = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3="01")
    assert clean["gap"]["gap_agriculture_ws"] <= gap


def test_the_estimate_recovers_a_level_it_was_never_shown():
    """The synthetic truth is known: region 55's reference level at zero
    agriculture. The model never sees a clean stream there and still has to
    land near it, which is the whole claim of the rung."""
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    level = float(np.linspace(-1.5, 1.5, 9)[-1])
    truth = 20.0 + level
    lo, hi = got["anchors"]
    assert lo < truth < hi + 1.5, (lo, hi, truth)


def test_determinism():
    frame, values = _frame()
    a = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    b = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    assert a["anchors"] == b["anchors"]
    assert a["values"].equals(b["values"])


# --------------------------------------------------------------------------- #
# only a validated metric may use this rung
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("verdict,admitted", [
    ("validated", True), ("Validated", True),
    ("promising", False), ("unsupported", False),
    ("unsupported by data coverage", False), ("not evaluated", False), (None, False)])
def test_only_a_validated_verdict_admits_the_rung(verdict, admitted):
    table = {"bent_HPRIME": {"verdict": verdict}} if verdict is not None else {}
    assert mr.validated_for(table, "bent_HPRIME") is admitted


def test_a_metric_absent_from_the_evidence_is_not_admitted():
    assert not mr.validated_for({}, "chem_PTL")
    assert not mr.validated_for(None, "chem_PTL")


# --------------------------------------------------------------------------- #
# what it claims
# --------------------------------------------------------------------------- #
def test_the_decision_counts_stations_not_residual_draws():
    """The synthetic sample runs to hundreds of thousands of values because each
    station is drawn 200 times. Reporting that as sample size would overstate
    the evidence by a factor of 200."""
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    d = mr.pool_decision("bent_HPRIME", got, region_name="Region 55")
    assert d.basis == cb.MODELED
    assert d.n_usable == got["n_train"] <= len(frame)
    assert d.n_usable < len(got["values"])
    assert d.disposition == "exploratory"


def test_the_note_says_it_is_an_extrapolation_and_names_the_distance():
    frame, values = _frame()
    got = mr.modeled_population("bent_HPRIME", frame=frame, values=values, target_l3=DIRTY)
    note = mr.pool_decision("bent_HPRIME", got).transfer_note
    assert "extrapolation" in note
    assert "no stream in this ecoregion is clean enough" in note.lower()
    # said in words a reader can check: where it was evaluated and how far off
    assert "median pressure of the national reference streams" in note
    assert "percentage points more agricultural cover" in note
    assert "vector" not in note and "Its own level" not in note
