"""The reliable-member selection and the refit comparison of E5b (WP-R6c) on synthetic
members and a synthetic K2b quality table: the masks, the counts by class, the shipped fit
rules on the reliable members, the stated materiality rule and the assembled set."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves.easi_method import fit_recipe as fr
from streamcurves.easi_method import refit, reliable


def _quality(rows: list[dict]) -> pd.DataFrame:
    base = {"xs_status": "ok", "flags": "", "low_quality_tokens": "", "er": 2.0, "bhr": 1.0, "er_sections": 9,
            "bhr_sections": 9, "bankfull_extrapolated": False, "detection": "unknown"}
    frame = pd.DataFrame([{**base, **r} for r in rows])
    return frame.set_index("comid", drop=False)


def test_reliability_masks_read_the_k2b_flags_and_the_three_rules():
    q = _quality([
        {"comid": 1},                                                                    # clean
        {"comid": 2, "flags": "low_quality", "low_quality_tokens": "bankfull_extrapolated", "bankfull_extrapolated": True},
        {"comid": 3, "flags": "low_quality", "low_quality_tokens": "cap_unreachable"},   # three rules pass, K2b fails
        {"comid": 4, "flags": "low_quality", "low_quality_tokens": "few_sections:bhr,few_sections:er",
         "er_sections": 2, "bhr_sections": 2},
        {"comid": 5, "flags": "out_of_range_er", "er": 0.8},                             # impossible ER only
        {"comid": 6, "xs_status": "empty", "er": None, "bhr": None},
        {"comid": 7, "detection": "crest_scan", "flags": "low_quality", "low_quality_tokens": "crest_scan"},
    ])
    er = reliable.reliability(q, "er_median")
    assert list(er["has"]) == [True, True, True, True, True, False, True]
    assert list(er["three_rules"]) == [True, False, True, False, True, False, False]
    assert list(er["k2b"]) == [True, False, False, False, False, False, False]
    bhr = reliable.reliability(q, "bhr_median")
    assert list(bhr["k2b"]) == [True, False, False, False, True, False, False]   # the ER flag never withholds the BHR


def _members(n_per: dict) -> pd.DataFrame:
    rows, comid = [], 1
    for (slope, da), n in n_per.items():
        for _ in range(n):
            rows.append({"comid": comid, "huc12": f"h{comid}", "level": "national", "stratum": "national:national",
                         "slope_class": slope, "da_class": da, "fcode_class": "stream", "screen": "strict",
                         "panel_tier": "complete", "state": "XX", "in_scored_set": True, "totdasqkm": 5.0, "streamorde": 2})
            comid += 1
    rows.append({**rows[-1], "comid": comid, "level": "nars9", "stratum": "nars9:CPL"})
    return pd.DataFrame(rows)


def test_select_members_keeps_the_k2b_reliable_national_members_and_counts_by_class():
    members = _members({("lt_0.5", "le_10"): 4, ("ge_2", "gt_100"): 3})
    rng = np.random.default_rng(3)
    q = _quality([{"comid": c, "er": float(v)} for c, v in zip(range(1, 8), rng.uniform(1.2, 3.0, 7))])
    q.loc[2, ["flags", "low_quality_tokens", "bankfull_extrapolated"]] = ["low_quality", "bankfull_extrapolated", True]
    q.loc[5, ["flags", "low_quality_tokens"]] = ["low_quality", "cap_unreachable"]
    kept, counts = reliable.select_members(members, q, "er_median")
    assert sorted(kept["comid"]) == [1, 3, 4, 6, 7] and set(kept["level"]) == {"national"}
    assert counts["total"] == {"members": 7, "with_ratio": 7, "three_rules": 6, "k2b": 5}
    assert counts["by_slope_class"]["lt_0.5"] == {"members": 4, "with_ratio": 4, "three_rules": 3, "k2b": 3}
    assert counts["by_slope_class"]["ge_2"] == {"members": 3, "with_ratio": 3, "three_rules": 3, "k2b": 2}
    assert counts["by_da_class"]["le_10"]["k2b"] == 3 and counts["by_da_class"]["gt_100"]["k2b"] == 2
    assert counts["by_slope_class_and_da_class"]["ge_2|gt_100"]["members"] == 3
    # a member the quality table does not know carries no ratio
    members2 = pd.concat([members, pd.DataFrame([{**members.iloc[0].to_dict(), "comid": 99}])], ignore_index=True)
    _, counts2 = reliable.select_members(members2, q, "er_median")
    assert counts2["total"] == {"members": 8, "with_ratio": 7, "three_rules": 6, "k2b": 5}


def test_the_materiality_rule_reads_the_acc04_shift_and_the_rounding():
    shipped = {"n": 100, "nMembers": 120, "q25": 1.4, "q50": 1.6, "q75": 2.0, "x39": 0.80, "x69": 1.40, "status": "complete",
               "panelTier": "complete"}
    within = {"n": 60, "n_members": 60, "q25": 1.41, "q50": 1.62, "q75": 2.1, "x39": 0.805, "x69": 1.405, "status": "complete",
              "usable": True, "reason": "", "panel_tier": "complete", "rho_pressure": 0.1}
    c = reliable.compare_curve(shipped, within)
    assert c["iqr_shipped"] == pytest.approx(0.6) and c["allowance_iqr"] == pytest.approx(0.12)
    assert c["material"] is False and c["reasons"] == [] and c["deltas"]["q75"] == pytest.approx(0.1)
    quartile = {**within, "q75": 2.13}                       # 0.13 > 0.12
    c = reliable.compare_curve(shipped, quartile)
    assert c["material"] and any("ACC-04" in r for r in c["reasons"])
    boundary = {**within, "x69": 1.41}                       # exactly the rounding
    c = reliable.compare_curve(shipped, boundary)
    assert c["material"] and any("class boundary x69" in r for r in c["reasons"])
    unusable = {**quartile, "usable": False, "reason": "censored at the 2.0 cap"}
    c = reliable.compare_curve(shipped, unusable)
    assert c["material"] is False and "not usable" in c["reasons"][0]
    c = reliable.compare_curve(shipped, None)
    assert c["material"] is False and c["refit"] is None
    # the set-level comparison names the material curves and any stratum only the refit has
    fit = {"rows": [{**quartile, "quantity": "er_median", "level": "national", "stratum": "national:national", "split": "lt_0.5"},
                    {**within, "quantity": "er_median", "level": "national", "stratum": "national:national", "split": "ge_2"}],
           "national_entrenchment": {**within, "quantity": "er_median", "level": "national", "stratum": "national:national",
                                     "split": ""}}
    shipped_set = {"quantity": "er_median", "stratifier": "slope_class", "higherIsBetter": True,
                   "curves": {"lt_0.5": shipped, "ge_2": shipped, "national": shipped}}
    comparison = reliable.compare_entrenchment(shipped_set, fit)
    assert comparison["material"] and comparison["material_curves"] == ["lt_0.5"] and comparison["refit_only_strata"] == []
    assert "ACC-04" in comparison["rule"] and sorted(comparison["curves"]) == ["ge_2", "lt_0.5", "national"]


def test_the_reliable_fit_runs_the_shipped_rules_and_assembles_the_set():
    rng = np.random.default_rng(11)
    members = _members({("lt_0.5", "le_10"): 60, ("0.5_to_2", "le_10"): 60, ("ge_2", "gt_100"): 60})
    comids = members.loc[members["level"] == "national", "comid"].to_numpy()
    values = pd.DataFrame({"comid": comids, "er_median": rng.lognormal(0.5, 0.3, len(comids)),
                           "bhr_median": np.minimum(2.0, rng.lognormal(0.2, 0.4, len(comids))),
                           "composite_pressure": rng.uniform(0, 1, len(comids)),
                           "screen__pctimp2019ws": rng.uniform(0, 1, len(comids)), "screen__rddensws": rng.uniform(0, 2, len(comids))})
    panels = pd.DataFrame([{"level": "national", "stratum": "national:national", "panel_tier": "complete", "screen": "strict"},
                           {"level": "nars9", "stratum": "nars9:CPL", "panel_tier": "none", "screen": "none"}])
    fit = reliable.fit_reliable(members[members["level"] == "national"], values, panels)
    keys = sorted((r["quantity"], r["split"]) for r in fit["rows"])
    assert keys == sorted([(q, s) for q in ("er_median", "bhr_median") for s in ("lt_0.5", "0.5_to_2", "ge_2")])
    assert fit["national_entrenchment"]["n"] == 180 and fit["national_entrenchment"]["quantity"] == "er_median"
    definition = reliable.assemble_entrenchment(fit)
    assert definition["quantity"] == "er_median" and definition["stratifier"] == "slope_class" and definition["higherIsBetter"]
    assert "national" in definition["curves"]
    for curve in definition["curves"].values():
        assert set(curve) >= {"points", "n", "nMembers", "q25", "q50", "q75", "x39", "x69", "status", "panelTier", "screen"}
        assert len(curve["points"]) >= 2
    evaluation = reliable.evaluate_bhr(fit)
    assert set(evaluation["fits"]) == {"lt_0.5", "0.5_to_2", "ge_2"} and evaluation["bands"] == [1.3, 1.5]
    for entry in evaluation["fits"].values():
        assert (entry["boundary_vs_bands"] is None) == (not entry["usable"])
    assert fr.QUANTITIES["bhr_median"].cap == 2.0 and refit.SLOPE_CLASSES == ("lt_0.5", "0.5_to_2", "ge_2")


def test_write_curve_sets_names_the_set_only_when_material(tmp_path):
    import json
    path = reliable.write_curve_sets(tmp_path / "curve-sets.json", entrenchment=None, provenance={"material": False})
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["sets"] == {} and doc["provenance"] == {"material": False} and doc["schema"] == "staf-easi-candidate-curves"
    definition = {"higherIsBetter": True, "quantity": "er_median", "stratifier": "slope_class", "curves": {"national": {"n": 1}}}
    path = reliable.write_curve_sets(tmp_path / "curve-sets.json", entrenchment=definition, provenance={"material": True})
    assert json.loads(path.read_text(encoding="utf-8"))["sets"] == {"entrenchment": definition}
    text = path.read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
