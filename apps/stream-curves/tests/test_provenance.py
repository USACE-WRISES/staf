"""The run record: what a regional run did, and what it did not.

The old decision log wrote a hardcoded rule list. It named REF-01 twice, claimed
DATA-05 and DATA-06 on runs where every curve was adequate, and named no STRAT
rule at all, which is exactly how a whole missing analysis stage went unnoticed
while the log said the run was complete.

The completeness test below is the one that matters: every rule in the catalog
must appear in either rules_applied or rules_not_evaluated. A silently skipped
family cannot survive it.
"""

from __future__ import annotations

import json

import pytest

from streamcurves import methodology
from streamcurves import provenance as pv
from streamcurves import regional_agent as ra

TS = "2026-07-29T00:00:00+00:00"


@pytest.fixture(scope="module")
def result() -> dict:
    # diagnostics_n_boot=20 keeps the module fixture fast; the resampling path
    # is identical, just with fewer resamples than a production run's 200.
    return ra.run("58", "Northeastern Highlands", do_screen=False,
                  use_streamcat=False, diagnostics_n_boot=20)


@pytest.fixture(scope="module")
def manifest(result) -> dict:
    return pv.build_run_manifest(
        result, argv=["--l3", "58"], started_at=TS, finished_at=TS)


@pytest.fixture(scope="module")
def provenance(result, manifest) -> dict:
    return pv.build_provenance(result, manifest, timestamp=TS)


# --- the completeness gate ---------------------------------------------------- #
def test_every_catalog_rule_is_accounted_for(provenance):
    applied = set(provenance["rules_applied"])
    not_evaluated = {r["rule_id"] for r in provenance["rules_not_evaluated"]}
    catalog = set(methodology.rule_ids())

    assert applied | not_evaluated == catalog
    assert not (applied & not_evaluated), "a rule cannot be both"


def test_the_strat_family_is_applied(provenance):
    """The bug, stated executably: no STRAT rule appeared in the old log."""
    strat = [r for r in provenance["rules_applied"] if r.startswith("STRAT-")]
    assert "STRAT-00" in strat, "stratifier screening did not run"
    assert "STRAT-08" in strat, "breakpoint provenance was not recorded"


def test_rules_applied_is_derived_from_the_records(provenance):
    assert provenance["rules_applied"] == sorted(
        {r["rule_id"] for r in provenance["records"]})


def test_unimplemented_rules_say_why(provenance):
    by_id = {r["rule_id"]: r for r in provenance["rules_not_evaluated"]}
    # CURVE-03 (one-standard-error selection) stays moot while exactly one
    # curve family is approved, so it must be accounted for as not evaluated.
    assert "CURVE-03" in by_id, "an unevaluated rule must say so"
    for entry in provenance["rules_not_evaluated"]:
        assert entry["reason"]
        assert entry["implementation_status"]


def test_the_new_machinery_rules_are_applied(provenance):
    """Wave 3: the formerly missing rule families now leave records."""
    applied = set(provenance["rules_applied"])
    for rule_id in ("CURVE-02", "CURVE-04", "CURVE-06", "CONF-01", "SELECT-02",
                    "DATA-01", "DATA-09"):
        assert rule_id in applied, f"{rule_id} left no record"


# --- record shape ------------------------------------------------------------- #
def test_every_record_carries_every_field(provenance):
    assert provenance["records"], "no rule fired"
    for record in provenance["records"]:
        assert set(record) == set(pv.RULE_RECORD_FIELDS), record["decision_id"]


def test_every_rule_id_exists_in_the_catalog(provenance):
    """Guards typos, and makes the catalog load-bearing rather than decorative."""
    for record in provenance["records"]:
        methodology.rule(record["rule_id"])  # raises on an unknown id


def test_statuses_are_copied_from_the_catalog_not_retyped(provenance):
    for record in provenance["records"]:
        catalog = methodology.rule(record["rule_id"])
        assert record["threshold_status"] == catalog.get("threshold_status")
        assert record["implementation_status"] == catalog.get("implementation_status")


def test_confidence_is_null_with_a_stated_basis(provenance):
    """The per-record confidence slot keeps its categorical basis: the CONF-01
    heuristic is a per-curve score that rides in its own CONF-01/02 records,
    and inventing a number on every rule record would be the single thing that
    makes a published assessment indefensible."""
    for record in provenance["records"]:
        assert record["confidence"]["score"] is None
        assert record["confidence"]["basis"] == "categorical_proxy"


def test_reviewer_fields_start_empty(provenance):
    """They are what a human pass fills in, which is what makes this an audit
    trail rather than a report."""
    for record in provenance["records"]:
        assert record["reviewer"] is None
        assert record["reviewer_action"] is None


# --- the manifest ------------------------------------------------------------- #
def test_manifest_has_every_required_block(manifest):
    for block in ("agent", "methodology", "configs", "inputs", "stratifiers",
                  "determinism", "outputs", "inputsDigest"):
        assert manifest.get(block), block
    assert manifest["methodology"]["methodology_version"]
    assert manifest["methodology"]["config_sha256"].startswith("sha256:")


def test_inputs_digest_is_stable_and_input_sensitive(result, manifest):
    again = pv.build_run_manifest(result, argv=["--l3", "58"], started_at="different")
    assert manifest["inputsDigest"] == again["inputsDigest"], "timestamps must not count"

    tweaked = dict(manifest["methodology"], methodology_version="9.9-test")
    assert methodology.inputs_digest({"methodology": tweaked}) != methodology.inputs_digest(
        {"methodology": manifest["methodology"]})


def test_every_registered_stratifier_appears_with_a_verdict(manifest):
    """Included and excluded both, so "why was slope not screened in this region"
    is answerable from the record without re-running anything."""
    registry = ra.stratifiers.load_national_registry()
    candidates = manifest["stratifiers"]["candidates"]
    assert [c["stratification"] for c in candidates] == list(registry["candidates"])
    for candidate in candidates:
        assert isinstance(candidate["eligible"], bool)
        assert candidate["reason"]
        assert candidate["breakpoints"], candidate["stratification"]


def test_manifest_states_the_breakpoint_policy(manifest):
    assert "No data-derived binning" in manifest["stratifiers"]["breakpointPolicy"]
    assert manifest["stratifiers"]["mode"] == "advisory"


def test_determinism_block_states_the_seed_policy(manifest):
    determinism = manifest["determinism"]
    # v0.6: the run seed every resampling diagnostic derives from is recorded
    # here, where the old policy text said a future bootstrap would have to.
    assert determinism["randomSeeds"] == {"runSeed": manifest["diagnostics"]["runSeed"]}
    assert "deterministic" in determinism["seedPolicy"]
    assert "run seed" in determinism["seedPolicy"]
    assert "registry-declared order" in determinism["orderPolicy"]


# --- the review queue --------------------------------------------------------- #
def test_queue_and_records_are_in_bijection(provenance):
    flagged = {r["decision_id"] for r in provenance["records"] if r["review_required"]}
    queued = {i["decision_id"] for i in provenance["reviewQueue"]["items"]}
    assert flagged == queued, "a review-required record with no queue item, or vice versa"


def test_the_advisory_stratifier_gap_is_surfaced(provenance):
    """The item this whole effort exists for: screening says a stratification is
    significant, the agent builds one unstratified curve, and before this the gap
    was completely invisible."""
    items = [i for i in provenance["reviewQueue"]["items"]
             if i["trigger"] == "advisory_stratifier_not_applied"]
    assert items, "a broad-use candidate was found but never raised for review"
    for item in items:
        assert item["priority"] == 2
        assert "unstratified" in item["question"]
        assert item["evidence"]["tier"] == "Broad-Use Candidate"


def test_queue_items_are_actionable(provenance):
    for item in provenance["reviewQueue"]["items"]:
        assert item["question"].endswith("?")
        assert "accept" in item["allowed_actions"]
        assert item["status"] == "open"


def test_priority_tiers_stay_primary_with_the_numeric_score_riding_along(provenance):
    """Tier ordering is the queue's backbone; the implemented Review Priority
    (impact x uncertainty x novelty) rides per metric item without reordering
    a hard stop below anything."""
    for item in provenance["reviewQueue"]["items"]:
        assert item["priority"] in (1, 2, 3, 4)
        assert "hard stop" in item["priority_basis"]["note"]
        numeric = item["priority_basis"].get("review_priority")
        if numeric is not None:
            assert 1 <= numeric <= 27


def test_queue_is_ordered_by_priority(provenance):
    priorities = [i["priority"] for i in provenance["reviewQueue"]["items"]]
    assert priorities == sorted(priorities)


def test_markdown_is_rendered_from_the_json(provenance):
    text = pv.review_queue_markdown(provenance["reviewQueue"])
    for item in provenance["reviewQueue"]["items"]:
        assert item["item_id"] in text


# --- serialization ------------------------------------------------------------ #
def test_provenance_is_json_serializable(provenance):
    """The published copy goes through a strict writer with no default handler,
    so a stray numpy scalar fails the publish rather than the record."""
    json.dumps(provenance)


def test_records_flatten_to_a_table(provenance):
    frame = pv.to_frame(provenance["records"])
    assert list(frame.columns) == list(pv.RULE_RECORD_FIELDS)
    assert len(frame) == len(provenance["records"])


# --- the rebuild ledger and the register (campaign Round 1) -------------------- #
def test_the_document_carries_the_ledger_and_the_register(provenance, result):
    """Every build writes both: a legacy build's fitted curves have no reference-support
    record, so each reads not_evaluated with the reason; the selected set still names
    every bundle pair."""
    ledger = provenance["metricLedger"]
    assert ledger["schema"] == pv.LEDGER_SCHEMA
    assert ledger["build"]["inputsDigest"] == provenance["inputsDigest"]
    assert ledger["build"]["refit"] == "missing" and ledger["build"]["carriedFrom"] is None
    rows = ledger["rows"]
    assert rows and all(r["disposition"] in pv.LEDGER_DISPOSITIONS for r in rows)
    assert {r["disposition"] for r in rows if r["functionId"]} <= {pv.NOT_EVALUATED, pv.REMOVED}
    legacy = [r for r in rows if r["disposition"] == pv.NOT_EVALUATED]
    assert legacy and all("legacy reference method" in r["reason"] for r in legacy)
    assert all(not r["engine"]["executed"] for r in legacy)
    register = provenance["candidateRegister"]
    assert register["schema"] == 1 and register["rows"]
    selected = {(r["subject"], r["functionId"]) for r in register["rows"] if r["status"] == "selected"}
    assert selected == {(r["metric"], r["functionId"]) for r in legacy}
    json.dumps(provenance)


def test_build_provenance_takes_a_register_already_computed(result, manifest):
    from streamcurves import candidates as C
    reg = C.register_for_result(result)
    doc = pv.build_provenance(result, manifest, timestamp=TS, register=reg)
    assert doc["candidateRegister"] == C.register_document(reg)
    assert doc["metricLedger"] == pv.build_ledger(result, manifest=manifest, register=reg)


def test_ledger_rows_for_bundle_states_both_sides():
    ledger = {"rows": [{"metric": "phab_XEMBED", "functionId": "f1", "disposition": pv.REFITTED},
                       {"metric": "chem_PTL", "functionId": "f1", "disposition": pv.UNSUPPORTED},
                       {"metric": "phab_SINU", "functionId": None, "disposition": pv.REFITTED}]}
    bundle = {"metricsByFunction": [{"functionId": "f1", "metrics": [{"metricId": "spring-phab-xembed"},
                                                                     {"metricId": "spring-chem-ptl"}]}]}
    got = pv.ledger_rows_for_bundle(bundle, ledger)
    assert got["selected"] == [("spring-phab-xembed", "f1")]
    assert got["missing"] == [("spring-chem-ptl", "f1")] and got["extra"] == [] and not got["equal"]


# --- digest schema 2 ----------------------------------------------------------- #
def test_a_legacy_manifest_keeps_its_digest_under_the_legacy_rules(result, manifest):
    assert "digestSchema" not in manifest
    payload = pv.digest_payload_from_manifest(manifest)
    assert "code" not in payload and "decisions" not in payload
    assert methodology.inputs_digest(payload) == manifest["inputsDigest"]
    assert manifest["agent"]["agentVersion"] == pv.AGENT_VERSION == "regional-agent-2"
    assert "codeFingerprint" not in manifest["agent"]


def test_new_manifest_defaults_declare_schema_2_and_move_the_digest(result, manifest):
    from streamcurves import code_identity
    defaults = pv.new_manifest_defaults()
    assert defaults == {"digestSchema": 2}
    two = pv.build_run_manifest(result, argv=["--l3", "58"], started_at=TS, finished_at=TS,
                                defaults=defaults)
    assert two["digestSchema"] == 2 and two["inputsDigest"] != manifest["inputsDigest"]
    assert two["agent"]["codeFingerprint"] == code_identity.fingerprint()
    assert two["inputs"]["refit"] == {"mode": "missing", "carriedFrom": None}
    assert two["reviewerInputs"]["decisionsDigest"].startswith("sha256:")
    assert two["reviewerInputs"]["files"] == {} and two["experimental"] is None
    names = {c["path"] for c in two["configs"]}
    assert {"config/" + n for n in pv.DIGEST_SCHEMA_2_CONFIGS} <= names
    assert {c["path"] for c in manifest["configs"]} < names
    assert "config/metric_evidence.yaml" not in names
    payload = pv.digest_payload_from_manifest(two)
    assert payload["code"] == two["agent"]["codeFingerprint"] and payload["refit"] == "missing"
    assert methodology.inputs_digest(payload) == two["inputsDigest"]
    # what the batch declares rides through: the refit mode, the files, the experimental block
    three = pv.build_run_manifest(result, argv=[], started_at=TS, finished_at=TS, defaults={
        "digestSchema": 2, "refit": "all", "reviewerInputs": {"files": {"owner_decisions.json": "abc"}},
        "experimental": {"configRoot": "variant-a", "extensionFlag": True}})
    assert three["inputs"]["refit"]["mode"] == "all"
    assert three["reviewerInputs"]["files"] == {"owner_decisions.json": "abc"}
    assert three["experimental"]["configRoot"] == "variant-a"
    assert three["inputsDigest"] != two["inputsDigest"]
    with pytest.raises(ValueError):
        pv.build_run_manifest(result, defaults={"digestSchema": 9})


def test_the_decisions_digest_is_canonical_and_input_sensitive():
    from streamcurves import owner_curves as oc
    d = oc.new_decision("chem_PTL", oc.REMOVE, rationale="The owner explains this decision here.",
                        recorded_by="GM", recorded_at="2026-09-25T00:00:00+00:00")
    base = {"curveDecisions": [d], "answers": [{"rule_id": "CURVE-07", "subject": "m", "action": "accept",
                                                 "rationale": "fine  as is"}],
            "finalizations": {"m": "note"}, "approvals": [{"functionId": "f", "approvedBy": "GM", "note": "ok"}],
            "gaps": [{"functionId": "g", "reason": "no-suitable-metric", "justification": "none fits"}],
            "enabledPolicyIds": ["b", "a"], "consideredCandidates": ["cand-2", "cand-1"]}
    one = pv.decisions_digest(base)
    assert one.startswith("sha256:") and one == pv.decisions_digest(dict(base))
    # order and whitespace never count; who confirmed an approval never counts
    reordered = {**base, "enabledPolicyIds": ["a", "b"], "consideredCandidates": ["cand-1", "cand-2"],
                 "answers": [{**base["answers"][0], "rationale": "fine as is"}],
                 "approvals": [{"functionId": "f", "approvedBy": "standing-policy (pending)", "note": "ok"}]}
    assert pv.decisions_digest(reordered) == one
    for change in ({"answers": []}, {"finalizations": {}}, {"gaps": []}, {"enabledPolicyIds": []},
                   {"consideredCandidates": []}, {"curveDecisions": []},
                   {"approvals": [{"functionId": "f", "note": "other"}]}):
        assert pv.decisions_digest({**base, **change}) != one
    assert pv.decisions_digest({}) == pv.decisions_digest({"answers": [], "gaps": []})
    assert pv.decision_inputs_of({"curve_decisions": [d], "standing_decisions": {"enabledIds": ["x"]},
                                  "meta": {"portfolioApprovals": [{"functionId": "f"}]}})["approvals"] == [{"functionId": "f"}]


def test_an_interactive_document_carries_the_ledger_when_given_the_fields():
    bundle = {"metricsByFunction": []}
    without = pv.build_interactive_provenance(bundle, {}, region={"code": "58"}, timestamp=TS)
    assert "metricLedger" not in without and without["metricLedgerNote"]
    fields = {"metric_config": {}, "curve_review": {}, "completed_metrics": {}, "reference_build": None,
              "owner_curve_decisions": [], "region_of_applicability": {"kind": "ecoregion", "code": "58"}}
    with_fields = pv.build_interactive_provenance(bundle, {}, region={"code": "58"}, timestamp=TS,
                                                  fields=fields)
    ledger = with_fields["metricLedger"]
    assert ledger["schema"] == pv.LEDGER_SCHEMA and ledger["region"]["code"] == "58"
    assert ledger["rows"] == [] and ledger["build"]["inputsDigest"] is None
