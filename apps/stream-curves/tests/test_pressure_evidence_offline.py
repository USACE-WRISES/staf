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
its reference came from. The builds here are fresh (no carry-forward).
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
        assert ev["nrsa_policy"] == nrsa_dataset.POLICY_LATEST_NON_NULL
        assert ev["easi_vendor"] is None and ev["screening_cache"] is None


def test_a_region_with_enough_reference_stations_stays_local(evidence):
    support = evidence["58"]["reference_support"]
    assert support and {d["status"] for d in support.values()} == {"local"}
    assert all(d["transfer_risk"] == "none" for d in support.values())
    assert not evidence["58"]["insufficient_support"]


def test_a_thin_region_borrows_per_metric_and_withholds_what_nothing_supports(evidence):
    ip = evidence["71"]
    statuses = {mk: d["status"] for mk, d in ip["reference_support"].items()}
    # Interior Plateau holds three least-disturbed stations. Each metric takes the
    # first source that passes the acceptance rules (methodology 0.14): the
    # region's own streams under the regional screen, its Level II parent, the
    # NARS-9 pool, an approved model or a published criterion. What nothing
    # admits is withheld. Total nitrogen takes the same source as total phosphorus.
    assert set(statuses.values()) == {"local_relaxed", "borrowed_l2", "borrowed_nars9",
                                      "modeled", "published", "insufficient"}
    assert sorted(ip["insufficient_support"]) == [
        "bent_EPT_NTAX", "bent_TOLRPIND", "bent_TOTLNTAX", "bfiws", "chem_CHLA", "chem_COND",
        "fish_NAT_TOLRPIND", "pctwet2019ws", "phab_BFWD_RAT", "phab_PCT_FAST",
        "phab_RP100_cm", "phab_XBKF_H", "phab_XCDENMID", "phab_XCMGW", "phab_XFC_NAT"]
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


def test_a_region_with_no_reference_station_says_so(evidence):
    ecbp = evidence["55"]
    assert ecbp["n_retained"] == 0 and ecbp["retained_ids"] == set()
    assert ecbp["tier"]["review_flags"] == [pe.NO_LOCAL_REFERENCE]
    built = {d["status"] for mk, d in ecbp["reference_support"].items()
             if mk in ecbp["curve_rows"]}
    assert built == {"borrowed_l1", "borrowed_l2", "borrowed_nars9"}
    # most chemistry and the benthic metrics find no source that passes
    withheld = set(ecbp["insufficient_support"])
    assert {"chem_COND", "bent_EPT_NTAX", "fish_NAT_TOTLNTAX"} <= withheld
    # the nutrient criteria carry the published basis. The approved models stay
    # out of a fresh build: this region's least-disturbed stream lies outside
    # their validated impervious-cover limits (REF-13)
    ladder = {mk: d["basis"] for mk, d in ecbp["reference_support"].items()
              if mk in (ecbp.get("ladder_metrics") or {})}
    assert ladder == {"chem_PTL": "published-benchmark",
                      "chem_NTL": "published-benchmark"}
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
    candidates enter only such a function). The two richness counts stay withheld,
    and each names which source refused and why, because "unassessed" without a
    reason is what the coverage gate exists to stop."""
    ecbp = evidence["55"]
    support = ecbp["reference_support"]
    assert support["fish_NAT_NTOLNTAX"]["status"] == "borrowed_l1"
    assert support["fish_NAT_NTOLPTAX"]["status"] == "borrowed_nars9"
    rows = {r["function_id"]: r for r in results["55"]["portfolio"] if r.get("function_id")}
    assert rows["population-support"]["coverage"] == "covered"
    assert set(rows["population-support"]["metrics"]) == {"fish_NAT_NTOLNTAX",
                                                          "fish_NAT_NTOLPTAX"}
    tried = {(a["metric"], a["rung"]): a for a in ecbp["ladder_attempts"]}
    for metric in ("fish_NAT_TOTLNTAX", "bent_TOTLNTAX"):
        for rung in ("REF-12", "REF-13", "REF-14"):
            got = tried.get((metric, rung))
            assert got is not None and not got["admitted"] and got["why"], (metric, rung)
        assert "state-specific field index" in tried[(metric, "REF-14")]["why"]


def test_each_metric_column_holds_values_only_inside_its_own_pool(evidence):
    ip = evidence["71"]
    data = ip["data"]
    for mk, d in ip["reference_support"].items():
        # a withheld metric has no pool, and a landscape column it shares with the
        # predictors stays in the frame for them
        if mk not in data.columns or d["status"] == "insufficient":
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
    assert ref["nLocalReference"] == 66 and ref["nCurvesBorrowed"] == 0
    # every metric has a local pool here, and none is held: the two benthic curves
    # DATA-03 held under 0.13 carry a value at 65 of the 66 stations since the
    # archive's empty cycles were restored (methodology 0.14)
    assert not b.get("insufficientReferenceSupport")
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


def test_withheld_metrics_are_named_and_stay_out_of_the_scored_blocks(results):
    b = results["55"]["bundle"]
    withheld = {w["metricKey"]: w for w in b["insufficientReferenceSupport"]}
    assert "chem_COND" in withheld
    w = withheld["chem_COND"]
    assert w["reason"] == "insufficient-reference-support"
    assert w["functions"] and w["levelsTried"]
    assert w["statement"].startswith("Insufficient reference support.")
    scored = set(_entries(b))
    assert not {w["metricId"] for w in withheld.values()} & scored


def test_coverage_counts_the_fixed_metrics_and_names_what_withholding_left_open(
        results, evidence):
    assert results["58"]["coverage"]["missing"] == 0
    assert results["58"]["coverage"]["covered"] == 20
    ecbp = results["55"]
    missing = set(ecbp["coverage"]["missingFunctionIds"])
    # 0.14, built fresh: the native non-tolerant fish taxa cover Population
    # support and the nutrient criteria cover Nutrient cycling. With no pool that
    # passes the acceptance rules, and outside the approved models' limits, eight
    # functions stay open, each drafted as an exception that names why
    assert "population-support" not in missing and "nutrient-cycling" not in missing
    assert missing == {"surface-water-storage", "low-flow-baseflow-dynamics",
                       "floodplain-connectivity", "channel-evolution", "light-thermal-regime",
                       "carbon-processing", "habitat-provision", "community-dynamics"}
    draft = pe.coverage_exceptions_draft(ecbp)
    assert {d["functionId"] for d in draft} <= missing
    assert all(d["reason"] == "insufficient-reference-support" and d["recordedBy"] == ""
               for d in draft)
    # a drafted entry becomes valid the moment the owner signs it
    signed = [{**d, "recordedBy": "gtmenichino"} for d in draft]
    rebuilt = ra.assemble(evidence["55"], coverage_exceptions=signed)
    assert not set(d["functionId"] for d in draft) & set(
        rebuilt["coverage"]["missingFunctionIds"])


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
    decision closes it."""
    mk = "phab_XEMBED"
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
    assert ref["valuePolicy"] == nrsa_dataset.POLICY_LATEST_NON_NULL
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
    assert {"REF-04", "REF-06", "REF-07", "REF-11", "REF-12", "REF-13", "REF-14",
            "DATA-11", "STRAT-10", "CURVE-11", "SELECT-04", "COV-01"} <= set(by_rule)
    assert not {"REF-01", "REF-02", "REF-05", "REF-08", "REF-09", "REF-10"} & set(by_rule)
    assert len(by_rule["CURVE-11"]) == len(FIXED)
    assert {r["subject"] for r in by_rule["REF-06"]} == set(res["insufficient_support"])
    # an accepted regional pool is the rule's decision: recorded, never a review item
    assert by_rule["REF-11"] and not any(r["review_required"] for r in by_rule["REF-11"])
    queue_triggers = {i["trigger"] for i in doc["reviewQueue"]["items"]}
    assert "borrowed_reference_pool" not in queue_triggers
    # a function no source supports reaches the queue as a documented gap
    assert "function_unassessed" in queue_triggers


# --------------------------------------------------------------------------- #
# the session and an interactive republish
# --------------------------------------------------------------------------- #
def test_the_session_carries_the_reference_statement(results):
    res = results["71"]
    fields = ra.session_fields(res)
    build = fields["reference_build"]
    assert build["method"] == "pressure-screen"
    assert set(build["fixedMetrics"]) == FIXED
    assert build["insufficientReferenceSupport"]
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
    assert bundle["insufficientReferenceSupport"] == res["bundle"]["insufficientReferenceSupport"]


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
    assert "bent_TOLRPIND" in text.split("## 6b.")[1].split("## 6c.")[0]
    borrowed_flag = [r for r in packet["curves"]
                     if any("pool borrowed from" in f for f in r["flags"])]
    assert borrowed_flag


def test_the_support_table_accounts_for_every_metric(results):
    res = results["55"]
    table = pe.support_frame(res)
    assert set(table["metric"]) == set(res["reference_support"]) | FIXED
    assert set(table.loc[table["status"] == "insufficient", "metric"]) \
        == set(res["insufficient_support"])
    # A published criterion is scored like the fixed criteria, so it groups with
    # them. 0.13 adds the two NRSA nutrient criteria to the five EASI ones.
    published = {mk for mk, d in res["reference_support"].items()
                 if d.get("basis") == "published-benchmark"}
    assert published == {"chem_PTL", "chem_NTL"}
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


def test_a_finalized_curve_is_scored_and_no_longer_held(evidence):
    """Built fresh, the Eastern Corn Belt Plains holds two curves for a reviewer,
    large wood volume and fast-water habitat. Finalizing one scores it."""
    res = ra.assemble(evidence["55"], finalize_metrics={"phab_PCT_FAST": "cleared in test"},
                      finalize_actor="tester")
    assert "spring-phab-pct-fast" in _entries(res["bundle"])
    held = {w["metricKey"] for w in res["bundle"].get("insufficientReferenceSupport") or []
            if w["reason"] == pe.HELD_FOR_REVIEW}
    assert held == {"phab_LWDeqVolM100"}


def test_a_metric_is_never_both_scored_and_withheld(evidence, monkeypatch):
    """The bundle writer drops a record whose metric made it into a scoring block,
    whatever produced the record: an interactive republish restores the session's
    list, and a reviewer may have cleared the curve since."""
    stale = {"metricId": "spring-chem-cond", "metricKey": "chem_COND",
             "reason": pe.HELD_FOR_REVIEW, "functions": [], "statement": "stale"}
    monkeypatch.setattr(pe, "held_for_review", lambda *a, **k: [stale])
    res = ra.assemble(evidence["58"])
    assert "spring-chem-cond" in _entries(res["bundle"])
    assert not res["bundle"].get("insufficientReferenceSupport")



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
        assert got["carriedForward"]["fromVersion"] == prior["fromVersion"]
    # a rebuilt curve walks the hierarchy afresh, and every carried one keeps its record
    support = res["reference_support"]
    assert all(support[mk].get("carried_from") == prior["fromVersion"] for mk in carried)
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
