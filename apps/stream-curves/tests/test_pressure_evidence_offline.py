"""The pressure-screen reference method end to end (methodology 0.12), offline.

Everything the pass reads is committed: the station screen table and the pooled
NRSA archive. Diagnostics are switched off, and the three pilot regions are built
once per module (a few minutes each, since methodology 0.14 walks the whole
reference-source hierarchy for every metric).

What is pinned here is what the owner decided on 2026-09-19 and extended on
2026-09-21: reference stations come from a fixed pressure screen, a thin region
takes, per metric, the first source of the reference-source hierarchy that passes
the acceptance rules, a metric no source supports is withheld and never scored,
landscape pressures are scored on fixed criteria, and every curve carries where
its reference came from. The builds here are fresh (no carry-forward). The facts
pinned per region follow the reference evidence regenerated at methodology 0.15
(616c04d: config/basis_validation.yaml rebuilt from the campaign's round three,
hierarchy and model-iv runs), which moved ten source verdicts.
"""
from __future__ import annotations

import copy
import json
import re

import pandas as pd
import pytest

from streamcurves import deep_export, fixed_criteria, methodology, nrsa_dataset
from streamcurves import pressure_evidence as pe
from streamcurves import provenance as pv
from streamcurves import reference_pool as rp
from streamcurves import reference_screen as rscreen
from streamcurves import regional_agent as ra
from streamcurves import review_packet, run_state, session_io

pytestmark = pytest.mark.skipif(
    not rscreen.station_screen_available()
    or nrsa_dataset.default_build_dataset_id() != nrsa_dataset.MULTI_CYCLE_DATASET_ID,
    reason="needs the committed station screen table and the pooled NRSA archive")

REGIONS = {"58": "Northeastern Highlands", "71": "Interior Plateau",
           "55": "Eastern Corn Belt Plains"}
FIXED = {"pctimp2019ws", "pctag2019ws", "rddensws", "dorws", "nid_dams_1mi"}


def _evidence(code: str, **kwargs) -> dict:
    max_order, protocols = nrsa_dataset.governed_frame("wadeable")
    # No national registry unless a test passes one: the committed registry is a
    # decision of record that may be regenerated, and these tests pin the
    # pipeline, not its contents. Without one every borrowed pool reads
    # "unassessed" and no class split applies.
    kwargs.setdefault("scale_registry", {})
    # These tests pin the hierarchy a fresh build walks; carrying the published
    # curves forward is tested on its own (methodology 0.14).
    kwargs.setdefault("carry", False)
    return ra.run_evidence(
        code, REGIONS[code], reference_method=run_state.REFERENCE_METHOD_PRESSURE,
        nrsa_dataset_id=nrsa_dataset.MULTI_CYCLE_DATASET_ID,
        nrsa_max_stream_order=max_order, nrsa_protocols=protocols,
        diagnostics_enabled=False, **kwargs)


@pytest.fixture(scope="module")
def evidence():
    return {code: _evidence(code) for code in REGIONS}


@pytest.fixture(scope="module")
def results(evidence):
    return {code: ra.assemble(ev) for code, ev in evidence.items()}


def _entries(bundle: dict) -> dict[str, dict]:
    out = {}
    for block in bundle["metricsByFunction"]:
        for m in block["metrics"]:
            out.setdefault(m["metricId"], m)
    return out


# --------------------------------------------------------------------------- #
# the reference screen and the pools
# --------------------------------------------------------------------------- #
def test_the_three_pilots_read_the_screen_the_station_table_pins(evidence):
    got = {code: (ev["reference_pool_summary"]["target_n_frame"], ev["n_retained"])
           for code, ev in evidence.items()}
    assert got == {"58": (175, 66), "71": (41, 3), "55": (44, 0)}
    # the candidate panel also holds the one canal reach the frame leaves out
    assert evidence["55"]["n_candidates"] == 45
    for ev in evidence.values():
        assert ev["reference_method"] == "pressure-screen"
        assert ev["screening_method"] == rscreen.METHOD
        assert ev["nrsa_policy"] == nrsa_dataset.DEFAULT_VALUE_POLICY
        assert ev["easi_vendor"] is None and ev["screening_cache"] is None


def test_a_region_with_enough_reference_stations_stays_local(evidence):
    support = evidence["58"]["reference_support"]
    statuses = {mk: d["status"] for mk, d in support.items()}
    # methodology 0.16 (owner decision D13): a local curve the checks refuse (a fallback
    # ramp or an inverted curve) walks on. Five metrics leave the 66-station local pool:
    # native fish richness and residual pool depth are withheld, native non-tolerant fish
    # taxa and wetland cover rest on flagged national pools (REF-16), bank angle on the
    # Level II pool. Everything else stays local at no transfer risk.
    assert {mk: s for mk, s in statuses.items() if s != "local"} == {
        "fish_NAT_NTOLNTAX": "national", "fish_NAT_TOTLNTAX": "insufficient",
        "phab_RP100_cm": "insufficient", "phab_XBKA": "borrowed_l2", "pctwet2019ws": "national"}
    assert all(d["transfer_risk"] == "none" for mk, d in support.items() if statuses[mk] == "local")
    assert sorted(evidence["58"]["insufficient_support"]) == ["fish_NAT_TOTLNTAX", "phab_RP100_cm"]
    assert evidence["58"]["flagged_metrics"] == {"fish_NAT_NTOLNTAX": "national",
                                                 "pctwet2019ws": "national"}


def test_a_thin_region_borrows_per_metric_and_withholds_what_nothing_supports(evidence):
    ip = evidence["71"]
    statuses = {mk: d["status"] for mk, d in ip["reference_support"].items()}
    # Interior Plateau holds three least-disturbed stations. Each metric takes the
    # first source that passes the acceptance rules (methodology 0.15, one search
    # order for every family): the region's own streams under the regional screen,
    # its Level II parent, its Level I parent, the NARS-9 pool, then comparable
    # national donors, an approved model or a published criterion. What nothing
    # admits is withheld. Total nitrogen takes the same source as total phosphorus.
    # Under the regenerated evidence (616c04d, basis_validation.yaml rebuilt)
    # relative bed stability lost its Level II acceptance and takes the Level I
    # pool, and tolerant fish individuals and large wood volume take matched
    # national donors (3c_matched accepted), so two statuses join the set.
    # 0.16: nothing is withheld here any more (REF-17 scores wetland cover, below)
    assert set(statuses.values()) == {"local_relaxed", "borrowed_l2", "borrowed_l1",
                                      "borrowed_nars9", "national", "modeled", "published"}
    assert statuses["phab_LRBS_use"] == "borrowed_l1"
    assert statuses["fish_NAT_TOLRPIND"] == statuses["phab_LWDeqVolM100"] == rp.STATUS_NATIONAL
    # regenerated evidence: tolerant fish individuals (national donors) and natural
    # fish cover (the region's own streams, 2r_l3 accepted) left the withheld list
    assert statuses["phab_XFC_NAT"] == "local_relaxed"
    # methodology 0.16 (REF-16): the twelve metrics 0.15 withheld because the recovery
    # evidence refused every pool now rest on flagged transfers (thirteen pools and
    # one national pool, base flow index), each at transfer risk unvalidated with the
    # verdict recorded and the confidence capped; wetland cover alone stays withheld,
    # every option refused for a stated reason (a fallback ramp or an inverted curve,
    # owner decision D13)
    assert sorted(ip["insufficient_support"]) == []
    flagged = ip["flagged_metrics"]
    # conductivity left the flagged pools for EPA's salinity benchmark (owner decision D12)
    assert sorted(mk for mk, src in flagged.items() if src == "pool") == [
        "bent_EPT_NTAX", "bent_TOLRPIND", "bent_TOTLNTAX", "chem_CHLA",
        "phab_BFWD_RAT", "phab_LSUB_DMM", "phab_PCT_FAST", "phab_RP100_cm", "phab_XBKF_H",
        "phab_XCDENMID", "phab_XCMGW", "phab_XEMBED"]
    assert flagged["bfiws"] == "national"
    for mk in flagged:
        d = ip["reference_support"][mk]
        assert d["transfer_risk"] == rp.RISK_UNVALIDATED and d["confidence_cap"] == 39, mk
        assert d["transfer_validation"]["accepted"] is False and "recovery test" in d["transfer_note"], mk
    # a validated source of any kind replaced the flagged pool of these metrics
    # (0.16 with the published-benchmark review: the two MMIs and conductivity on
    # EPA's benchmarks)
    assert sorted(ip["flagged_replaced"]) == ["bent_MMI_BENT", "chem_COND", "chem_NTL", "chem_PTL",
                                              "chem_TURB", "fish_MMI_FISH", "fish_NAT_TOLRPIND",
                                              "phab_LWDeqVolM100", "phab_SINU"]
    # every station pool refused wetland cover (a fallback ramp or an inverted curve,
    # D13), so REF-17 scores surface water storage on EASI's wetland-extent method, and
    # the pools' refusals stay on the metric's record
    wet = ip["reference_support"]["pctwet2019ws"]
    assert wet["basis"] == "easi-screening-method" and wet["status"] == "published"
    assert ip["last_resort_metrics"]["pctwet2019ws"]["functionId"] == "surface-water-storage"
    refused = {t["option"]: t for t in wet["options_tried"]}
    assert refused["regional_l2"]["refused_by"] == "curve"
    assert refused["regional_l2"]["curve"]["curve12"]["verdict"] == "inverted"
    assert refused["regional_l1"]["curve"]["curve_status"] == "degenerate_q25"
    assert refused["regional_nars9"]["curve"]["curve_status"] == "degenerate_q25"
    assert statuses["chem_NTL"] == statuses["chem_PTL"] == "published"
    # a withheld metric never reaches the curve engine
    assert not set(ip["insufficient_support"]) & set(ip["curve_rows"])
    assert not set(ip["insufficient_support"]) & set(ip["metric_config"])
    # a regional pool meets the floor, holds the region's own reference stations
    # and says where it came from
    for mk, d in ip["reference_support"].items():
        if d["status"].startswith("borrowed") or d["status"] == "local_relaxed":
            assert d["n_usable"] >= 10 and d["n_usable"] >= d["n_local"]
            assert d["transfer_note"]
        if d["status"] == rp.STATUS_NATIONAL:
            # REF-12: at least 10 matched donors, none inside the region, every one
            # carrying a value (DATA-03 has nothing to judge on such a pool)
            assert d["n_usable"] >= 10 and d["n_local"] == 0
            assert d["n_usable"] == d["n_comparable"] and d["transfer_note"]


def test_a_region_with_no_reference_station_says_so(evidence):
    ecbp = evidence["55"]
    assert ecbp["n_retained"] == 0 and ecbp["retained_ids"] == set()
    assert ecbp["tier"]["review_flags"] == [pe.NO_LOCAL_REFERENCE]
    built = {d["status"] for mk, d in ecbp["reference_support"].items()
             if mk in ecbp["curve_rows"]}
    # the built station curves rest on Level II and Level I pools, and (0.16, REF-16)
    # on the NARS-9 pool residual pool depth takes under the flagged-transfer rung.
    # Regenerated evidence (616c04d): the NARS-9 pool that 0.15-provisional drew
    # large wood volume from (23 usable of 43, withheld by DATA-03) is no longer an
    # accepted source (2r_nars9 is unsupported in the rebuilt basis_validation.yaml;
    # its 3a_envelope and 3c_matched stay accepted), so the search passes to REF-12:
    # 71 comparable national donors matched on natural setting, none inside this
    # ecoregion, each carrying a value, and the curve is a ladder curve
    assert built == {"borrowed_l1", "borrowed_l2", "borrowed_nars9"}
    assert ecbp["reference_support"]["phab_RP100_cm"]["status"] == "borrowed_nars9"
    assert ecbp["reference_support"]["phab_RP100_cm"]["transfer_risk"] == rp.RISK_UNVALIDATED
    lwd = ecbp["reference_support"]["phab_LWDeqVolM100"]
    assert lwd["status"] == rp.STATUS_NATIONAL and lwd["basis"] == "national-reference"
    assert lwd["n_usable"] == lwd["n_comparable"] == 71 and lwd["n_local"] == 0
    assert [{k: t[k] for k in ("option", "accepted", "n", "why")} for t in lwd["options_tried"]] \
        == [{"option": "3c_matched", "accepted": True, "n": 71, "why": ""}]
    # 0.16: every option tried records every check it faced, the curve checks included
    assert [c["check"] for c in lwd["options_tried"][0]["checks"]] == [
        "ACC-01", "ACC-04", "CURVE-07", "ACC-05/06", "ACC-03"]
    assert lwd["options_tried"][0]["curve"]["curve_status"] == "complete"
    assert "phab_LWDeqVolM100" in ecbp["ladder_metrics"]
    assert "phab_LWDeqVolM100" not in ecbp["curve_rows"]
    # 0.15: most chemistry and the benthic metrics found no source that passed.
    # 0.16 (REF-16): they rest on flagged Level I transfers, the recovery verdict
    # recorded and the confidence capped; only fast-water habitat stays withheld,
    # every pool a fallback ramp or unstable (owner decision D13)
    withheld = set(ecbp["insufficient_support"])
    assert withheld == {"phab_PCT_FAST"}
    flagged = ecbp["flagged_metrics"]
    # (conductivity left them for EPA's salinity benchmark, owner decision D12)
    assert {"bent_EPT_NTAX", "fish_NAT_TOTLNTAX"} <= set(flagged) and "chem_COND" not in flagged
    assert set(flagged.values()) == {"pool"} and len(flagged) == 16
    for mk in ("bent_EPT_NTAX", "fish_NAT_TOTLNTAX"):
        d = ecbp["reference_support"][mk]
        assert d["status"] == "borrowed_l1" and d["transfer_risk"] == rp.RISK_UNVALIDATED
        assert d["confidence_cap"] == 39 and d["transfer_validation"]["basis"] == "2r_l1"
    fast = ecbp["insufficient_support"]["phab_PCT_FAST"]["decision"]
    tried = {t["option"]: t for t in fast["options_tried"]}
    assert tried["regional_l2"]["curve"]["curve_status"] == "degenerate_q25"
    assert tried["regional_l1"]["curve"]["curve_status"] == "degenerate_q25"
    assert "not stable" in tried["regional_nars9"]["why"]
    # the nutrient criteria carry the published basis, and two metrics rest on
    # matched national donors (REF-12; tolerant fish individuals gained its
    # 3c_matched acceptance in the rebuilt file). The approved models stay out of
    # a fresh build: this region's least-disturbed stream lies outside their
    # validated impervious-cover limits (REF-13)
    ladder = {mk: d["basis"] for mk, d in ecbp["reference_support"].items()
              if mk in (ecbp.get("ladder_metrics") or {})}
    assert ladder == {"chem_PTL": "published-benchmark",
                      "chem_NTL": "published-benchmark",
                      # 0.16 (D12): EPA's salinity and the two MMI benchmarks
                      "chem_COND": "published-benchmark",
                      "bent_MMI_BENT": "published-benchmark",
                      "fish_MMI_FISH": "published-benchmark",
                      "phab_LWDeqVolM100": "national-reference",
                      "fish_NAT_TOLRPIND": "national-reference"}
    assert ecbp["reference_support"]["fish_NAT_TOLRPIND"]["n_usable"] == 29
    tried = {(a["metric"], a["rung"]): a for a in ecbp["ladder_attempts"]}
    assert not tried[("bent_HPRIME", "REF-13")]["admitted"]
    assert "impervious" in tried[("bent_HPRIME", "REF-13")]["why"]
    # a ladder curve never rides in the pooled station frame
    assert not set(ladder) & set(ecbp["data"].columns)
    # and every refusal names a reason rather than going silent
    for a in ecbp["ladder_attempts"]:
        assert a["why"], a


def test_population_support_is_scored_by_proportions_and_counts_name_their_blockers(
        evidence, results):
    """The Eastern Corn Belt Plains function 0.13 left without a basis. Under 0.14
    the native non-tolerant fish taxa, as a count and as a percent, pass the
    acceptance rules on the Level I and NARS-9 pools and score it (reserve
    candidates enter only such a function). Under 0.16 the two richness counts rest
    on flagged Level I transfers (REF-16); a validated reserve candidate still wins
    the function's place over a flagged regular one, so the portfolio is the same,
    and the flagged counts are recorded as supported, not selected, each naming
    which validated source refused it and why."""
    ecbp = evidence["55"]
    support = ecbp["reference_support"]
    assert support["fish_NAT_NTOLNTAX"]["status"] == "borrowed_l1"
    # methodology 0.15 (one search order, Level I before NARS-9): the percent taxa
    # take the Level I pool of 39 where 0.14 took the NARS-9 pool of 14
    assert support["fish_NAT_NTOLPTAX"]["status"] == "borrowed_l1"
    assert support["fish_NAT_NTOLNTAX"]["transfer_risk"] != rp.RISK_UNVALIDATED
    rows = {r["function_id"]: r for r in results["55"]["portfolio"] if r.get("function_id")}
    assert rows["population-support"]["coverage"] == "covered"
    # 0.16: the two default metrics are flagged, so SELECT-04 fills the function from the
    # validated reserves by source and score; the fish MMI (a new reserve on EPA's
    # benchmark, ranked as published) joins the candidates and moves the redundancy
    # scores, so lithophil individuals take the place non-tolerant taxa held
    assert set(rows["population-support"]["metrics"]) == {"fish_NAT_LITHPIND",
                                                          "fish_NAT_NTOLPTAX"}
    selection = results["55"]["meta"]["portfolioSelection"]["population-support"]
    left = {x["metric"]: x["source"] for x in selection["notSelected"]}
    assert left["fish_NAT_TOTLNTAX"] == "flagged" and left["bent_TOTLNTAX"] == "flagged"
    tried = {(a["metric"], a["rung"]): a for a in ecbp["ladder_attempts"]}
    for metric in ("fish_NAT_TOTLNTAX", "bent_TOTLNTAX"):
        assert support[metric]["transfer_risk"] == rp.RISK_UNVALIDATED
        for rung in ("REF-12", "REF-13", "REF-14"):
            got = tried.get((metric, rung))
            assert got is not None and not got["admitted"] and got["why"], (metric, rung)
        assert "state-specific field index" in tried[(metric, "REF-14")]["why"]


def test_each_metric_column_holds_values_only_inside_its_own_pool(evidence):
    ip = evidence["71"]
    data = ip["data"]
    for mk, d in ip["reference_support"].items():
        # a withheld metric has no pool, and a landscape column it shares with the
        # predictors stays in the frame for them; a curve from a source after the
        # station pools (0.16: base flow index on a flagged national pool) rides in
        # no station pool either
        if mk not in data.columns or d["status"] == "insufficient" \
                or d["status"] in rp.LADDER_STATUSES:
            continue
        have = set(data.loc[data[mk].notna(), "site_id"].astype(str))
        assert have == set(d["station_ids"]), mk
        assert ip["curve_rows"][mk]["n_reference"] == d["n_usable"]


def test_missingness_is_judged_over_the_metrics_own_pool(evidence):
    ip = evidence["71"]
    for mk, rec in ip["missingness"].items():
        d = ip["reference_support"][mk]
        assert rec["n_pool_members"] == d["n_comparable"]
        assert rec["n_with_value"] == d["n_usable"]


def test_pressure_metrics_are_never_fitted(evidence):
    for ev in evidence.values():
        assert set(ev["fixed_metrics"]) == FIXED
        assert not FIXED & set(ev["curve_rows"])
        # crops, dam density and road crossings left the fitted set with them
        assert not {"pctcrop2019ws", "damdensws", "rdcrsws"} & set(ev["curve_rows"])
        # the landscape expectation metrics still get reference curves
        assert {"pctwet2019ws", "bfiws"} <= set(ev["reference_support"])


# --------------------------------------------------------------------------- #
# the bundle
# --------------------------------------------------------------------------- #
def test_the_bundle_states_its_reference_method(results):
    b = results["58"]["bundle"]
    ref = b["referenceMethod"]
    assert ref["method"] == "pressure-screen" and ref["screenId"] == rscreen.SCREEN_ID
    # 0.16 (D13): bank angle takes the Level II pool and two metrics every source refused
    # are withheld; the two flagged national pools are not borrowed station pools
    assert ref["nLocalReference"] == 66 and ref["nCurvesBorrowed"] == 1
    assert ref["nCurvesFlagged"] == 2 and ref["nCurvesEasiScreening"] == 0
    assert {x["metricKey"] for x in b.get("insufficientReferenceSupport") or []} == {
        "fish_NAT_TOTLNTAX", "phab_RP100_cm"}
    assert b["sourceCitation"].endswith(
        "StreamCurves regional analysis, methodology " + methodology.methodology_version())


def test_fixed_metrics_ride_in_the_bundle_with_their_criteria(results):
    for code, res in results.items():
        entries = _entries(res["bundle"])
        for mk in FIXED:
            m = entries["spring-" + deep_export.deep_slug(mk)]
            assert m["criteriaBasis"] == "fixed"
            assert m["confidenceLabel"] == fixed_criteria.CONFIDENCE_LABEL
            assert m["metricRole"] == fixed_criteria.METRIC_ROLE
            assert m["criteriaSource"]["bands"]
            assert "referenceTier" not in m and "referenceSupport" not in m
            assert "referenceN" not in m and "predictorSource" not in m
            want = [{"x": float(p[0]), "y": float(p[1])}
                    for p in fixed_criteria.entry_for(mk)["points"]]
            assert m["curve"]["points"] == want
            assert m["sourceCitation"].startswith("EASI fixed criteria")


def test_fixed_curves_are_the_same_in_every_region(results):
    per_region = []
    for res in results.values():
        entries = _entries(res["bundle"])
        per_region.append({mk: entries["spring-" + deep_export.deep_slug(mk)]["curve"]["points"]
                           for mk in FIXED})
    assert per_region[0] == per_region[1] == per_region[2]


def test_every_reference_curve_carries_where_its_stations_came_from(results):
    for code, res in results.items():
        for m in _entries(res["bundle"]).values():
            if m.get("criteriaBasis") == "fixed":
                continue
            assert m["criteriaBasis"] == "reference"
            sup = m["referenceSupport"]
            # 0.13: a curve fitted to a station pool, or a modelled expectation
            # for an ecoregion that holds no clean stream. Either way it says so.
            assert sup["status"] in ("local", "local_relaxed", "borrowed_l2",
                                     "borrowed_nars9", "borrowed_l1",
                                     rp.STATUS_MODELED, rp.STATUS_NATIONAL)
            assert sup["basis"] and sup["basisLabel"]
            if sup["status"] == rp.STATUS_MODELED:
                assert sup["basis"] == "modeled-reference"
                # it says whether any stream here passes the screen, and never
                # claims none where a few do
                n_ref = res["bundle"]["referenceMethod"]["nLocalReference"]
                note = sup["transferNote"].lower()
                assert "reference condition" in note
                assert ("no stream in this ecoregion" in note) == (n_ref == 0)
                continue
            if sup["status"] == rp.STATUS_NATIONAL:
                # 0.15 (REF-12, first reached by the regenerated evidence: the
                # Eastern Corn Belt Plains' large wood volume): comparable national
                # donors matched on natural setting where no station pool of the
                # region or its parents passed. A ladder curve carries its station
                # count in the support record, not as a fitted curve's referenceN,
                # and says the donors are not the region's own.
                assert sup["basis"] == "national-reference"
                assert sup["nUsable"] >= 10 and sup["nLocal"] == 0
                assert "national pool" in sup["transferNote"]
                assert "none inside this ecoregion" in sup["transferNote"]
                # R5-12 (the 0.16 commit): the donors behind the curve, as a pool
                # curve states its stations
                assert m.get("referenceN") == sup["nUsable"]
                assert any("outside this ecoregion" in c for c in m["curveCaveats"])
                continue
            if sup.get("screenId"):
                # 0.14: a pool admitted under the documented regional screen names
                # the screen and the agriculture limit it was admitted under
                assert sup["screenId"] != rscreen.SCREEN_ID
                assert sup["screen"].startswith(sup["screenId"] + " (relaxed tier")
                assert sup["agricultureLimit"] >= 25
            else:
                assert sup["screen"] == rscreen.screen_label("strict")
            assert sup["nUsable"] == m["referenceN"]
            assert m["referenceTier"] == "least_disturbed"
            if sup["status"].startswith("borrowed"):
                assert sup["transferNote"]
                assert any("borrowed" in c for c in m["curveCaveats"])


def test_the_local_comparison_is_labeled_and_never_a_baseline(results):
    entries = _entries(results["58"]["bundle"])
    comp = entries["spring-chem-cond"]["localComparison"]
    assert comp["label"] == "Local best-available comparison (not reference)"
    assert comp["n"] >= 10 and comp["q25"] <= comp["q50"] <= comp["q75"]
    # it never enters the curve: the reference n is the strict pool's
    assert entries["spring-chem-cond"]["referenceN"] <= 66 < comp["n"]


def test_withheld_metrics_are_named_and_stay_out_of_the_scored_blocks(results, evidence):
    b = results["55"]["bundle"]
    withheld = {w["metricKey"]: w for w in b["insufficientReferenceSupport"]}
    # 0.16: conductivity rests on a flagged Level I transfer; fast-water habitat is
    # the one withheld metric, every option a fallback ramp or unstable (D13), and
    # its statement names each refusal
    assert set(withheld) == {"phab_PCT_FAST"}
    w = withheld["phab_PCT_FAST"]
    assert w["reason"] == "insufficient-reference-support" and "rule" not in w
    assert w["functions"] and w["levelsTried"]
    assert w["statement"].startswith("Insufficient reference support.")
    assert "fallback ramp" in w["statement"]
    # regenerated evidence (616c04d): no rule withholds a metric whose pool exists
    # in this build, since every pool used is under the DATA-03 limit. Large wood
    # volume, which 0.15-provisional withheld on its NARS-9 pool (23 usable of 43),
    # now rests on 71 matched national donors that all carry a value (REF-12) and
    # is scored. Every withheld entry is REF-06's; no curve is held for a reviewer
    # (a fallback curve is refused before it is built, 0.16); the DATA-03 statement
    # path runs end to end in test_a_rule_withheld_metric_is_recorded_and_raises_no_review_item.
    assert {x["reason"] for x in withheld.values()} == {"insufficient-reference-support"}
    assert "phab_LWDeqVolM100" not in withheld
    scored = set(_entries(b))
    assert "spring-phab-lwdeqvolm100" in scored
    assert not {x["metricId"] for x in withheld.values()} & scored
    # the support record is the national pool's, with no withhold on it
    sup = results["55"]["reference_support"]["phab_LWDeqVolM100"]
    assert sup["status"] == rp.STATUS_NATIONAL and "withheld" not in sup
    assert sup["n_usable"] == sup["n_comparable"] == 71
    assert "phab_LWDeqVolM100" in evidence["55"]["ladder_metrics"]
    assert "phab_LWDeqVolM100" not in results["55"]["curve_rows"]
    assert "phab_LWDeqVolM100" not in results["55"]["metric_config"]


def test_coverage_counts_the_fixed_metrics_and_names_what_withholding_left_open(
        results, evidence):
    assert results["58"]["coverage"]["missing"] == 0
    assert results["58"]["coverage"]["covered"] == 20
    ecbp = results["55"]
    missing = set(ecbp["coverage"]["missingFunctionIds"])
    # 0.14, built fresh: the native non-tolerant fish taxa cover Population
    # support and the nutrient criteria cover Nutrient cycling. Regenerated
    # evidence (616c04d): residual pool depth gained its Level II acceptance
    # (2r_l2) and took the Level II 8.2 pool of 16 stations. Methodology 0.16
    # (owner decision D11, REF-16): the seven functions 0.15 left as documented
    # gaps are scored on flagged transfers, so every function is covered; the
    # Level II pool of residual pool depth is refused by the curve checks and the
    # metric takes the NARS-9 pool of 43 under the flagged rung
    assert "population-support" not in missing and "nutrient-cycling" not in missing
    assert "low-flow-baseflow-dynamics" not in missing
    assert ecbp["reference_support"]["phab_RP100_cm"]["status"] == "borrowed_nars9"
    assert ecbp["reference_support"]["phab_RP100_cm"]["n_usable"] == 43
    assert missing == set() and ecbp["coverage"]["covered"] == 20
    assert pe.coverage_exceptions_draft(ecbp) == []
    # a documented-gap draft still becomes valid the moment the owner signs it: shown
    # on a copy of the result with one function emptied
    sample = copy.deepcopy(ecbp)
    sample["coverage"] = {**sample["coverage"], "missingFunctionIds": ["habitat-provision"]}
    draft = pe.coverage_exceptions_draft(sample)
    assert [d["functionId"] for d in draft] == ["habitat-provision"]
    assert all(d["reason"] == "insufficient-reference-support" and d["recordedBy"] == ""
               for d in draft)
    assert "Fast-water / riffle habitat" in draft[0]["justification"]
    assert "withheld" in draft[0]["justification"]


def test_the_portfolio_counts_fixed_metrics_in_their_functions(results):
    rows = {r["function_id"]: r for r in results["58"]["portfolio"] if r.get("function_id")}
    assert set(rows["catchment-hydrology"]["metrics"]) >= {"pctimp2019ws", "pctag2019ws"}
    assert rows["watershed-connectivity"]["coverage"] == "covered"
    assert rows["watershed-connectivity"]["metrics"] == ["nid_dams_1mi"]
    assert not any(g["function_id"] == "reach-inflow"
                   for g in results["58"]["uncovered_functions"])


# --------------------------------------------------------------------------- #
# review, confidence, provenance
# --------------------------------------------------------------------------- #
def test_an_accepted_regional_pool_is_the_rules_decision_and_caps_confidence(results):
    """0.14: a regional pool that passes the acceptance rules is REF-11's decision,
    recorded with every option tried, and no longer a review item. A pool admitted
    under the regional screen caps confidence at 59."""
    ip = results["71"]
    regional = [mk for mk, d in ip["reference_support"].items()
                if d["status"].startswith("borrowed") or d["status"] == "local_relaxed"]
    assert regional
    for mk in regional:
        rec = ip["mandatory_review"].get(mk) or {}
        assert not any(t.startswith("REF-05") for t in rec.get("triggers") or []), mk
        conf = ip["confidence"][mk]
        assert "regional_relaxed_screen" in conf["caps_applied"]
        assert conf["total"] <= 59
    local = results["58"]
    assert not any(t.startswith("REF-05") for rec in local["mandatory_review"].values()
                   for t in rec["triggers"])


def test_an_adjudicated_review_item_closes(evidence):
    """An exploratory pool (DATA-05) is still the owner's to accept, and a recorded
    decision closes it. (0.16: embeddedness rests on an adequate Level I pool now, so
    the exploratory pool here is bank angle, 12 of this ecoregion's own streams.)"""
    mk = "phab_XBKA"
    assert evidence["71"]["sample_sizes"][mk]["disposition"] == "exploratory"
    res = ra.assemble(evidence["71"], reviewer_decisions=[
        {"rule_id": "DATA-05", "subject": mk, "action": "accept",
         "rationale": "Exploratory pool reviewed against the region's streams.",
         "reviewer": "gtmenichino", "date": "2026-09-19"}])
    assert f"DATA-05:{mk}" not in res["mandatory_review"][mk]["open"]
    assert f"DATA-05:{mk}" in res["mandatory_review"][mk]["adjudicated"]


def test_the_manifest_and_digest_carry_the_reference_inputs(results):
    res = results["71"]
    manifest = pv.build_run_manifest(res, argv=["test"], started_at="2026-09-19T00:00:00Z",
                                     finished_at="2026-09-19T00:01:00Z")
    ref = manifest["inputs"]["reference"]
    assert ref["method"] == "pressure-screen"
    assert ref["screenId"] == rscreen.SCREEN_ID
    assert ref["stationScreen"]["sha256"] == rscreen.station_screen_identity()["sha256"]
    assert ref["fixedCriteria"]["sha256"] == fixed_criteria.fixed_criteria_sha256()
    assert ref["valuePolicy"] == nrsa_dataset.DEFAULT_VALUE_POLICY
    payload = pv.digest_payload_from_manifest(manifest)
    assert payload["reference"]["method"] == "pressure-screen"
    # the digest moves when a reference input moves
    other = copy.deepcopy(manifest)
    other["inputs"]["reference"]["fixedCriteria"]["sha256"] = "sha256:0"
    assert pv.digest_payload_from_manifest(other) != payload
    # and a manifest without the block (every legacy run) carries no such key
    legacy = copy.deepcopy(manifest)
    legacy["inputs"].pop("reference")
    assert "reference" not in pv.digest_payload_from_manifest(legacy)


def test_the_records_name_the_new_rules(results):
    res = results["71"]
    manifest = pv.build_run_manifest(res, argv=["test"], started_at="2026-09-19T00:00:00Z",
                                     finished_at="2026-09-19T00:01:00Z")
    doc = pv.build_provenance(res, manifest, timestamp="2026-09-19T00:00:00Z")
    by_rule: dict[str, list[dict]] = {}
    for r in doc["records"]:
        by_rule.setdefault(r["rule_id"], []).append(r)
    # 0.16: REF-16 (the flagged pools) and REF-17 (wetland cover on EASI's method) record
    # their decisions; nothing is withheld and no function is left unassessed, so
    # REF-06 and COV-01 raise nothing here
    assert {"REF-04", "REF-07", "REF-11", "REF-12", "REF-13", "REF-14", "REF-16", "REF-17",
            "DATA-11", "STRAT-10", "CURVE-11", "SELECT-04"} <= set(by_rule)
    assert not {"REF-06", "COV-01"} & set(by_rule)
    assert not {"REF-01", "REF-02", "REF-05", "REF-08", "REF-09", "REF-10"} & set(by_rule)
    assert len(by_rule["CURVE-11"]) == len(FIXED)
    # methodology 0.15: every REF-06 subject is a withheld metric, and every
    # withheld entry names its rule (REF-06 where no source passed, else the rule
    # that judged its existing pool)
    ref06 = {r["subject"] for r in by_rule.get("REF-06", [])}
    assert ref06 <= set(res["insufficient_support"])
    for mk, item in res["insufficient_support"].items():
        assert (pe.withheld_rule(item) == "REF-06") == (mk in ref06), mk
    # an accepted regional pool is the rule's decision: recorded, never a review item
    assert by_rule["REF-11"] and not any(r["review_required"] for r in by_rule["REF-11"])
    queue_triggers = {i["trigger"] for i in doc["reviewQueue"]["items"]}
    assert "borrowed_reference_pool" not in queue_triggers
    # every function is scored (owner decision D18), so none reaches the queue unassessed
    assert "function_unassessed" not in queue_triggers


def _provenance(res: dict) -> dict:
    manifest = pv.build_run_manifest(res, argv=["test"], started_at="2026-09-26T00:00:00Z",
                                     finished_at="2026-09-26T00:01:00Z")
    return pv.build_provenance(res, manifest, timestamp="2026-09-26T00:00:00Z")


def test_a_rule_withheld_metric_is_recorded_and_raises_no_review_item(results, monkeypatch):
    """Methodology 0.15: a DATA-03 withhold at the build is a failed DATA-03 record
    with the statement, the pool record says the metric was withheld, and nothing
    about it reaches the review queue.

    Under the regenerated evidence (616c04d) no pool the three pilots use is above
    the 40 percent limit: the Eastern Corn Belt Plains' large wood volume, withheld
    on its NARS-9 pool at 0.15-provisional, now rests on national donors that all
    carry a value (a REF-12 pass, checked last). So the path runs on the
    Northeastern Highlands' own pool with the limit lowered to 20 percent, below
    the 24 percent of its 66 stations that carry no large wood volume: a real
    build with real numbers, only the limit moved (config untouched)."""
    cfg = copy.deepcopy(methodology.load_config())
    assert cfg["data_rules"]["max_missingness_review"] == 0.40
    cfg["data_rules"]["max_missingness_review"] = 0.20
    monkeypatch.setattr(methodology, "load_config", lambda: cfg)
    ev = _evidence("58")
    mk = "phab_LWDeqVolM100"
    withheld = {k: v for k, v in ev["insufficient_support"].items()
                if v.get("reason") == pe.HIGH_MISSINGNESS}
    # 0.16: beside it, the two metrics every source refused here (D13)
    assert list(withheld) == [mk]
    assert set(ev["insufficient_support"]) == {mk, "fish_NAT_TOTLNTAX", "phab_RP100_cm"}
    item = withheld[mk]
    rec = ev["missingness"][mk]
    assert rec["withheld"] is True and rec["rule"] == "DATA-03"
    assert rec["n_pool_members"] == 66 and rec["n_with_value"] == 50
    assert 0.20 < rec["missing_fraction"] < 0.40 and item["rule"] == "DATA-03"
    assert item["statement"] == pe.missingness_statement(item["detail"], item["decision"])
    assert item["statement"] == (
        "High missingness. 24% of the 66 comparable reference stations of this ecoregion's "
        "own reference pool have no value for this metric (50 carry one), above the 20% "
        "limit of rule DATA-03, so the metric is withheld at the build. No curve was built "
        "and the metric is not scored.")
    # the support record keeps the pool it was judged over, under status withheld
    sup = ev["reference_support"][mk]
    assert sup["status"] == pe.STATUS_WITHHELD and sup["pool_status"] == rp.STATUS_LOCAL
    assert sup["withheld"] == pe.HIGH_MISSINGNESS and sup["rule"] == "DATA-03"
    assert sup["n_usable"] == 50 and sup["n_comparable"] == 66
    assert mk not in ev["curve_rows"] and mk not in ev["metric_config"]
    assert mk not in ev["data"].columns
    res = ra.assemble(ev)
    listed = {w["metricKey"]: w for w in res["bundle"]["insufficientReferenceSupport"]}
    w = listed[mk]
    assert w["reason"] == pe.HIGH_MISSINGNESS and w["rule"] == "DATA-03"
    assert w["statement"] == item["statement"] and w["functions"]
    assert w["detail"]["missing_fraction"] == rec["missing_fraction"]
    assert w["metricId"] not in _entries(res["bundle"])
    table = pe.support_frame(res)
    assert set(table.loc[table["status"] == "withheld", "metric"]) == {mk}
    doc = _provenance(res)
    by = {(r["rule_id"], r["subject"]): r for r in doc["records"]}
    data03 = by[("DATA-03", mk)]
    assert data03["verdict"] == pv.VERDICT_FAIL and data03["review_required"] is False
    assert data03["review_triggers"] == [] and data03["computed"]["withheld"] is True
    assert data03["recommendation"] == item["statement"]
    assert data03["inputs"]["missing_fraction"] == rec["missing_fraction"]
    assert ("REF-06", mk) not in by
    pool = by[("REF-05", mk)]                # the region's own pool
    assert pool["computed"]["status"] == rp.STATUS_LOCAL
    assert pool["computed"]["withheld"] == pe.HIGH_MISSINGNESS
    assert pool["computed"]["withheld_rule"] == "DATA-03" and not pool["review_required"]
    assert "withheld under DATA-03" in pool["recommendation"]
    assert not any(i["subject"] == mk for i in doc["reviewQueue"]["items"])
    ledger = {r["metric"]: r for r in doc["metricLedger"]["rows"]}
    assert ledger[mk]["disposition"] == pv.UNSUPPORTED and ledger[mk]["rule"] == "DATA-03"
    assert not ledger[mk]["engine"]["executed"]
    # its function is a documented gap when every candidate is withheld with a statement
    gaps = {g["function_id"]: g for g in pv.coverage_gaps(res)}
    fid = ledger[mk]["functionId"]
    if fid in gaps:
        assert gaps[fid]["withheld_under"][mk] == "DATA-03"
        assert mk not in gaps[fid]["held_for_review"]
    # and in the Eastern Corn Belt Plains, built on the shipped limit, the same
    # metric is a REF-12 pass on 71 national donors, with no DATA-03 record at all
    monkeypatch.undo()
    doc55 = _provenance(results["55"])
    by55 = {(r["rule_id"], r["subject"]): r for r in doc55["records"]}
    assert ("DATA-03", mk) not in by55 and ("REF-06", mk) not in by55
    assert by55[("REF-12", mk)]["verdict"] == pv.VERDICT_PASS
    assert by55[("REF-12", mk)]["computed"]["n_usable"] == 71
    ledger55 = {r["metric"]: r for r in doc55["metricLedger"]["rows"]}
    assert ledger55[mk]["rule"] == "REF-12" and ledger55[mk]["engine"]["executed"]
    assert not any(i["subject"] == mk for i in doc55["reviewQueue"]["items"])


# --------------------------------------------------------------------------- #
# the session and an interactive republish
# --------------------------------------------------------------------------- #
def test_the_session_carries_the_reference_statement(results):
    res = results["71"]
    fields = ra.session_fields(res)
    build = fields["reference_build"]
    assert build["method"] == "pressure-screen"
    assert set(build["fixedMetrics"]) == FIXED
    # 0.16: nothing is withheld here (REF-17 scores wetland cover); the list is kept
    assert build["insufficientReferenceSupport"] == []
    assert fields["screening_run"]["method"] == rscreen.METHOD
    assert fields["screening_run"]["method_version"] == run_state.REFERENCE_SCREEN_METHOD_VERSION
    # it survives a save and a reopen
    payload = session_io.dump_session_fields(fields, session_name="ip")
    back = session_io.decode_session_fields(json.loads(session_io.dumps_session(payload)))
    assert back["reference_build"]["fixedMetrics"] == build["fixedMetrics"]
    assert back["reference_build"]["metricAnnotations"].keys() \
        == build["metricAnnotations"].keys()


def test_an_interactive_republish_keeps_the_fixed_metrics_and_the_support(results):
    res = results["71"]
    fields = ra.session_fields(res)
    rows = deep_export.deep_collect_curve_rows(fields["completed_metrics"])
    rows = {mk: r for mk, r in rows.items() if mk in res["intended_metrics"]}
    meta = {"assessmentId": "ip", "assessmentName": "IP", "sourceCitation": "t",
            "region": res["region"]}
    rows, mapping, config = pe.apply_reference_build(
        fields["reference_build"], rows, fields["discipline_function_mapping"],
        fields["metric_config"], meta)
    bundle = deep_export.build_deep_assessment_bundle(rows, mapping, config, meta)
    got, want = _entries(bundle), _entries(res["bundle"])
    assert set(got) == set(want)
    for mid, m in want.items():
        for key in ("criteriaBasis", "referenceSupport", "localComparison", "criteriaSource"):
            assert got[mid].get(key) == m.get(key), (mid, key)
        assert got[mid]["curve"]["points"] == m["curve"]["points"]
    assert bundle["referenceMethod"] == res["bundle"]["referenceMethod"]
    assert bundle.get("insufficientReferenceSupport") == res["bundle"].get("insufficientReferenceSupport")


def test_a_legacy_session_passes_through_untouched():
    rows, mapping, config = {"m": {"metric": "m"}}, pd.DataFrame(), {"m": {}}
    meta: dict = {}
    assert pe.apply_reference_build(None, rows, mapping, config, meta) == (rows, mapping, config)
    assert meta == {}
    assert pe.fixed_function_ids(None) == []
    assert pe.session_reference_build({"reference_method": "easi-eci"}) is None


def test_the_coverage_views_count_the_fixed_functions(results):
    fields = ra.session_fields(results["58"])
    fids = pe.fixed_function_ids(fields["reference_build"])
    assert {"catchment-hydrology", "reach-inflow", "streamflow-regime",
            "high-flow-dynamics", "watershed-connectivity"} == set(fids)
    without = deep_export.uncovered_functions_from_mapping(
        fields["discipline_function_mapping"], fields["metric_config"], [])
    with_fixed = deep_export.uncovered_functions_from_mapping(
        fields["discipline_function_mapping"], fields["metric_config"], [],
        always_covered=fids)
    assert {f for f, _ in without} - {f for f, _ in with_fixed} \
        == {"catchment-hydrology", "reach-inflow", "high-flow-dynamics",
            "watershed-connectivity"}
    quick = deep_export.function_coverage_quick(
        fields["completed_metrics"], fields["discipline_function_mapping"], [],
        always_covered=fids)
    assert "watershed-connectivity" in quick["coveredFunctionIds"]


# --------------------------------------------------------------------------- #
# the packet, the tables, the census
# --------------------------------------------------------------------------- #
def test_the_packet_shows_the_reference_statement(results):
    res = results["71"]
    manifest = pv.build_run_manifest(res, argv=["test"], started_at="2026-09-19T00:00:00Z",
                                     finished_at="2026-09-19T00:01:00Z")
    doc = pv.build_provenance(res, manifest, timestamp="2026-09-19T00:00:00Z")
    packet = review_packet.build_packet(
        res, doc, {"decisions": [], "uncovered": [], "hard_stops": [], "applied_ids": []},
        policy_meta={"policy_version": "1.1", "sha256": "sha256:0"}, enabled=[],
        staged=None, promote_command="promote")
    json.dumps(packet, default=str)                   # the packet is a JSON document
    text = review_packet.packet_markdown(packet)
    for heading in ("## 6. Reference support per metric", "## 6a. Borrowed pools",
                    "## 6b. Withheld for insufficient reference support",
                    "## 6c. Local best-available comparison", "## 6d. Fixed criteria",
                    "## 6e. Discrimination check"):
        assert heading in text
    assert "REF-02" not in text.split("## 6.")[1].split("## 7.")[0]
    # 0.16: every station pool refused wetland cover here and REF-17 scores it on EASI's
    # method, so nothing is withheld; the metric is still listed with its support
    assert "None." in text.split("## 6b.")[1].split("## 6c.")[0]
    assert "pctwet2019ws" in text.split("## 6.")[1].split("## 7.")[0]
    borrowed_flag = [r for r in packet["curves"]
                     if any("pool borrowed from" in f for f in r["flags"])]
    assert borrowed_flag


def test_the_support_table_accounts_for_every_metric(results):
    res = results["55"]
    table = pe.support_frame(res)
    assert set(table["metric"]) == set(res["reference_support"]) | FIXED
    # a metric with no pool reads insufficient; one a rule withheld although its
    # pool exists reads withheld (methodology 0.15); together they are the list.
    # Regenerated evidence (616c04d): no rule withholds here (large wood volume
    # is a national curve now), and every listed metric is REF-06's
    assert set(table.loc[table["status"].isin(["insufficient", "withheld"]), "metric"]) \
        == set(res["insufficient_support"])
    assert set(table.loc[table["status"] == "withheld", "metric"]) == set()
    assert set(table.loc[table["status"] == rp.STATUS_NATIONAL, "metric"]) \
        == {"phab_LWDeqVolM100", "fish_NAT_TOLRPIND"}
    # A published criterion is scored like the fixed criteria, so it groups with
    # them. 0.13 adds the two NRSA nutrient criteria to the five EASI ones.
    published = {mk for mk, d in res["reference_support"].items()
                 if d.get("basis") == "published-benchmark"}
    # 0.16 (D12): EPA's salinity and the two MMI benchmarks join the nutrient criteria
    assert published == {"chem_PTL", "chem_NTL", "chem_COND", "bent_MMI_BENT", "fish_MMI_FISH"}
    assert set(table.loc[table["criteria_basis"] == "fixed", "metric"]) == FIXED | published


def test_the_census_agrees_with_the_build(evidence):
    max_order, protocols = nrsa_dataset.governed_frame("wadeable")
    got = pe.census(["71"], max_stream_order=max_order, protocols=protocols,
                    scale_registry={})
    table = got["table"].set_index("metric")
    support = evidence["71"]["reference_support"]
    assert set(table.index) == set(support)
    for mk, d in support.items():
        assert table.loc[mk, "status"] == d["status"], mk
        # a model has no station pool: the census reports none, the build the
        # stations the model was fitted on
        if d["status"] != rp.STATUS_MODELED:
            assert int(table.loc[mk, "n_usable"]) == d["n_usable"], mk
    region = got["regions"][0]
    assert (region["n_frame"], region["n_strict"], region["n_relaxed"]) == (41, 3, 8)
    assert "| 71 | Interior Plateau | 41 | 3 | 8 |" in pe.census_markdown(got)


# --------------------------------------------------------------------------- #
# a registry split becomes curve layers
# --------------------------------------------------------------------------- #
def test_a_registry_split_the_pool_supports_becomes_curve_layers():
    registry = {"version": 1, "analysis_version": "test",
                "metrics": {"phab_XEMBED": {"status": "decided", "supported_level": "l3",
                                            "split": "NhdSlopeClass",
                                            "split_status": "accept"}}}
    ev = _evidence("58", scale_registry=registry)
    rec = ev["strata_applied"]["phab_XEMBED"]
    assert rec["stratifier"] == "NhdSlopeClass"
    if not rec["applied"]:
        pytest.skip("the pool holds fewer than two slope classes at the stratum floor")
    assert len(ev["stratum_rows"]["phab_XEMBED"]) == len(rec["supported"]) >= 2
    res = ra.assemble(ev)
    m = _entries(res["bundle"])["spring-phab-xembed"]
    layers = m["curveLayers"]
    assert layers[0]["stratum"] == "" and m["activeStratum"] == ""
    assert layers[0]["points"] == m["curve"]["points"]          # the pooled curve
    block = m["stratifier"]
    assert block["variable"] == "nhd_slope" and block["breaks"] == [0.005, 0.02]
    # a layer is named by its class key, the value the data column holds
    with_curve = [c["key"] for c in block["classes"] if c["hasCurve"]]
    assert sorted(with_curve) == sorted(layer["stratum"] for layer in layers[1:])
    assert all(c["label"] != c["key"] for c in block["classes"])
    # a metric without a registry split stays a single curve
    assert "curveLayers" not in _entries(res["bundle"])["spring-chem-cond"]
    assert ev["strata_applied"].keys() == {"phab_XEMBED"}


# --------------------------------------------------------------------------- #
# method choice
# --------------------------------------------------------------------------- #
def test_the_method_default_follows_the_data_set():
    pooled, legacy = nrsa_dataset.MULTI_CYCLE_DATASET_ID, nrsa_dataset.DEFAULT_DATASET_ID
    assert rscreen.resolve_reference_method(None, pooled) == "pressure-screen"
    assert rscreen.resolve_reference_method(None, legacy) == "easi-eci"
    assert rscreen.resolve_reference_method("easi-eci", pooled) == "easi-eci"
    with pytest.raises(ValueError, match="pooled NRSA archive"):
        rscreen.resolve_reference_method("pressure-screen", legacy)
    with pytest.raises(ValueError, match="unknown reference method"):
        rscreen.resolve_reference_method("quartiles", pooled)


def test_the_pressure_pass_refuses_the_legacy_data_set():
    with pytest.raises(ValueError, match="pooled NRSA archive"):
        ra.run_evidence("58", "Northeastern Highlands",
                        reference_method=run_state.REFERENCE_METHOD_PRESSURE,
                        nrsa_dataset_id="not-the-pooled-archive")



# --------------------------------------------------------------------------- #
# a curve held for a reviewer is recorded, and nothing is decided for the owner
# --------------------------------------------------------------------------- #
def _held_evidence():
    return {"n_retained": 66,
            "metric_config": {"bent_A": {"display_name": "A"}, "bent_B": {}, "bent_C": {},
                              "chem_X": {}},
            "missingness": {"bent_A": {"missing_fraction": 0.6364}},
            "curve_rows": {"bent_A": {}, "bent_B": {}, "bent_C": {}},
            "insufficient_support": {"chem_X": {}}, "ladder_metrics": {}}


def test_only_a_curve_still_pending_is_recorded_as_held():
    review = {"bent_A": {"status": "data_review", "decision": "pending"},
              "bent_B": {"status": "data_review", "decision": "reviewer_finalized"},
              "bent_C": {"status": "degenerate", "decision": "removed_from_scope"},
              "chem_X": {"status": "insufficient_data", "decision": "pending"}}
    held = pe.held_for_review(_held_evidence(), review, scored=["bent_B"])
    assert [w["metricKey"] for w in held] == ["bent_A"]
    w = held[0]
    assert w["reason"] == pe.HELD_FOR_REVIEW and w["curveStatus"] == "data_review"
    assert w["statement"].startswith("Held for review. 64% of this ecoregion's 66 "
                                     "least-disturbed stations have no value")
    assert "40%" in w["statement"] and "DATA-" not in w["statement"]


def test_a_held_curve_says_why_in_words_for_every_status():
    for status in pe.HELD_REASONS:
        review = {"bent_C": {"status": status, "decision": "pending"}}
        held = pe.held_for_review(_held_evidence(), review, scored=[])
        assert held and "reviewer" in held[0]["statement"], status
        assert not re.search(r"(DATA|CURVE|STRAT|REF)-[0-9]", held[0]["statement"])


def test_no_curve_is_held_and_a_fallback_curve_is_withheld_not_finalizable(evidence):
    """Built fresh under 0.15, the Eastern Corn Belt Plains held one curve for a
    reviewer, fast-water habitat, the engine's fallback ramp. Under 0.16 (owner
    decision D13) the pool whose curve would be that ramp is refused at acceptance:
    nothing is held, the metric is withheld with the reasons, and a finalization
    names nothing to finalize. Large wood volume, held under 0.14 for its
    missingness and withheld at the build by DATA-03 at 0.15-provisional, is a
    national curve under the regenerated evidence (616c04d)."""
    plain = ra.assemble(evidence["55"])
    listed = {w["metricKey"]: w for w in plain["bundle"].get("insufficientReferenceSupport") or []}
    assert {mk for mk, w in listed.items() if w["reason"] == pe.HELD_FOR_REVIEW} == set()
    assert listed["phab_PCT_FAST"]["reason"] == "insufficient-reference-support"
    assert "phab_LWDeqVolM100" not in listed
    assert "spring-phab-lwdeqvolm100" in _entries(plain["bundle"])
    assert "spring-phab-pct-fast" not in _entries(plain["bundle"])
    assert plain["bundle"]["referenceMethod"]["nHeldForReview"] == 0
    assert deep_export.fallback_entries(plain["bundle"]) == []
    assert not (evidence["55"].get("curve_review") or {}).get("phab_PCT_FAST")


def test_a_metric_is_never_both_scored_and_withheld(evidence, monkeypatch):
    """The bundle writer drops a record whose metric made it into a scoring block,
    whatever produced the record: an interactive republish restores the session's
    list, and a reviewer may have cleared the curve since."""
    stale = {"metricId": "spring-chem-cond", "metricKey": "chem_COND",
             "reason": pe.HELD_FOR_REVIEW, "functions": [], "statement": "stale"}
    monkeypatch.setattr(pe, "held_for_review", lambda *a, **k: [stale])
    res = ra.assemble(evidence["58"])
    assert "spring-chem-cond" in _entries(res["bundle"])
    # the stale record is dropped; the two metrics every source refused here (D13) stay
    withheld = {x["metricKey"] for x in res["bundle"].get("insufficientReferenceSupport") or []}
    assert withheld == {"fish_NAT_TOTLNTAX", "phab_RP100_cm"}



def test_a_ladder_curve_states_its_basis_at_the_metric(results):
    """DEEP and the calculator read the sentence at the metric, where the fixed
    criteria have always put it. The first 0.13 bundles put it only inside
    referenceSupport, and both readers fell back to the station-pool sentence."""
    entries = _entries(results["55"]["bundle"])
    ladder = {mid: m for mid, m in entries.items()
              if str((m.get("referenceSupport") or {}).get("status")) in
              ("national", "modeled", "published")}
    assert ladder, "the Eastern Corn Belt Plains build should carry ladder curves"
    for mid, m in ladder.items():
        assert m.get("basisStatement"), mid
        assert m["basisStatement"] == m["referenceSupport"]["basisStatement"], mid


def test_a_published_benchmark_carries_its_criterion_into_the_bundle(results):
    entries = _entries(results["55"]["bundle"])
    for mk in ("chem_PTL", "chem_NTL"):
        m = entries["spring-" + deep_export.deep_slug(mk)]
        src = m["criteriaSource"]
        assert isinstance(src, dict) and src["region"] == "TPL"
        assert len(src["bands"]) == 3 and src["citations"]
        assert m["sourceCitation"].startswith("USEPA NRSA 2018-19 regional")
        assert m["publishedBenchmark"]["region"] == "TPL"


# --------------------------------------------------------------------------- #
# methodology 0.14: published curves are carried forward unchanged
# --------------------------------------------------------------------------- #
#: what a carried block may differ in: where the exporter places it, and the
#: marker naming the version it comes from
_PLACEMENT = ("discipline", "assignmentOrigin", "carriedForward")


def _blocks_by_function(bundle: dict) -> dict:
    return {(fn["functionId"], m["metricId"]): m for fn in bundle["metricsByFunction"]
            for m in fn["metrics"]}


def test_a_rebuild_carries_every_published_curve_it_does_not_rebuild():
    from streamcurves import carry_forward as cf
    from streamcurves import library as lib
    prior = cf.prepare("71")
    assert prior.get("fromVersion"), "Interior Plateau has a published version"
    ev = _evidence("71", carry=prior)
    res = ra.assemble(ev)
    published = lib.load_version_bundle(prior["assessmentId"], prior["fromVersion"])
    before = _blocks_by_function(published)
    after = _blocks_by_function(res["bundle"])
    carried = set(prior["carried"])
    assert carried and not carried & set(prior["rebuilt"])
    for (fid, mid), block in before.items():
        mk = next((k for k in carried if "spring-" + deep_export.deep_slug(k) == mid), None)
        if mk is None:
            continue
        got = after.get((fid, mid))
        assert got is not None, (fid, mid)
        strip = lambda b: {k: v for k, v in b.items() if k not in _PLACEMENT}
        assert strip(got) == strip(block), (fid, mid)
        # the version that built it: this one, or, for a curve this version itself
        # carried, the one it came from (review of 2026-09-22)
        assert got["carriedForward"] == prior["carried"][mk]["annotations"]["carriedForward"]
        assert got["carriedForward"]["fromVersion"] <= prior["fromVersion"]
    # a rebuilt curve walks the hierarchy afresh, and every carried one keeps its record
    support = res["reference_support"]
    assert all(support[mk].get("carried_from")
               == prior["carried"][mk]["annotations"]["carriedForward"]["fromVersion"]
               for mk in carried)
    assert len(carried) > 20, "v6 scores 22 curves it carried itself, and they carry again"
    for mk in prior["rebuilt"]:
        assert not support.get(mk, {}).get("carried_from")


# --------------------------------------------------------------------------- #
# REF-15: a build applies the owner's curve decisions, end to end
# --------------------------------------------------------------------------- #
def test_a_build_applies_the_owners_curve_decisions(evidence):
    from streamcurves import owner_curves as oc
    from streamcurves import owner_sources as osrc
    ev = evidence["55"]
    plain = ra.assemble(ev)
    before = _blocks_by_function(plain["bundle"])
    why = "A reason long enough to be recorded."
    ladder = sorted(ev["ladder_metrics"])[0]
    fixed = next(mk for mk in sorted(FIXED)
                 if sum(1 for (_f, mid) in before
                        if mid == "spring-" + deep_export.deep_slug(mk)) > 1)
    fixed_mid = "spring-" + deep_export.deep_slug(fixed)
    fixed_fn = sorted(f for (f, mid) in before if mid == fixed_mid)[-1]
    withheld = sorted(ev["insufficient_support"])[0]
    cfg = osrc.agent_config(withheld)
    entered = {"kind": osrc.ENTERED, "title": "Owner thresholds", "citation": None,
               "ref": {"method": osrc.BREAKPOINTS},
               "curve": {"displayName": withheld, "points": [{"x": 0.0, "y": 0.0},
                                                            {"x": 10.0, "y": 1.0}],
                         "config": cfg, "annotations": osrc.entered_annotations(
                             withheld, method=osrc.BREAKPOINTS, title="Owner thresholds",
                             config=cfg)}}
    fn = osrc.crosswalk_functions(withheld)[0]
    decisions = [
        oc.new_decision(ladder, oc.REMOVE, rationale=why, recorded_by="owner"),
        oc.new_decision(fixed, oc.UNMAP, functions=[fixed_fn], rationale=why,
                        recorded_by="owner"),
        oc.new_decision(withheld, oc.SOURCE, functions=[fn], source=entered, rationale=why,
                        recorded_by="owner")]
    res = ra.assemble(ev, curve_decisions=decisions)
    after = _blocks_by_function(res["bundle"])
    ladder_mid = "spring-" + deep_export.deep_slug(ladder)
    assert not any(mid == ladder_mid for (_f, mid) in after)
    assert (fixed_fn, fixed_mid) not in after
    assert any(mid == fixed_mid for (_f, mid) in after)
    got = after[(fn, "spring-" + deep_export.deep_slug(withheld))]
    assert got["ownerDecision"]["recordedBy"] == "owner" and got["basis"] == "owner-entered"
    assert withheld not in {str(w.get("metricKey")) for w in
                            res["bundle"].get("insufficientReferenceSupport") or []}
    # nothing else moves: every other entry is the plain build's
    touched = {ladder_mid, fixed_mid, "spring-" + deep_export.deep_slug(withheld)}
    assert {k: v for k, v in before.items() if k[1] not in touched} == \
        {k: v for k, v in after.items() if k[1] not in touched}
    assert len(res["bundle"]["ownerCurveDecisions"]) == 3
    # the record: one REF-15 record per decision, and the packet section
    manifest = pv.build_run_manifest(res, started_at="a", finished_at="a")
    doc = pv.build_provenance(res, manifest, timestamp="a")
    assert len([r for r in doc["records"] if r["rule_id"] == "REF-15"]) == 3
    section = "\n".join(review_packet._hierarchy_section(review_packet.hierarchy_block(res)))
    assert "Curve decisions of the owner (REF-15)" in section and withheld in section


def _points(row) -> list:
    return pd.DataFrame(row["curve_points"])[["metric_value", "index_score"]].round(9) \
        .values.tolist()


def test_the_owners_choice_holds_its_metric_out_of_the_fit(evidence):
    """REF-15, owner decision 2026-09-22 ("your choice stands"): a metric the owner
    removed or chose a source for stays out of the build's own fit though a station
    pool would support it. The record says so, and the request joins the digest."""
    from streamcurves import owner_curves as oc
    from streamcurves import owner_sources as osrc
    plain = evidence["55"]
    plain_res = ra.assemble(plain)
    fitted = next(mk for mk in sorted(plain["curve_rows"])
                  if any(mid == "spring-" + deep_export.deep_slug(mk)
                         for (_f, mid) in _blocks_by_function(plain_res["bundle"])))
    mid = "spring-" + deep_export.deep_slug(fitted)
    fn = next(f for (f, m) in _blocks_by_function(plain_res["bundle"]) if m == mid)
    held_ev = _evidence("55", hold=[fitted])
    assert fitted not in held_ev["curve_rows"] and fitted not in held_ev["metric_config"]
    assert fitted not in held_ev["data"].columns
    pool = held_ev["held_by_owner"][fitted]["decision"]
    assert pool["status"] != rp.STATUS_INSUFFICIENT and pool["n_usable"]
    # every other curve the build fits is the plain build's
    for mk, row in plain["curve_rows"].items():
        if mk != fitted:
            assert _points(held_ev["curve_rows"][mk]) == _points(row), mk
    why = "A reason long enough to be recorded."
    cfg = dict(plain["metric_config"][fitted])
    entered = {"kind": osrc.ENTERED, "title": "Owner thresholds", "citation": None,
               "ref": {"method": osrc.BREAKPOINTS},
               "curve": {"displayName": fitted, "points": [{"x": 0.0, "y": 0.0},
                                                          {"x": 10.0, "y": 1.0}],
                         "config": cfg, "annotations": osrc.entered_annotations(
                             fitted, method=osrc.BREAKPOINTS, title="Owner thresholds",
                             config=cfg)}}
    chosen = oc.new_decision(fitted, oc.SOURCE, functions=[fn], source=entered, rationale=why,
                             recorded_by="owner")
    res = ra.assemble(held_ev, curve_decisions=[chosen])
    got = _blocks_by_function(res["bundle"])[(fn, mid)]
    assert got["basis"] == "owner-entered" and got["ownerDecision"]["recordedBy"] == "owner"
    manifest = pv.build_run_manifest(res, started_at="a", finished_at="a")
    assert manifest["inputs"]["reference"]["heldByOwner"] == [fitted]
    [rec] = [r for r in pv.build_provenance(res, manifest, timestamp="a")["records"]
             if r["rule_id"] == "REF-15"]
    assert rec["computed"]["heldFromFit"]["nUsable"] == pool["n_usable"]
    assert rec["computed"]["overrides"] in ("REF-04", "REF-11")
    assert "holds it out of the build" in rec["recommendation"]
    assert pe.session_reference_build(res)["ownerHeld"][fitted]["nUsable"] == pool["n_usable"]
    # a removal leaves it out, and a build with no decision names no hold
    removed = oc.new_decision(fitted, oc.REMOVE, rationale=why, recorded_by="owner")
    res2 = ra.assemble(held_ev, curve_decisions=[removed])
    assert not any(m == mid for (_f, m) in _blocks_by_function(res2["bundle"]))
    plain_manifest = pv.build_run_manifest(plain_res, started_at="a", finished_at="a")
    assert "heldByOwner" not in plain_manifest["inputs"]["reference"]
    assert oc.held_metrics([chosen, removed]) == [fitted]
