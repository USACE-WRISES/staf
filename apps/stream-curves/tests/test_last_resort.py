"""The last resort (REF-17, methodology 0.16, owner decision D19 of 2026-09-28).

A function no source supports in an ecoregion is scored on EASI's national
screening method for the same quantity: surface water storage on watershed
wetland cover, channel and floodplain dynamics on the bank height ratio. The
curves are generated from the vendored EASI catalog; a boundary value lands in
EASI's own class; the rung never touches a function that holds any other curve;
and the promotion gate of D18 refuses a bundle that leaves a function unscored.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from streamcurves import campaign as camp
from streamcurves import curve_basis
from streamcurves import fixed_criteria as fc
from streamcurves import methodology
from streamcurves import pressure_evidence as pe
from streamcurves import reference_pool as rp

DEEP_CLASS = {"Good": "Functioning", "Fair": "Functioning-at-Risk", "Poor": "Non-Functioning"}


# --------------------------------------------------------------------------- #
# the generated curves
# --------------------------------------------------------------------------- #
def test_the_last_resort_block_is_generated_from_the_vendored_catalog():
    assert fc.criteria_drift() == []
    assert fc.last_resort_keys() == ["pctwet2019ws", "bank_height_ratio"]
    # the fixed criteria never include a last-resort metric, so no bundle gains one
    assert not set(fc.metric_keys()) & set(fc.last_resort_keys())
    wet = fc.last_resort_entry("pctwet2019ws")
    assert wet["easi_method"] == "watershed-wetland-extent" and wet["direction"] == "higher_better"
    assert wet["method_basis_class"] == "provisional STAF screening judgment" and wet["provisional"]
    bhr = fc.last_resort_entry("bank_height_ratio")
    assert bhr["easi_method"] == "bhr-bank-instability-susceptibility"
    assert bhr["direction"] == "lower_better" and bhr["domain"][0] == 1.0
    for e in (wet, bhr):
        assert e["rule"] == fc.LAST_RESORT_RULE and e["required_input"] and e["limitation"]
        assert e["citations"] and all(c["text"] for c in e["citations"])
        assert "—" not in json.dumps(e)


@pytest.mark.parametrize("key,values", [
    ("pctwet2019ws", [0.0, 0.5, 0.99, 1.0, 3.0, 5.0, 5.01, 20.0, 100.0]),
    ("bank_height_ratio", [1.0, 1.2, 1.3, 1.31, 1.4, 1.5, 1.51, 2.0, 3.0]),
])
def test_a_boundary_value_lands_in_easis_own_class(key, values):
    """EASI's bands own their boundaries (wetland 1 and 5 percent are Fair; a bank
    height ratio of 1.3 is Good and 1.5 Fair): the curve passes through the DEEP
    cuts so DEEP reads the same class at, just below and just above each one."""
    e = fc.last_resort_entry(key)
    for x in values:
        assert fc.deep_class(fc.interpolate(e, x)) == DEEP_CLASS[fc.class_of(e, x)], (key, x)


def test_zero_wetland_rates_poor_and_the_limitation_says_so():
    e = fc.last_resort_entry("pctwet2019ws")
    assert fc.deep_class(fc.interpolate(e, 0.0)) == "Non-Functioning"
    assert "natural absence from loss" in e["limitation"]


def test_the_basis_label_statement_limit_and_cap():
    b = curve_basis.EASI_SCREENING
    assert curve_basis.resolve(b) == b
    assert curve_basis.label_for(b) == "Adopted from EASI's national screening method (provisional)"
    assert curve_basis.cap_for(b) == 39
    assert curve_basis.statement_for(b) and curve_basis.limit_for(b)
    for text in (curve_basis.label_for(b), curve_basis.statement_for(b), curve_basis.limit_for(b)):
        assert "—" not in text
    # outside the reference ladder: it ranks after every rung
    assert curve_basis.rank(b) == len(curve_basis.ORDER)


def test_the_config_enables_the_rung_and_ranks_it_last():
    assert methodology.last_resort() == {"enabled": True}
    rank = methodology.threshold("metric_portfolio.source_rank", {})
    assert rank["pathway"] > rank["flagged"] > rank["published"]
    assert pe.source_of({"basis": curve_basis.EASI_SCREENING, "status": "published"}) == "pathway"
    assert pe.source_of({"basis": curve_basis.PUBLISHED, "status": "published"}) == "published"


# --------------------------------------------------------------------------- #
# which functions the rung completes
# --------------------------------------------------------------------------- #
def _insufficient(mk: str) -> rp.PoolDecision:
    return rp.PoolDecision(metric=mk, status=rp.STATUS_INSUFFICIENT, level=None, region_code="71",
                           region_name="Interior Plateau", family=None, n_pool=0,
                           n_comparable=0, n_usable=0, n_local=0, n_huc12=0,
                           disposition="insufficient", supported_level=None,
                           transfer_risk=rp.RISK_NONE, transfer_note="", station_ids=(),
                           levels_tried=[])


def test_the_rung_completes_only_a_function_nothing_else_covers():
    got = pe.last_resort_fills(
        "71", "Interior Plateau",
        covered_metrics=["phab_XBKA", "phab_SINU", "bfi"],
        candidate_metrics={"pctwet2019": _insufficient("pctwet2019")},
        configs={}, withheld_statements={"pctwet2019": "every pool is a fallback ramp."})
    assert list(got) == ["pctwet2019ws"]
    fill = got["pctwet2019ws"]
    assert fill["function_id"] == "surface-water-storage"
    assert fill["candidates"] == ["pctwet2019"]
    d = fill["decision"]
    assert d.basis == curve_basis.EASI_SCREENING and d.status == rp.STATUS_PUBLISHED
    assert d.station_ids == () and d.transfer_risk == rp.RISK_NONE
    assert "pctwet2019 (every pool is a fallback ramp)" in fill["attempt"]["why"]
    assert fill["attempt"]["rung"] == "REF-17" and fill["attempt"]["admitted"]
    assert fill["row"]["curve_source"] == "easi_screening_method" and fill["row"]["n_reference"] is None
    assert fill["config"]["higher_is_better"] and fill["config"]["criteria_basis"] == "fixed"


def test_the_channel_function_takes_the_bank_height_ratio_when_both_its_metrics_fail():
    got = pe.last_resort_fills(
        "44", "Nebraska Sand Hills",
        covered_metrics=["pctwet2019"],
        candidate_metrics={"phab_XBKA": _insufficient("phab_XBKA"),
                           "phab_SINU": _insufficient("phab_SINU")},
        configs={})
    assert list(got) == ["bank_height_ratio"]
    assert got["bank_height_ratio"]["function_id"] == "channel-floodplain-dynamics"
    assert got["bank_height_ratio"]["candidates"] == ["phab_SINU", "phab_XBKA"]
    assert got["bank_height_ratio"]["config"]["higher_is_better"] is False


def test_a_carried_curve_or_a_disabled_rung_adds_nothing(monkeypatch):
    carried = {"pctwet2019ws": {"functions": ["surface-water-storage"]},
               "phab_XBKA": {"functions": ["channel-floodplain-dynamics"]}}
    got = pe.last_resort_fills("71", "Interior Plateau", covered_metrics=[],
                               candidate_metrics={}, configs={}, carried=carried)
    assert got == {}
    monkeypatch.setattr(methodology, "last_resort", lambda: {"enabled": False})
    got = pe.last_resort_fills("71", "Interior Plateau", covered_metrics=[],
                               candidate_metrics={}, configs={})
    assert got == {}


def test_the_mapping_row_is_the_methods_own_function():
    base = pd.DataFrame([{"metric_key": "bfi", "discipline": "Hydraulics",
                          "function_label": "Low flow and baseflow dynamics", "sort_order": 4}])
    out = pe._with_last_resort_mapping(base, ["bank_height_ratio"])
    row = out[out["metric_key"] == "bank_height_ratio"].iloc[0]
    assert row["function_label"] == "Channel and floodplain dynamics" and int(row["sort_order"]) == 5


# --------------------------------------------------------------------------- #
# the promotion gate of D18
# --------------------------------------------------------------------------- #
def _bundle(functions, *, excluded=0, entry=None):
    entry = entry or {"metricId": "spring-x", "howToMeasure": "measure it",
                      "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}
    return {"functionCoverage": {"coveredFunctionIds": list(functions), "excluded": excluded},
            "metricsByFunction": [{"functionId": f, "metrics": [entry]} for f in functions]}


def test_the_all_functions_scored_gate(tmp_path, monkeypatch):
    from streamcurves import deep_export as dx
    wanted = [str(f["id"]) for f in dx.deep_read_staf_crosswalk()]
    assert len(wanted) == 20
    policy = camp.load_promotion_policy()
    assert [g["id"] for g in policy["gates"]][-1] == "all-functions-scored"
    assert "all-functions-scored" in camp.RUN_FOLDER_GATES
    vdir = tmp_path / "v1"
    vdir.mkdir()
    monkeypatch.setattr(camp, "staged_version_dir", lambda run_dir, packet=None: vdir)

    def stage(bundle):
        (vdir / camp.BUNDLE_FILE).write_text(json.dumps(bundle), encoding="utf-8")

    stage(_bundle(wanted))
    ok, detail = camp.gate_all_functions_scored(tmp_path)
    assert ok and "all 20 functions" in detail
    stage(_bundle(wanted[:-1]))
    ok, detail = camp.gate_all_functions_scored(tmp_path)
    assert not ok and wanted[-1] in detail
    # a documented gap does not complete a function (D18)
    stage(_bundle(wanted, excluded=1))
    ok, detail = camp.gate_all_functions_scored(tmp_path)
    assert not ok and "documented gaps" in detail
    # a curve with no documented input, or one point, does not score a function
    stage(_bundle(wanted, entry={"metricId": "spring-x", "curve": {"points": [
        {"x": 0, "y": 0}, {"x": 1, "y": 1}]}}))
    ok, detail = camp.gate_all_functions_scored(tmp_path)
    assert not ok and "documented input" in detail
    stage(_bundle(wanted, entry={"metricId": "spring-x", "methodContext": "m",
                                 "curve": {"points": [{"x": 0, "y": 0}]}}))
    assert not camp.gate_all_functions_scored(tmp_path)[0]


# --------------------------------------------------------------------------- #
# DATA-03 never empties a function by itself (methodology 0.16, owner decision D18)
# --------------------------------------------------------------------------- #
def test_the_last_candidate_of_a_function_keeps_its_pool_with_its_missingness_stated():
    """Northern Minnesota Wetlands (49) in the six-region check: embeddedness held a
    validated Level II pool of 25 stations with a value out of 42 comparable, DATA-03
    withheld it, and hyporheic connectivity lost its last metric. The rule keeps the
    candidate with the smallest missing fraction and states the fraction."""
    to_withhold = {"phab_XEMBED": {}, "phab_PCT_SAFN": {}, "phab_XBKA": {}}
    missing = {"phab_XEMBED": {"missing_fraction": 0.405, "n_pool_members": 42, "n_with_value": 25},
               "phab_PCT_SAFN": {"missing_fraction": 0.5, "n_pool_members": 40, "n_with_value": 20},
               "phab_XBKA": {"missing_fraction": 0.45, "n_pool_members": 40, "n_with_value": 22}}
    kept = pe.missingness_kept_for_coverage(
        to_withhold, missing, covered_metrics=["phab_SINU", "phab_LSUB_DMM", "phab_LRBS_use"],
        configs={})
    # embeddedness keeps hyporheic connectivity (and bed composition was covered);
    # sand and fines is not needed once embeddedness keeps it; bank angle's function
    # is covered by sinuosity, so bank angle is withheld as before
    assert kept == {"phab_XEMBED": ["hyporheic-connectivity"]}
    caveat = pe.missingness_kept_caveat(missing["phab_XEMBED"], ["Hyporheic connectivity"], 0.4)
    assert "17 of the pool's 42 comparable stations" in caveat and "25 stations" in caveat
    assert "—" not in caveat
    # with nothing else in the function, the smaller fraction wins the place
    kept = pe.missingness_kept_for_coverage(
        {"phab_XBKA": {}, "phab_SINU": {}},
        {"phab_XBKA": {"missing_fraction": 0.45, "n_with_value": 22},
         "phab_SINU": {"missing_fraction": 0.42, "n_with_value": 24}},
        covered_metrics=[], configs={})
    assert kept == {"phab_SINU": ["channel-floodplain-dynamics"]}
