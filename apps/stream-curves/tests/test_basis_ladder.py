"""The ladder above the ecoregion hierarchy: order, gates, and honest refusals."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import basis_ladder as bl
from streamcurves import curve_basis as cb
from streamcurves import reference_pool as rp


def _frame(seed: int = 5, n_per: int = 40, dirty: str = "55"):
    rng = np.random.default_rng(seed)
    rows = []
    codes = [f"{i:02d}" for i in range(1, 9)] + [dirty]
    for code in codes:
        for i in range(n_per):
            ag = rng.uniform(30.0, 80.0) if code == dirty else rng.uniform(0.0, 80.0)
            rows.append({
                "station_key": f"{code}-{i}", "l3": code, "l2": "8.1", "l1": "8",
                "l3_name": f"Region {code}", "huc12": f"{code}{i // 4:03d}",
                "nars9": "TPL" if code == dirty else "CPL",
                "pass_strict": (code != dirty) and ag < 5.0,
                "drainage_area_sqkm": float(rng.uniform(5, 400)),
                "nhd_slope": float(rng.uniform(0.001, 0.03)),
                "tmean8110ws": float(rng.uniform(4, 20)),
                "precip8110ws": float(rng.uniform(700, 1400)),
                "bfiws": float(rng.uniform(30, 70)), "runoffws": float(rng.uniform(100, 900)),
                "lith_group": "glacial_till",
                "agriculture_ws": ag, "pctimp2019ws": float(rng.uniform(0, 8)),
                "rddensws": float(rng.uniform(0, 6)), "dor": 0.0,
                "nabd_densws": 0.0, "npdesdensws": 0.0, "mines_ws": 0.0,
            })
    frame = pd.DataFrame(rows).reset_index(drop=True)
    level = {c: float(v) for c, v in zip(codes, np.linspace(-1.5, 1.5, len(codes)))}
    vals = (20.0 - 0.15 * frame["agriculture_ws"] + frame["l3"].map(level).astype(float)
            + np.random.default_rng(seed + 1).normal(0, 0.8, len(frame)))
    wide = pd.DataFrame({"site_id": frame["station_key"].astype(str),
                         "bent_HPRIME": vals.to_numpy(),
                         "chem_PTL": np.abs(vals.to_numpy()) * 6.0})
    return frame, wide


DIRTY = "55"
#: Validated AND transportable to the synthetic dirty region, the two things the
#: modelled rung needs (REF-09 with Pre-registration II's transportability).
VALIDATED = {"bent_HPRIME": {"1B_mixed_local": {
    "verdict": "validated", "n_cells": 4,
    "transport": {"measure": "extrapolation_ok", "better": "min", "bound": 0.07,
                  "targets": {"55": True}}}}}


# --------------------------------------------------------------------------- #
# the gates
# --------------------------------------------------------------------------- #
def test_only_a_validated_verdict_opens_the_modeled_rung():
    frame, wide = _frame()
    series = pd.Series(frame["station_key"].astype(str).map(
        wide.set_index("site_id")["bent_HPRIME"]).to_numpy(), index=frame.index)
    ok = bl.try_modeled("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                        validation=VALIDATED)
    assert ok["admitted"] and ok["basis"] == cb.MODELED

    for verdict, says in (("promising", "reached promising but not validated"),
                          ("unsupported", "did not pass the recovery test"),
                          ("unsupported by data coverage", "training data did not reach")):
        table = {"bent_HPRIME": {"1B_mixed_local": {"verdict": verdict}}}
        got = bl.try_modeled("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                             validation=table)
        assert not got["admitted"], verdict
        assert says in got["why"], (verdict, got["why"])


def test_a_metric_with_no_recorded_verdict_is_refused_and_says_so():
    frame, wide = _frame()
    series = pd.Series(frame["station_key"].astype(str).map(
        wide.set_index("site_id")["bent_HPRIME"]).to_numpy(), index=frame.index)
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                         validation={})
    assert not got["admitted"]
    assert got["why"] == "No fitted expectation was tested for this metric."


def test_the_national_rung_needs_donors_and_a_demonstration_that_they_transfer():
    """Borrowed donors have never validated anywhere in this programme. The rung
    exists so the ladder records the refusal rather than skipping it."""
    frame, wide = _frame()
    series = pd.Series(frame["station_key"].astype(str).map(
        wide.set_index("site_id")["bent_HPRIME"]).to_numpy(), index=frame.index)
    got = bl.try_national("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                          validation=VALIDATED)
    assert not got["admitted"]
    assert got["why"]


def test_the_donor_pool_never_contains_the_region_it_is_borrowed_for():
    frame, _ = _frame()
    donors = bl.bt.national_reference(frame, exclude_l3=DIRTY)
    assert not (donors["l3"].astype(str) == DIRTY).any()


# --------------------------------------------------------------------------- #
# order
# --------------------------------------------------------------------------- #
def test_the_rungs_are_tried_in_the_registered_order_and_stop_at_the_first():
    frame, wide = _frame()
    got = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config={"bent_HPRIME": {"column_name": "bent_HPRIME",
                                                    "display_name": "Shannon diversity",
                                                    "higher_is_better": True,
                                                    "metric_family": "continuous",
                                                    "domain_min": 0}},
                     validation=VALIDATED)
    rungs = [a["rung"] for a in got["attempts"] if a["metric"] == "bent_HPRIME"]
    assert rungs == ["REF-08", "REF-09"], "national is tried first, then it stops at modeled"
    assert got["decisions"]["bent_HPRIME"].basis == cb.MODELED
    assert "bent_HPRIME" in got["curve_rows"]


def test_bl_rungs_match_the_ladder_order_in_curve_basis():
    assert bl.RUNGS == ("REF-08", "REF-09", "REF-10")


# --------------------------------------------------------------------------- #
# refusals are recorded
# --------------------------------------------------------------------------- #
def test_every_attempt_records_a_reason_admitted_or_not():
    frame, wide = _frame()
    cfg = {mk: {"column_name": mk, "display_name": mk, "higher_is_better": mk != "chem_PTL",
                "metric_family": "continuous", "domain_min": 0}
           for mk in ("bent_HPRIME", "chem_PTL")}
    got = bl.resolve(["bent_HPRIME", "chem_PTL"], frame=frame, values_wide=wide,
                     target_l3=DIRTY, metric_config=cfg, validation=VALIDATED)
    assert got["attempts"]
    for a in got["attempts"]:
        assert a["why"], a
    table = bl.attempt_table(got["attempts"])
    assert list(table.columns) == ["metric", "rung", "basis", "admitted", "why"]
    assert len(table) == len(got["attempts"])


def test_a_metric_the_archive_does_not_carry_is_recorded_not_crashed():
    frame, wide = _frame()
    got = bl.resolve(["not_a_metric"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config={}, validation=VALIDATED)
    assert got["decisions"] == {}
    assert got["attempts"][0]["why"]


def test_an_empty_attempt_table_still_has_its_columns():
    assert list(bl.attempt_table([]).columns) == ["metric", "rung", "basis", "admitted", "why"]


# --------------------------------------------------------------------------- #
# the committed evidence
# --------------------------------------------------------------------------- #
def test_the_committed_evidence_is_readable_and_admits_only_what_it_says():
    table = bl.load_validation()
    assert table, "config/basis_validation.yaml should ship with the repo"
    admitted = {mk for mk, by_basis in table.items()
                for rec in by_basis.values()
                if str(rec.get("verdict")) == "validated"}
    # exactly the two that reached the validated tier
    assert admitted == {"bent_HPRIME", "chem_TURB"}
    for mk, by_basis in table.items():
        for basis, rec in by_basis.items():
            # "not evaluated" means too few regions to judge, and every other
            # verdict was reached on at least the pre-registered minimum
            if rec.get("verdict") == "not evaluated":
                assert rec.get("n_cells", 0) < bl.MIN_CELLS, (mk, basis)
            else:
                assert rec.get("n_cells", 0) >= bl.MIN_CELLS, (mk, basis)


def test_the_evidence_names_the_preregistration_it_came_from():
    prov = bl.validation_provenance()
    assert prov.get("preregistration_sha256"), "a verdict with no pre-registration is not evidence"
    assert prov.get("preregistration")


def test_a_missing_evidence_file_admits_nothing_rather_than_everything(tmp_path):
    absent = tmp_path / "nope.yaml"
    assert bl.load_validation(absent) == {}
    assert bl.validation_provenance(absent) == {}


def test_a_promising_verdict_in_the_evidence_does_not_admit():
    table = bl.load_validation()
    promising = [(mk, b) for mk, by in table.items() for b, r in by.items()
                 if str(r.get("verdict")) == "promising"]
    assert promising, "the record keeps promising results so a refusal can cite them"
    for mk, basis in promising:
        assert not bl._validated(table, mk, basis)


# --------------------------------------------------------------------------- #
# the no-fit path the census uses
# --------------------------------------------------------------------------- #
def test_the_census_path_agrees_with_the_build_about_what_admits():
    frame, wide = _frame()
    cfg = {"bent_HPRIME": {"column_name": "bent_HPRIME", "display_name": "Shannon diversity",
                           "higher_is_better": True, "metric_family": "continuous",
                           "domain_min": 0}}
    built = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                       metric_config=cfg, validation=VALIDATED, fit=True)
    peeked = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                        metric_config=cfg, validation=VALIDATED, fit=False)
    assert set(built["decisions"]) == set(peeked["decisions"])
    assert (built["decisions"]["bent_HPRIME"].basis
            == peeked["decisions"]["bent_HPRIME"].basis)
    assert peeked["curve_rows"] == {}, "the cheap path states no curve"


def test_a_ladder_decision_never_claims_the_local_status():
    """A modelled or published curve rests on no station of this ecoregion, so
    it must not count toward a local pool anywhere downstream."""
    frame, wide = _frame()
    cfg = {"bent_HPRIME": {"column_name": "bent_HPRIME", "display_name": "d",
                           "higher_is_better": True, "metric_family": "continuous",
                           "domain_min": 0}}
    got = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config=cfg, validation=VALIDATED)
    d = got["decisions"]["bent_HPRIME"]
    assert d.status in rp.LADDER_STATUSES
    assert d.status != rp.STATUS_LOCAL
    assert d.n_local == 0 or d.basis != cb.PUBLISHED



# --------------------------------------------------------------------------- #
# Pre-registration II's transportability condition (REF-08, REF-09)
# --------------------------------------------------------------------------- #
def _series(frame, wide, metric="bent_HPRIME"):
    return pd.Series(frame["station_key"].astype(str).map(
        wide.set_index("site_id")[metric]).to_numpy(), index=frame.index)


def test_a_validated_basis_is_refused_where_it_was_never_tested():
    """A basis that passed in the evaluation regions is recommended for a target
    only when the target sits inside the range the passing cells spanned."""
    frame, wide = _frame()
    outside = {"bent_HPRIME": {"1B_mixed_local": {
        "verdict": "validated", "n_cells": 4,
        "transport": {"measure": "extrapolation_ok", "better": "min", "bound": 0.30,
                      "targets": {"55": False}}}}}
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=_series(frame, wide),
                         target_l3=DIRTY, validation=outside)
    assert not got["admitted"]
    assert "never tested in conditions like these" in got["why"]
    assert "at least 0.3" in got["why"]


def test_a_target_the_evaluation_never_placed_is_refused_not_assumed():
    """The rung admits on a positive finding. No transport record for this
    target is an absence of evidence, and absence is refused."""
    frame, wide = _frame()
    unplaced = {"bent_HPRIME": {"1B_mixed_local": {"verdict": "validated", "n_cells": 4}}}
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=_series(frame, wide),
                         target_l3=DIRTY, validation=unplaced)
    assert not got["admitted"]
    assert "never evaluated" in got["why"]


def test_the_committed_evidence_places_every_validated_basis_at_its_target():
    """Both validated bases were evaluated for transport to the Eastern Corn Belt
    Plains, and both are inside the range they passed in."""
    table = bl.load_validation()
    for metric in ("bent_HPRIME", "chem_TURB"):
        rec = table[metric]["1B_mixed_local"]
        assert rec["verdict"] == "validated"
        assert rec["transport"]["targets"]["55"] is True, metric



# --------------------------------------------------------------------------- #
# a refusal says what the recovery test found (2026-09-21)
# --------------------------------------------------------------------------- #
def test_a_failed_test_and_an_untested_metric_are_different_blockers():
    """The first 0.13 build said "reached 'not evaluated'" for every withheld
    metric, including five the test had run and failed, because the evidence
    file kept only validated and promising verdicts. The file now keeps every
    verdict and the sentence follows it."""
    table = bl.load_validation()
    assert table["fish_NAT_TOTLNTAX"]["1B_mixed_local"]["verdict"] == "unsupported"
    assert (bl._modeled_refusal(table["fish_NAT_TOTLNTAX"]["1B_mixed_local"])
            == "The fitted expectation did not pass the recovery test for this metric.")
    too_few = table["bent_TOLRPIND"]["1B_mixed_local"]
    assert too_few["verdict"] == "not evaluated" and too_few["n_cells"] == 2
    assert bl._modeled_refusal(too_few) == (
        "Only 2 evaluation regions could test a fitted expectation for this metric, fewer "
        "than the 4 the recovery test needs.")
    assert "not evaluated" not in bl._modeled_refusal(too_few)
    assert bl._national_refusal(17, table["bent_EPT_NTAX"]["3a_envelope"]).endswith(
        "did not pass the recovery test for it, so they are not shown to transfer to this "
        "ecoregion.")


def test_every_verdict_reads_as_a_sentence_and_never_as_a_token():
    for verdict in ("promising", "unsupported", "unsupported by data coverage",
                    "not quantified", "not evaluated", None):
        rec = None if verdict is None else {"verdict": verdict, "n_cells": 1}
        for why in (bl._modeled_refusal(rec), bl._national_refusal(12, rec)):
            assert why.endswith(".") and "'" not in why, why
            assert "documented and held" not in why
    assert bl._modeled_refusal({"verdict": "not evaluated", "n_cells": 1}).startswith(
        "Only 1 evaluation region could")


def test_one_donor_is_counted_in_the_singular():
    assert bl._donors(1) == "1 comparable national donor carries this metric"
    assert bl._donors(6) == "6 comparable national donors carry this metric"


def test_a_refused_benchmark_names_its_condition_as_a_token_not_in_the_sentence():
    frame, wide = _frame()
    got = bl.try_published("bent_EPT_NTAX", frame=frame, target_l3=DIRTY)
    assert not got["admitted"] and got["condition"] == "PB-1"
    assert "PB-" not in got["why"] and got["why"]
    res = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config={}, validation={}, fit=False)
    pub = [a for a in res["attempts"] if a["rung"] == "REF-10"]
    assert pub and pub[0]["condition"] == "PB-1" and "PB-" not in pub[0]["why"]
