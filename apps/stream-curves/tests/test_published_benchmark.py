"""REF-10: a published criterion is admitted on fitness, not on agreement."""
from __future__ import annotations

import pytest

from streamcurves import curve_basis as cb
from streamcurves import published_benchmark as pb


# --------------------------------------------------------------------------- #
# PB-1 to PB-5
# --------------------------------------------------------------------------- #
def test_the_two_nutrients_the_catalog_actually_keys_by_region_are_admissible():
    for metric in ("chem_PTL", "chem_NTL"):
        got = pb.fitness(metric, region="TPL")
        assert got["admissible"], (metric, got["conditions"])
        assert set(got["conditions"]) == set(pb.PB_CONDITIONS)


def test_a_failing_condition_names_itself_rather_than_failing_silently():
    got = pb.fitness("chem_TURB", region="TPL")
    assert not got["admissible"]
    for cond in got["conditions"].values():
        assert cond["why"], "a refusal has to say why"


def test_total_nitrogen_passes_where_dissolved_nitrogen_fails_on_the_fraction():
    """Pre-registration II recorded that the archive held no total-nitrogen
    metric, so the nitrogen criterion was never tried. It holds both, and only
    the total fraction matches what Table 7-1 is written for."""
    assert pb.fitness("chem_NTL", region="TPL")["admissible"]
    got = pb.fitness("chem_NTL_DISS", region="TPL")
    assert not got["admissible"]
    assert "fraction" in got["conditions"]["PB-1"]["why"].lower()


def test_no_published_biological_criterion_in_this_repo_is_computable_from_nrsa():
    """Michigan Procedure 51, the Carolina IBI scores and the Wisconsin and
    Minnesota IBIs are state field indices, not taxa counts. This is the named
    blocker for Population support, so it is pinned rather than assumed."""
    for metric in ("fish_NAT_TOTLNTAX", "fish_NAT_NTOLNTAX", "bent_TOTLNTAX"):
        got = pb.fitness(metric, region="TPL")
        assert not got["admissible"], metric
        assert "state-specific field index" in got["conditions"]["PB-1"]["why"]


def test_a_region_the_criterion_does_not_key_fails_pb3():
    got = pb.fitness("chem_PTL", region="NOT-A-REGION")
    assert not got["admissible"]
    assert not got["conditions"]["PB-3"]["pass"]
    assert got["conditions"]["PB-1"]["pass"], "the parameter still matches; only the region fails"


def test_a_target_split_between_regions_has_no_single_applicable_criterion():
    import pandas as pd
    split = pd.DataFrame({"nars9": ["TPL"] * 6 + ["SAP"] * 4})
    got = pb.fitness("chem_PTL", frame=split)
    assert got["region"] == "TPL"
    assert not got["conditions"]["PB-3"]["pass"], "60 percent is not unanimous"
    unanimous = pd.DataFrame({"nars9": ["TPL"] * 10})
    assert pb.fitness("chem_PTL", frame=unanimous)["conditions"]["PB-3"]["pass"]


def test_pb5_carries_the_catalogs_own_provenance_rather_than_inventing_it():
    prov = pb.provenance("chem_NTL")
    assert prov["citations"], "a benchmark with no citation is not admissible"
    assert prov["sourceTier"]
    assert prov["fraction"] == "total"
    assert "mg N/L" in prov["unitConversion"]


# --------------------------------------------------------------------------- #
# the numbers themselves
# --------------------------------------------------------------------------- #
def test_the_bands_are_table_7_1_on_the_metrics_own_scale():
    # ECBP is NARS-9 region TPL. Table 7-1 reports mg/L; chem_PTL is stored ug/L.
    assert pb.bands("chem_PTL", "TPL") == pytest.approx((88.6, 143.0))
    # chem_NTL is already mg N/L, so nitrogen needs no conversion at all
    assert pb.bands("chem_NTL", "TPL") == pytest.approx((0.7, 1.274))


def test_the_region_is_not_a_formality():
    """Interior Plateau is SAP, not TPL. SAP's phosphorus bands are about six
    times stricter, so applying the wrong region's criterion would rate nearly
    every stream Poor. PB-3 exists for this."""
    tpl = pb.bands("chem_PTL", "TPL")
    sap = pb.bands("chem_PTL", "SAP")
    assert sap[0] < tpl[0] / 5


def test_a_benchmark_curve_is_built_by_the_same_code_path_as_every_other():
    pts = pb.curve_points("chem_PTL", "TPL")
    assert pts and len(pts) >= 3
    xs = [p["x"] for p in pts]
    ys = [p["y"] for p in pts]
    assert xs == sorted(xs), "x must be non-decreasing"
    assert ys[0] == 1.0 and ys[-1] == 0.0, "higher phosphorus is worse"
    assert all(0.0 <= y <= 1.0 for y in ys)
    lo, hi = pb.bands("chem_PTL", "TPL")
    # the two breakpoints sit at the DEEP class boundaries
    assert any(abs(p["x"] - lo) < 1.0 and abs(p["y"] - 0.69) < 1e-6 for p in pts)
    assert any(abs(p["x"] - hi) < 1.0 and abs(p["y"] - 0.39) < 1e-6 for p in pts)


def test_an_unkeyed_region_yields_no_curve_rather_than_a_default_one():
    assert pb.curve_points("chem_PTL", "NOT-A-REGION") is None
    assert pb.curve_points("chem_TURB", "TPL") is None


# --------------------------------------------------------------------------- #
# what it claims
# --------------------------------------------------------------------------- #
def test_the_decision_claims_no_station_of_this_ecoregion():
    d = pb.pool_decision("chem_PTL", "TPL", region_code="55",
                         region_name="Eastern Corn Belt Plains")
    assert d.basis == cb.PUBLISHED
    assert (d.n_pool, d.n_usable, d.n_local) == (0, 0, 0)
    assert "published criterion" in d.transfer_note
    assert "need not match" in d.transfer_note


def test_agreement_is_measured_but_does_not_gate_admission():
    """Round two refused these bands because they disagree with reference
    curves in 18 of 19 regions. Under REF-10 that disagreement is a disclosure,
    so a benchmark that disagrees completely is still admissible."""
    from streamcurves import curves as cv
    ref = [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 0.69}, {"x": 20.0, "y": 0.39},
           {"x": 40.0, "y": 0.0}]
    got = pb.agreement("chem_PTL", "TPL", [50.0, 120.0, 300.0], ref)
    assert got["n"] == 3
    assert got["same_class"] is not None
    # a far looser criterion places sites in a BETTER class than the strict
    # reference curve, so the shift is positive, and admission is unaffected
    assert got["net_shift"] > 0
    assert pb.fitness("chem_PTL", region="TPL")["admissible"]
    assert cv is not None


# --------------------------------------------------------------------------- #
# Ohio EPA's ecoregional biocriteria, checked on request (2026-09-21)
# --------------------------------------------------------------------------- #
def test_the_ohio_biocriteria_finding_is_recorded_and_checkable():
    """The nearest thing to a Population support criterion for this ecoregion.
    A negative finding a reader cannot check is not a finding, so the four
    grounds are recorded beside the code that refuses on them."""
    got = pb.OHIO_BIOCRITERIA
    assert got["verdict"] == "unsuitable"
    for key in ("indicator", "measurement", "stream_type", "geography"):
        assert got[key] and len(got[key]) > 80, key
    # the indicator ground is the one the owner named: an index, not a richness
    assert "COMPOSITE INDICES" in got["indicator"]
    # and the geography ground is measurable from the station table
    assert "Indiana" in got["geography"] and "Ohio" in got["geography"]


def test_the_archive_carries_no_index_the_ohio_criteria_could_be_read_against():
    """PB-1 for Ohio turns on whether Ohio's actual indices can be evaluated. They
    cannot: the archive carries none of the IBI, ICI, MIwb or QHEI columns. EPA's
    own NRSA indices (MMI_BENT, MMI_FISH, OE_SCORE) do enter the archive under
    methodology 0.16, as metrics of their own with EPA's own benchmarks; they are
    not Ohio's indices and never lend a threshold to a component metric."""
    import pandas as pd
    from pathlib import Path
    values = Path(__file__).resolve().parents[1] / "data" / "nrsa" / "values.parquet"
    if not values.exists():
        pytest.skip("NRSA archive not present")
    cols = pd.read_parquet(values, columns=None).columns
    # whole tokens only: land_PCTSILICICWS contains "ICI" and is a lithology share
    import re
    for token in ("IBI", "ICI", "MIWB", "QHEI"):
        pattern = re.compile(r"(^|_)" + token + r"($|_)", re.I)
        assert not [c for c in cols if pattern.search(c)], token
    indices = [c for c in cols if re.search(r"(^|_)MMI($|_)", c, re.I)]
    assert sorted(indices) == ["bent_MMI_BENT", "fish_MMI_FISH"]


# --------------------------------------------------------------------------- #
# Methodology 0.16: EPA's NRSA benchmarks for the indices and salinity
# --------------------------------------------------------------------------- #
NRSA_REGIONS = ("CPL", "NAP", "NPL", "SAP", "SPL", "TPL", "UMW", "WMT", "XER")


def test_the_nrsa_index_and_salinity_benchmarks_are_admissible_in_every_region():
    for metric in ("bent_MMI_BENT", "fish_MMI_FISH", "chem_COND"):
        assert pb.is_held(metric), metric
        for region in NRSA_REGIONS:
            got = pb.fitness(metric, region=region)
            assert got["admissible"], (metric, region, got["conditions"])
            assert "reproduces EPA's published condition class" in got["conditions"]["PB-1"]["why"]
    assert not pb.is_held("chem_PTL")


def test_a_held_benchmark_without_a_verified_class_check_is_not_admitted(monkeypatch):
    """The brief's rule: a benchmark that does not reproduce EPA's classes is not
    adopted as EPA's. PB-1 reads the recorded verification, so an unverified entry
    fails identity rather than passing on the strength of its citation."""
    spec = dict(pb.REGISTRY["bent_MMI_BENT"])
    spec["verification"] = {**spec["verification"], "result": {"n": 100, "agree": 90, "share": 0.9}}
    monkeypatch.setitem(pb.REGISTRY, "bent_MMI_BENT", spec)
    got = pb.fitness("bent_MMI_BENT", region="CPL")
    assert not got["admissible"] and not got["conditions"]["PB-1"]["pass"]
    assert "not been shown to reproduce" in got["conditions"]["PB-1"]["why"]


def test_the_index_bands_are_the_technical_support_documents_and_higher_is_better():
    # Table 5-3 of the 2018-19 TSD (Coastal Plains: good at or above 54.9, poor below 40.7)
    assert pb.bands("bent_MMI_BENT", "CPL") == pytest.approx((40.7, 54.9))
    assert pb.bands("bent_MMI_BENT", "UMW") == pytest.approx((22.7, 36.9))
    # Table 6-15: the Xeric West poor bound is EPA's applied 66.3, not the printed 63.7
    assert pb.bands("fish_MMI_FISH", "XER") == pytest.approx((66.3, 76.8))
    assert pb.bands("fish_MMI_FISH", "UMW") == pytest.approx((29.3, 39.8))
    pts = pb.curve_points("bent_MMI_BENT", "CPL")
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    assert xs == sorted(xs) and ys == sorted(ys), "a higher index scores higher"
    assert ys[0] == 0.0 and ys[-1] == 1.0
    assert 0.0 <= xs[0] and xs[-1] <= 100.0, "the index lives on 0 to 100"
    # the breakpoints sit at the DEEP class boundaries, a boundary value in EPA's class
    assert any(abs(p["x"] - 54.9) < 0.02 and abs(p["y"] - 0.69) < 1e-6 for p in pts)
    assert any(abs(p["x"] - 40.7) < 0.02 and abs(p["y"] - 0.39) < 1e-6 for p in pts)
    from streamcurves import curves as cv
    assert cv.interp_curve(pts, 54.9) > 0.69 and cv.interp_curve(pts, 54.89) < 0.69
    assert cv.interp_curve(pts, 40.7) > 0.39 and cv.interp_curve(pts, 40.69) < 0.39


def test_the_salinity_bands_are_table_7_1_with_good_owning_the_boundary():
    # Table 7-1: 500 and 1000 uS/cm in six regions, 1000 and 2000 in the three Plains regions
    assert pb.bands("chem_COND", "CPL") == pytest.approx((500.0, 1000.0))
    assert pb.bands("chem_COND", "SPL") == pytest.approx((1000.0, 2000.0))
    assert pb.bands("chem_COND", "TPL") == pytest.approx((1000.0, 2000.0))
    pts = pb.curve_points("chem_COND", "SPL")
    ys = [p["y"] for p in pts]
    assert ys[0] == 1.0 and ys[-1] == 0.0, "higher conductance is worse"
    from streamcurves import curves as cv
    # EPA classes a site at exactly 1000 in the Plains as Good and one at exactly 2000 as Fair
    assert cv.interp_curve(pts, 1000.0) > 0.69 and cv.interp_curve(pts, 1000.2) < 0.69
    assert cv.interp_curve(pts, 2000.0) > 0.39 and cv.interp_curve(pts, 2000.2) < 0.39


def test_a_held_entry_carries_its_own_provenance_into_the_bundle():
    src = pb.criteria_source("fish_MMI_FISH", "XER")
    assert src["catalogEntry"] == "nrsa-2018-19-fish-mmi" and src["easiMethod"] is None
    assert [b["rating"] for b in src["bands"]] == ["Good", "Fair", "Poor"]
    assert "76.8" in src["bands"][0]["label"] and "66.3" in src["bands"][2]["label"]
    assert [c["key"] for c in src["citations"]] == ["nrsa-2018-19", "nars-regions"]
    assert any("66.3" in x for x in src["limitations"])
    prov = pb.provenance("bent_MMI_BENT")
    assert prov["thresholdsSource"]["table"].startswith("Table 5-3") and prov["thresholdsSource"]["page"] == 41
    assert prov["verification"]["share"] == 1.0 and prov["verification"]["epaClassColumn"] == "BENT_MMI_COND"
    assert prov["sourceTier"] and prov["citations"] == ["nrsa-2018-19", "nars-regions"]
    assert pb.citation_line("chem_COND", "SPL") == (
        "USEPA NRSA 2018-19 specific conductance benchmarks, NARS-9 region SPL")
    d = pb.pool_decision("bent_MMI_BENT", "CPL", region_code="34", region_name="Western Gulf Coastal Plain")
    assert "40.7 and 54.9 MMI points" in d.transfer_note


def test_every_held_entry_records_a_complete_verification_against_epas_classes():
    """The catalog's own record of deliverable 2: thousands of classified visits per
    entry, every one reproduced, per region and per cycle, with the EPA class column
    and the script named, so a reader can rerun it from the raw files."""
    held = [e for e in pb.load_catalog()["entries"]
            if str((e.get("thresholds_source") or {}).get("catalog") or pb.EASI_SOURCE) != pb.EASI_SOURCE]
    assert sorted(e["metric"] for e in held) == ["bent_MMI_BENT", "chem_COND", "fish_MMI_FISH"]
    for e in held:
        ver = e["verification"]
        assert ver["epa_class_column"] and ver["script"].endswith("verify_benchmark_classes.py")
        res = ver["result"]
        assert res["n"] > 3000 and res["agree"] == res["n"] and res["share"] == 1.0, e["id"]
        assert set(res["per_region"]) == set(NRSA_REGIONS), e["id"]
        assert sum(r["n"] for r in res["per_region"].values()) == res["n"], e["id"]
        assert all(r["agree"] == r["n"] for r in res["per_region"].values()), e["id"]
        assert sum(c["n"] for c in res["per_cycle"].values()) == res["n"], e["id"]
        src = e["thresholds_source"]
        assert src["document"] and src["table"] and isinstance(src["page"], int) and src["url"], e["id"]
        assert set(e["thresholds"]) == set(NRSA_REGIONS), e["id"]
        for pair in e["thresholds"].values():
            assert float(pair[0]) < float(pair[1]), e["id"]


def test_the_verification_script_classifies_boundary_values_the_way_epa_does():
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts" / "nrsa" / "verify_benchmark_classes.py"
    spec = importlib.util.spec_from_file_location("verify_benchmark_classes", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    up = {"good": "at_or_above", "poor": "below"}
    # an MMI equal to the good bound is Good, equal to the poor bound is Fair
    assert mod.classify(54.9, 40.7, 54.9, "higher_is_better", up) == "Good"
    assert mod.classify(54.89, 40.7, 54.9, "higher_is_better", up) == "Fair"
    assert mod.classify(40.7, 40.7, 54.9, "higher_is_better", up) == "Fair"
    assert mod.classify(40.69, 40.7, 54.9, "higher_is_better", up) == "Poor"
    down = {"good": "at_or_below", "poor": "above"}
    # a conductance equal to the lower bound is Good, equal to the upper bound is Fair
    assert mod.classify(1000.0, 1000.0, 2000.0, "lower_is_better", down) == "Good"
    assert mod.classify(1000.1, 1000.0, 2000.0, "lower_is_better", down) == "Fair"
    assert mod.classify(2000.0, 1000.0, 2000.0, "lower_is_better", down) == "Fair"
    assert mod.classify(2000.1, 1000.0, 2000.0, "lower_is_better", down) == "Poor"
    # and the module's own bands agree with the script at the same boundaries
    from streamcurves import curves as cv
    pts = pb.curve_points("fish_MMI_FISH", "XER")
    assert cv.interp_curve(pts, 66.3) > 0.39 > cv.interp_curve(pts, 66.29)
    assert cv.interp_curve(pts, 76.8) > 0.69 > cv.interp_curve(pts, 76.79)


def test_every_input_pathway_names_its_measurements_source_entry_and_disclosure():
    """Owner decision D18: a documented gap does not complete a function. Each pathway
    the catalog states must be actionable: the measurement and protocol, at least one
    scoring source for any state, how the value enters DEEP, and the disclosure."""
    import json
    from pathlib import Path
    functions = {f["id"] for f in json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "staf_functions.json").read_text(encoding="utf-8"))["functions"]}
    paths = pb.input_pathways()
    assert {p["function"] for p in paths} == {"channel-floodplain-dynamics", "surface-water-storage"}
    for p in paths:
        assert p["function"] in functions, p["id"]
        assert p["applies_when"] and p["deep_entry"] and p["disclosure"], p["id"]
        assert "Provisional" in p["disclosure"], p["id"]
        for m in p["measurements"]:
            for key in ("key", "name", "units", "direction", "protocol"):
                assert m.get(key), (p["id"], key)
            assert m["direction"] in ("higher_is_better", "lower_is_better")
        assert any(str(s.get("states")).lower() == "any" for s in p["scoring"]), p["id"]
        for s in p["scoring"]:
            assert s.get("source"), p["id"]
    text = pb.catalog_path().read_text(encoding="utf-8")
    assert "\u2014" not in text


def test_the_channel_pathway_picks_the_state_tool_where_one_covers_the_target():
    import json
    from pathlib import Path
    registry = json.loads((Path(__file__).resolve().parents[1] / "data" / "sqt" / "registry.json")
                          .read_text(encoding="utf-8"))
    by_key = {r["key"]: r for r in registry["records"]}
    mn = pb.pathway_for("channel-floodplain-dynamics", states=["MN"])
    assert mn["scoring"]["states"] == ["MN"]
    for key in mn["scoring"]["registry_keys"]:
        rec = by_key[key]
        assert rec["state"] == "MN" and rec["eligible"], key
        assert rec["verification"]["status"] in ("verified", "partially-verified"), key
    wi = pb.pathway_for("channel-floodplain-dynamics", states=["wi", "MI"])
    assert wi["scoring"]["states"] == ["WI"]
    for key in wi["scoring"]["registry_keys"]:
        assert by_key[key]["verification"]["status"] == "verified", key
    # a state with no tool (Nebraska) falls to the national standard, two thresholds
    ne = pb.pathway_for("channel-floodplain-dynamics", states=["NE"])
    assert ne["scoring"]["states"] == "any"
    assert ne["scoring"]["thresholds"] == {"good": 1.2, "poor": 1.5}
    assert "EPA 843-K-12-006" in ne["scoring"]["source"] and "Table 7.2" in ne["scoring"]["source"]
    assert ne["scoring"]["measurement"] == "sqt_bank_height_ratio"
    assert [m["key"] for m in ne["measurements"]][0] == "sqt_bank_height_ratio"
    sentence = pb.pathway_sentence("channel-floodplain-dynamics", states=["NE"])
    assert sentence.startswith("This function is completed by an additional input: Bank height ratio")
    assert "Functioning 1.0 to 1.2" in sentence and "Provisional." in sentence
    assert pb.pathway_for("hyporheic-connectivity") is None
    assert pb.pathway_sentence("hyporheic-connectivity") == ""
    # the wetland pathway needs no new input and names the method that completes it
    wet = pb.pathway_for("surface-water-storage", states=["ID", "MT"])
    assert wet["measurements"][0]["key"] == "pctwet2019ws"
    assert "two-part" in wet["scoring"]["source"] and "zero_inflated_share" in wet["scoring"]["note"]
    assert "_" not in pb.pathway_sentence("surface-water-storage"), "a card speaks in plain words"
    # the channel refusal on a DEEP card already points at the pathway
    assert "bank height ratio" in pb.refusal("phab_XBKA", "44")


def test_the_oe_ratio_and_the_habitat_metrics_are_documented_refusals_not_gaps():
    for metric, words in (("bent_OE_SCORE", "taxa-loss categories"),
                          ("phab_XCMGW", "expected value modeled"),
                          ("phab_XFC_NAT", "expected value modeled"),
                          ("phab_LRBS_use", "expected value modeled"),
                          ("phab_XBKA", "bank angle"), ("phab_SINU", "sinuosity")):
        why = pb.refusal(metric, "44")
        assert why != pb.NO_CRITERION and words in why, (metric, why)
        assert not pb.fitness(metric, region="SPL")["admissible"]
    assert "chem_COND" not in pb.REFUSED, "salinity now has an entry, not a refusal"


def test_no_refusal_text_names_a_code_constant():
    """These strings ride into the bundle and onto a DEEP card. A reader cannot
    look up a Python name, so a refusal has to be self-contained."""
    import re
    for metric in sorted(pb.REFUSED):
        why = pb.fitness(metric, region="TPL")["conditions"]["PB-1"]["why"]
        assert not re.search(r"\b[A-Z][A-Z0-9_]{4,}\b", why.replace("NRSA", "").replace("IBI", "")
                             .replace("PB-1", "").replace("EPA", "")), (metric, why)


def test_no_refusal_carries_a_rule_or_fitness_code():
    """REFUSED strings ride onto DEEP's "Not assessed" card, where "PB-1" or
    "REF-10" means nothing to a reader. The condition is named in words."""
    import re
    for metric, why in pb.REFUSED.items():
        assert not re.search(r"\b(PB|REF|CONF|CURVE|DATA|STRAT|SELECT)-\d", why), (metric, why)



def test_the_ohio_finding_is_carried_only_where_it_was_made():
    """The Ohio check was made for the Eastern Corn Belt Plains, and its geography
    ground (half the stations in Indiana) is false anywhere else. The first 0.13
    Interior Plateau build carried it anyway (2026-09-21)."""
    ecbp = pb.refusal("bent_TOTLNTAX", "55")
    assert "Ohio EPA" in ecbp and "Indiana" in ecbp
    for l3 in ("71", "58", None):
        other = pb.refusal("bent_TOTLNTAX", l3)
        assert "Ohio" not in other and "Indiana" not in other, l3
        assert other == pb.REFUSED["bent_TOTLNTAX"]
    # read from the target's own stations when the caller names no region
    import pandas as pd
    at_71 = pb.fitness("bent_TOTLNTAX", frame=pd.DataFrame({"l3": ["71", "71"]}))
    assert "Ohio" not in at_71["conditions"]["PB-1"]["why"]
    at_55 = pb.fitness("bent_TOTLNTAX", frame=pd.DataFrame({"l3": ["55"]}))
    assert "Ohio EPA" in at_55["conditions"]["PB-1"]["why"]


def test_no_refusal_speaks_in_the_language_of_the_code():
    for metric in list(pb.REFUSED) + ["bent_EPT_NTAX"]:
        for l3 in ("55", "71"):
            why = pb.refusal(metric, l3)
            for word in ("vendored", "catalog", "repository", "these metrics"):
                assert word not in why, (metric, word)
    assert pb.refusal("bent_EPT_NTAX") == pb.NO_CRITERION



def test_the_bundle_carries_the_criterion_itself():
    """PB-5 says a benchmark's citation and provisional flag travel with the curve.
    The first 0.13 bundles carried only the method's title and cited the regional
    analysis the curve did not come from (2026-09-21)."""
    src = pb.criteria_source("chem_PTL", "TPL")
    assert src["region"] == "TPL" and "NARS-9 region TPL" in src["title"]
    # the bands on the metric's own units: micrograms for phosphorus
    assert [b["rating"] for b in src["bands"]] == ["Good", "Fair", "Poor"]
    assert "88.6 ug/L" in src["bands"][0]["label"] and "143 ug/L" in src["bands"][2]["label"]
    # only the citations that bear on the criterion, not EASI's station procedure
    assert [c["key"] for c in src["citations"]] == list(pb.CRITERION_CITATIONS)
    assert all(c["text"] for c in src["citations"])
    assert "sourceTier" not in src and src["provisional"] is False
    tn = pb.criteria_source("chem_NTL", "TPL")
    assert "0.7 mg N/L" in tn["bands"][0]["label"] and "1.274 mg N/L" in tn["bands"][2]["label"]
    assert pb.citation_line("chem_NTL", "TPL") == (
        "USEPA NRSA 2018-19 regional total nitrogen thresholds, NARS-9 region TPL")
