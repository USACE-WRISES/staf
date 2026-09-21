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
    """PB-1 for Ohio turns on whether the actual index can be evaluated. It
    cannot: the archive publishes component metrics, never an index."""
    import pandas as pd
    from pathlib import Path
    values = Path(__file__).resolve().parents[1] / "data" / "nrsa" / "values.parquet"
    if not values.exists():
        pytest.skip("NRSA archive not present")
    cols = pd.read_parquet(values, columns=None).columns
    # whole tokens only: land_PCTSILICICWS contains "ICI" and is a lithology share
    import re
    for token in ("IBI", "ICI", "MIWB", "QHEI", "MMI"):
        pattern = re.compile(r"(^|_)" + token + r"($|_)", re.I)
        assert not [c for c in cols if pattern.search(c)], token


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
