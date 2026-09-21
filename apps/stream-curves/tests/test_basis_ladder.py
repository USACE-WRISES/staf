"""The sources after the station pools (methodology 0.14, REF-12 to REF-14):
order, acceptance, and honest refusals."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import acceptance
from streamcurves import basis_ladder as bl
from streamcurves import curve_basis as cb
from streamcurves import published_benchmark as pb
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
CFG = {"bent_HPRIME": {"column_name": "bent_HPRIME", "display_name": "Shannon diversity",
                       "higher_is_better": True, "metric_family": "continuous",
                       "domain_min": 0}}
#: an approved registry entry whose limits reach the synthetic dirty region
REGISTRY = {"procedures": {"mixed_local": {"natural_covariates": ["drainage_area_sqkm", "nhd_slope",
                                                                  "tmean8110ws", "precip8110ws",
                                                                  "bfiws"],
                                           "residual_draws": 50, "bootstrap_refits": 4,
                                           "seed": 11}},
            "requirements": {"min_target_stations": 10},
            "entries": [{"metric": "bent_HPRIME", "procedure": "mixed_local",
                         "status": "approved",
                         "limits": {"min_coverage_share": 0.0,
                                    "max_disturbance_gap": {"agriculture_ws": 100.0,
                                                            "pctimp2019ws": 100.0,
                                                            "rddensws": 100.0}}}]}


def _accepted(transport=None):
    rec = {"verdict": "promising", "n_cells": 19,
           "acceptance": {"n_cells": 19, "accepted": True, "classification_error": True,
                          "directional_bias": True}}
    if transport:
        rec["transport"] = transport
    return rec


def _series(frame, wide, metric="bent_HPRIME"):
    return pd.Series(frame["station_key"].astype(str).map(
        wide.set_index("site_id")[metric]).to_numpy(), index=frame.index)


# --------------------------------------------------------------------------- #
# REF-12: comparable national reference
# --------------------------------------------------------------------------- #
def test_national_donors_need_accepted_evidence_and_say_so_when_there_is_none():
    frame, wide = _frame()
    got = bl.try_national("bent_HPRIME", frame=frame, values=_series(frame, wide),
                          target_l3=DIRTY, validation={}, entry=CFG["bent_HPRIME"])
    assert not got["admitted"]
    assert [o["option"] for o in got["options"]] == ["3c_matched", "3a_envelope"]
    assert "no evidence" in got["why"]


def test_accepted_national_evidence_inside_its_range_admits_the_matched_donors():
    frame, wide = _frame()
    table = {"bent_HPRIME": {"3c_matched": _accepted(
        {"measure": "distance_median", "better": "max", "bound": 10.0})}}
    got = bl.try_national("bent_HPRIME", frame=frame, values=_series(frame, wide),
                          target_l3=DIRTY, validation=table, entry=CFG["bent_HPRIME"])
    assert got["admitted"], got["why"]
    d = got["decisions"] if "decisions" in got else got["decision"]
    assert d.basis == cb.NATIONAL and d.status == rp.STATUS_NATIONAL
    assert d.screen_detail["option"] == "3c_matched"
    assert not any(k.startswith(DIRTY + "-") for k in d.station_ids)


def test_a_national_source_outside_its_tested_range_is_refused():
    frame, wide = _frame()
    table = {"bent_HPRIME": {"3c_matched": _accepted(
        {"measure": "distance_median", "better": "max", "bound": 0.0001})}}
    got = bl.try_national("bent_HPRIME", frame=frame, values=_series(frame, wide),
                          target_l3=DIRTY, validation=table, entry=CFG["bent_HPRIME"])
    assert not got["admitted"]
    assert "further than in any region" in got["why"]


def test_the_donor_pool_never_contains_the_region_it_is_borrowed_for():
    frame, _ = _frame()
    donors = bl.bt.national_reference(frame, exclude_l3=DIRTY)
    assert not (donors["l3"].astype(str) == DIRTY).any()


# --------------------------------------------------------------------------- #
# REF-13: the approved registry only
# --------------------------------------------------------------------------- #
def test_an_approved_specification_inside_its_limits_admits_a_modeled_curve():
    frame, wide = _frame()
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=_series(frame, wide),
                         target_l3=DIRTY, registry=REGISTRY)
    assert got["admitted"], got["why"]
    assert got["decision"].basis == cb.MODELED
    assert got["decision"].screen_detail["interval"]["q25"] is not None


def test_no_registry_entry_is_a_stated_refusal():
    frame, wide = _frame()
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=_series(frame, wide),
                         target_l3=DIRTY, registry={**REGISTRY, "entries": []})
    assert not got["admitted"]
    assert got["why"] == "The model registry holds no approved specification for this metric."


def test_a_target_outside_the_validated_limits_is_refused():
    frame, wide = _frame()
    tight = {**REGISTRY, "entries": [{**REGISTRY["entries"][0], "limits": {
        "min_coverage_share": 0.0, "max_disturbance_gap": {"agriculture_ws": 5.0}}}]}
    got = bl.try_modeled("bent_HPRIME", frame=frame, values=_series(frame, wide),
                         target_l3=DIRTY, registry=tight)
    assert not got["admitted"] and "validated only up to 5.00" in got["why"]


# --------------------------------------------------------------------------- #
# REF-14: the verified catalog
# --------------------------------------------------------------------------- #
def test_a_metric_the_catalog_does_not_hold_is_a_specific_gap_named_as_a_token():
    frame, _ = _frame()
    got = bl.try_published("bent_EPT_NTAX", frame=frame, target_l3=DIRTY)
    assert not got["admitted"] and got["condition"] == ("catalog", "no entry")
    assert got["why"] == pb.NO_CRITERION and "PB-" not in got["why"]


def test_a_catalog_criterion_admits_where_the_region_is_unanimous():
    frame, _ = _frame()
    got = bl.try_published("chem_PTL", frame=frame, target_l3=DIRTY)
    assert got["admitted"] and got["decision"].basis == cb.PUBLISHED
    assert got["decision"].screen_detail["catalogEntry"] == "nrsa-2018-19-total-phosphorus"


# --------------------------------------------------------------------------- #
# the walk
# --------------------------------------------------------------------------- #
def test_the_sources_are_tried_in_order_and_stop_at_the_first_that_passes():
    frame, wide = _frame()
    got = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config=CFG, validation={}, registry=REGISTRY)
    rungs = [a["rung"] for a in got["attempts"] if a["metric"] == "bent_HPRIME"]
    assert rungs == ["REF-12", "REF-13"], "national is tried first, then it stops at modeled"
    assert got["decisions"]["bent_HPRIME"].basis == cb.MODELED
    assert "bent_HPRIME" in got["curve_rows"]


def test_the_rule_codes_are_the_0_14_ones():
    assert bl.RUNGS == ("REF-12", "REF-13", "REF-14")


def test_every_attempt_records_a_reason_admitted_or_not():
    frame, wide = _frame()
    cfg = {mk: {"column_name": mk, "display_name": mk, "higher_is_better": mk != "chem_PTL",
                "metric_family": "continuous", "domain_min": 0}
           for mk in ("bent_HPRIME", "chem_PTL")}
    got = bl.resolve(["bent_HPRIME", "chem_PTL"], frame=frame, values_wide=wide,
                     target_l3=DIRTY, metric_config=cfg, validation={}, registry=REGISTRY)
    assert got["attempts"]
    for a in got["attempts"]:
        assert a["why"], a
    table = bl.attempt_table(got["attempts"])
    assert list(table.columns) == ["metric", "rung", "basis", "admitted", "why"]


def test_a_metric_the_archive_does_not_carry_is_recorded_not_crashed():
    frame, wide = _frame()
    got = bl.resolve(["not_a_metric"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config={}, validation={})
    assert got["decisions"] == {}
    assert got["attempts"][0]["why"]


def test_an_empty_attempt_table_still_has_its_columns():
    assert list(bl.attempt_table([]).columns) == ["metric", "rung", "basis", "admitted", "why"]


def test_the_census_path_agrees_with_the_build_about_what_admits():
    frame, wide = _frame()
    built = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                       metric_config=CFG, validation={}, registry=REGISTRY, fit=True)
    peeked = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                        metric_config=CFG, validation={}, registry=REGISTRY, fit=False)
    assert set(built["decisions"]) == set(peeked["decisions"])
    assert (built["decisions"]["bent_HPRIME"].basis
            == peeked["decisions"]["bent_HPRIME"].basis)
    assert peeked["curve_rows"] == {}, "the cheap path states no curve"


def test_a_ladder_decision_never_claims_the_local_status():
    frame, wide = _frame()
    got = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                     metric_config=CFG, validation={}, registry=REGISTRY)
    d = got["decisions"]["bent_HPRIME"]
    assert d.status in rp.LADDER_STATUSES and d.status != rp.STATUS_LOCAL


# --------------------------------------------------------------------------- #
# the committed evidence
# --------------------------------------------------------------------------- #
def test_the_committed_evidence_is_readable_and_every_verdict_says_whether_it_accepts():
    table = bl.load_validation()
    assert table, "config/basis_validation.yaml should ship with the repo"
    families = table.get(acceptance.FAMILIES_KEY) or {}
    assert families, "the family records are what a thinly tested metric is judged by"
    st = acceptance.settings()
    for mk, by_basis in table.items():
        if mk == acceptance.FAMILIES_KEY:
            continue
        for basis, rec in by_basis.items():
            acc = rec.get("acceptance") or {}
            assert "accepted" in acc, (mk, basis)
            if acc["accepted"]:
                assert rec["n_cells"] >= st["min_cells"], (mk, basis)
                assert rec["verdict"] in ("validated", "promising"), (mk, basis)


def test_the_evidence_names_the_preregistration_it_came_from():
    prov = bl.validation_provenance()
    assert prov.get("preregistration_sha256"), "a verdict with no pre-registration is not evidence"
    assert prov.get("preregistration") == "PREREGISTRATION_IV.md"


def test_a_missing_evidence_file_admits_nothing_rather_than_everything(tmp_path):
    absent = tmp_path / "nope.yaml"
    assert bl.load_validation(absent) == {}
    assert bl.validation_provenance(absent) == {}
