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
