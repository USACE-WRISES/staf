"""Carrying an agent build's provenance through an interactive publish.

The loop this protects: the Region builder stages an assessment with a full
audit chain; a human opens it, edits anything, and publishes from the Publish
page. The published version must carry the build's manifest, decision records
and review queue verbatim, plus an interactiveRevisions entry disclosing the
edit, with any pending standing decisions confirmed under the publisher's name
before anything is written."""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from conftest import documented_exclusions
from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import session_io as sio
from streamcurves.deep_export import build_deep_assessment_bundle
from views import assessment_publish as ap
from views.state import AppState

PENDING = "standing-policy:ref02-accept-best-available (pending owner confirmation)"
REGION = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}


def _doc(pending: bool = True) -> dict:
    reviewer = PENDING if pending else "owner"
    return {
        "schemaVersion": 2,
        "inputsDigest": "sha256:abc",
        "manifest": {
            "region": {"l3_code": "55"},
            "standingDecisions": {"policyVersion": "1.0",
                                  "enabledIds": ["ref02-accept-best-available"],
                                  "confirmedBy": None, "confirmedAt": None},
        },
        "rules_applied": ["REF-02"],
        "rules_not_evaluated": [],
        "records": [{
            "rule_id": "REF-02", "subject": "reference_screen",
            "reviewer": reviewer,
            "reviewer_action": "accept",
            "reviewer_rationale": "Best-available reference accepted.",
            "reviewer_rationale_origin": "standing_policy:1.0",
            "reviewer_asserts": {"reference_tier": "best_available"},
            "computed": {"reference_tier": "best_available"},
        }],
        "counts": {"total": 1},
        "reviewQueue": {"items": [{"item_id": "REF-02:reference_screen",
                                   "reviewer": reviewer}]},
        "version": 1, "updatedAt": "old", "contentDigest": "sha256:stale",
    }


_ORIGIN = {
    "kind": "staged", "library_id": None, "version": 2,
    "staged_path": "/runs/l3-55/library/assessments/ecbp/v2",
    "run_dir": "/runs/l3-55", "content_digest": "sha256:staged",
    "loaded_at": "2026-08-27T00:00:00+00:00",
    "baselines": {"data_fingerprint": "f1", "mapping_digest": "m1",
                  "region_code": "55", "curve_fingerprints": {"a": "1", "b": "2"}},
}


# --------------------------------------------------------------------------- #
# build_carried_provenance
# --------------------------------------------------------------------------- #
def test_the_carried_doc_keeps_the_build_record_verbatim():
    src = _doc()
    before = copy.deepcopy(src)
    out = pv.build_carried_provenance(src, origin=_ORIGIN, publisher="jess",
                                      session_name="ecbp edit",
                                      changes={"dataFingerprintChanged": True},
                                      timestamp="2026-08-27T01:00:00+00:00")
    assert src == before, "the source document must not be mutated"
    for key in ("manifest", "records", "reviewQueue", "rules_applied",
                "rules_not_evaluated", "counts", "inputsDigest"):
        assert out[key] == src[key], key
    for stale in ("version", "updatedAt", "contentDigest"):
        assert stale not in out
    (entry,) = out["interactiveRevisions"]
    assert entry["editedBy"] == "jess"
    assert entry["basedOn"]["kind"] == "staged"
    assert entry["basedOn"]["contentDigest"] == "sha256:staged"
    assert entry["basedOn"]["inputsDigest"] == "sha256:abc"
    assert entry["changes"] == {"dataFingerprintChanged": True}


def test_a_second_revision_chains_instead_of_nesting():
    first = pv.build_carried_provenance(_doc(), origin=_ORIGIN, publisher="jess",
                                        timestamp="t1")
    second = pv.build_carried_provenance(first, origin={**_ORIGIN, "kind": "library"},
                                         publisher="sam", timestamp="t2")
    kinds = [e["basedOn"]["kind"] for e in second["interactiveRevisions"]]
    assert kinds == ["staged", "library"]
    assert [e["editedBy"] for e in second["interactiveRevisions"]] == ["jess", "sam"]


def test_revision_changes_flags_only_what_moved():
    changes = pv.revision_changes(
        _ORIGIN, content_digest="sha256:staged", data_fingerprint="f1",
        mapping_digest="m1", region_code="55",
        curve_fingerprints={"a": "1", "b": "2"})
    assert changes["contentDigestMatches"] is True
    assert not changes["dataFingerprintChanged"]
    assert changes["curvesAdded"] == [] and changes["curvesChanged"] == []

    changes = pv.revision_changes(
        _ORIGIN, content_digest="sha256:other", data_fingerprint="f2",
        mapping_digest="m1", region_code="55",
        curve_fingerprints={"a": "1", "b": "9", "c": "3"})
    assert changes["contentDigestMatches"] is False
    assert changes["dataFingerprintChanged"] is True
    assert changes["curvesChanged"] == ["b"]
    assert changes["curvesAdded"] == ["c"]
    assert changes["curvesRemoved"] == []


def test_revision_changes_treats_an_unknown_baseline_as_no_change():
    origin = {**_ORIGIN, "baselines": {}}
    changes = pv.revision_changes(origin, data_fingerprint="f2", mapping_digest="m2",
                                  region_code="99", curve_fingerprints={"a": "1"})
    assert not changes["dataFingerprintChanged"]
    assert not changes["mappingChanged"]
    assert not changes["regionChanged"]
    assert changes["curvesAdded"] == ["a"], "no prior fingerprints reads as added"


# --------------------------------------------------------------------------- #
# confirm_pending_decisions (the one implementation promote and the app share)
# --------------------------------------------------------------------------- #
def test_confirm_rewrites_pending_reviewers_and_stamps_the_policy_block():
    doc = _doc()
    assert dec.is_pending(doc)
    out, applied = dec.confirm_pending_decisions(doc, reviewer="jess", date="d1")
    assert out is doc and applied == []
    assert not dec.is_pending(doc)
    assert doc["records"][0]["reviewer"] == "jess"
    assert doc["records"][0]["reviewed_at"] == "d1"
    assert doc["reviewQueue"]["items"][0]["reviewer"] == "jess"
    sd = doc["manifest"]["standingDecisions"]
    assert sd["confirmedBy"] == "jess" and sd["confirmedAt"] == "d1"
    # the rationale origin still says the rationale came from the policy
    assert doc["records"][0]["reviewer_rationale_origin"] == "standing_policy:1.0"


def test_confirm_refuses_asserts_that_contradict_the_record():
    doc = _doc()
    doc["records"][0]["reviewer_asserts"] = {"reference_tier": "functional"}
    with pytest.raises(ValueError, match="reference_tier"):
        dec.confirm_pending_decisions(doc, reviewer="jess", date="d1")


def test_confirm_refuses_a_scope_changing_override():
    with pytest.raises(ValueError, match="changes the scope"):
        dec.confirm_pending_decisions(
            _doc(), reviewer="jess", date="d1",
            overrides={"REF-02:reference_screen": ("reject", "changed my mind")})


def test_confirm_refuses_an_override_naming_an_unknown_item():
    with pytest.raises(ValueError, match="not on the record"):
        dec.confirm_pending_decisions(
            _doc(), reviewer="jess", date="d1",
            overrides={"CURVE-04:nope": ("accept", "who is this")})


def test_confirm_turns_an_owner_draft_origin_into_owner_approved():
    doc = _doc()
    doc["records"][0]["reviewer_rationale_origin"] = "owner_written_pending_owner_approval"
    dec.confirm_pending_decisions(doc, reviewer="jess", date="d1")
    assert doc["records"][0]["reviewer_rationale_origin"] == "owner_written_owner_approved"


# --------------------------------------------------------------------------- #
# The origin record and its baselines (views/assessment_publish)
# --------------------------------------------------------------------------- #
def _state_with_curves() -> AppState:
    state = AppState.fresh()
    state.data_fingerprint.set("f1")
    state.region_of_applicability.set(REGION)
    state.discipline_function_mapping.set({"perImperv": "Catchment hydrology"})
    state.curve_review.set({"a": {"status": "auto_ok", "decision": "auto_finalized"},
                            "b": {"status": "data_review", "decision": "pending"}})
    return state


def test_build_origin_captures_the_loaded_state_as_baselines():
    state = _state_with_curves()
    origin = ap.build_origin(state, kind="staged", staged_path="/x", run_dir="/y",
                             content_digest="sha256:z", loaded_at="t0")
    base = origin["baselines"]
    assert origin["kind"] == "staged" and origin["content_digest"] == "sha256:z"
    assert base["data_fingerprint"] == "f1"
    assert base["region_code"] == "55"
    assert set(base["curve_fingerprints"]) == {"a", "b"}
    assert base["mapping_digest"]


def test_the_origin_carries_the_select01_approvals():
    """The publish form builds fresh meta, so the origin must carry the staged
    portfolioApprovals or an opened agent build with a >2-metric function is
    refused by the very gate its own build already satisfied (found live on the
    pooled ECBP end-to-end, where bed-composition-bedform-dynamics gets a third
    metric)."""
    state = _state_with_curves()
    approvals = [{"functionId": "bed-composition-bedform-dynamics",
                  "approvedBy": "owner"}]
    origin = ap.build_origin(state, kind="staged", portfolio_approvals=approvals)
    assert origin["portfolio_approvals"] == approvals
    assert ap.build_origin(state, kind="staged")["portfolio_approvals"] is None


def test_the_publish_meta_inherits_the_origin_approvals():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "views" / "publish.py").read_text(
        encoding="utf-8")
    assert 'meta["portfolioApprovals"] = origin["portfolio_approvals"]' in text


def test_the_publish_form_rerenders_when_a_different_session_is_opened():
    """app_data_loaded stays True across opens, so with session_name isolated
    the form kept the previous session's name and a publish landed under the
    old assessment's id (found live on the end-to-end verification)."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "views" / "publish.py").read_text(
        encoding="utf-8")
    m = re.search(r"def publish_body\(\):.*?(?=\n    def |\n    @)", text, re.S)
    assert m, "publish_body not found"
    body = m.group(0)
    assert "state.session_name()" in body
    assert "reactive.isolate" not in body, \
        "the form's defaults must be render dependencies, not isolated reads"


def test_origin_changes_reports_the_edit_and_only_the_edit():
    from shiny import reactive

    state = _state_with_curves()
    origin = ap.build_origin(state, kind="staged", content_digest="sha256:z")
    with reactive.isolate():
        review = dict(state.curve_review())
    review["b"] = {"status": "auto_ok", "decision": "reviewer_finalized"}
    state.curve_review.set(review)
    changes = ap.origin_changes(state, origin, content_digest="sha256:other")
    assert changes["curvesChanged"] == ["b"]
    assert changes["curvesAdded"] == [] and changes["curvesRemoved"] == []
    assert not changes["dataFingerprintChanged"]
    assert not changes["regionChanged"]
    assert changes["contentDigestMatches"] is False


# --------------------------------------------------------------------------- #
# Session persistence (schema v2, additive)
# --------------------------------------------------------------------------- #
def test_the_origin_and_carried_doc_survive_save_and_reopen():
    payload = sio.dump_session_fields(
        {"session_name": "carry", "assessment_source": _ORIGIN,
         "source_provenance": _doc(pending=False)},
        session_name="carry")
    assert "assessment_source" in sio.SESSION_FIELDS
    assert "source_provenance" in sio.SESSION_FIELDS
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert back["assessment_source"] == _ORIGIN
    assert back["source_provenance"]["records"][0]["rule_id"] == "REF-02"


def test_a_session_written_before_the_origin_reads_as_none():
    payload = sio.dump_session_fields({"session_name": "old"}, session_name="old")
    payload["fields"].pop("assessment_source", None)
    payload["fields"].pop("source_provenance", None)
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert back.get("assessment_source") is None
    assert back.get("source_provenance") is None


# --------------------------------------------------------------------------- #
# The published version carries the record (library integration)
# --------------------------------------------------------------------------- #
@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    return root


def _bundle() -> dict:
    rows = {
        "perImperv": {
            "metric": "perImperv",
            "curve_status": "complete",
            "stratum": np.nan,
            "curve_points": pd.DataFrame(
                {"metric_value": [0, 9, 25, 75], "index_score": [1, 0.7, 0.3, 0]}
            ),
        }
    }
    mapping = pd.DataFrame(
        {"metric_key": ["perImperv"], "discipline": ["Hydrology"],
         "function_label": ["Catchment hydrology"], "sort_order": [1]})
    return build_deep_assessment_bundle(
        rows, mapping, {},
        {"region": REGION, "functionCoverageExceptions": documented_exclusions()})


def test_a_confirmed_carried_doc_publishes_with_the_build_record_on_disk(libroot):
    src = _doc()
    carried = pv.build_carried_provenance(src, origin=_ORIGIN, publisher="jess",
                                          changes={}, timestamp="t1")
    dec.confirm_pending_decisions(carried, reviewer="jess", date="t1")
    payload = sio.dump_session_fields({"session_name": "ecbp"}, session_name="ecbp")
    version = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                                  payload, _bundle(), provenance=carried)
    on_disk = lib.load_version_provenance("ecbp", version)
    assert on_disk["manifest"]["region"] == src["manifest"]["region"]
    assert [r["rule_id"] for r in on_disk["records"]] == ["REF-02"]
    assert on_disk["records"][0]["reviewer"] == "jess"
    assert on_disk["interactiveRevisions"][0]["editedBy"] == "jess"
    assert not dec.is_pending(on_disk)
    assert lib.version_content_digest("ecbp", version) == \
        lib.load_version_bundle("ecbp", version).get("contentDigest")
