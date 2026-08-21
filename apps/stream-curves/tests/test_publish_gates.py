"""Wave 4 publish gates: SELECT-01 portfolio approval, provenance visibility,
the reference-tier stamp, and the interactive provenance document."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from conftest import documented_exclusions
from streamcurves import library as lib
from streamcurves import provenance as pv
from streamcurves import session_io as sio
from streamcurves.deep_export import build_deep_assessment_bundle

REGION = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}


def _row(metric):
    return {
        "metric": metric,
        "curve_status": "complete",
        "stratum": np.nan,
        "curve_points": pd.DataFrame(
            {"metric_value": [0, 9, 25, 75], "index_score": [1, 0.7, 0.3, 0]}
        ),
    }


def _bundle(metrics=("perImperv",), function_label="Catchment hydrology", **meta_extra):
    rows = {m: _row(m) for m in metrics}
    mapping = pd.DataFrame({
        "metric_key": list(metrics),
        "discipline": ["Hydrology"] * len(metrics),
        "function_label": [function_label] * len(metrics),
        "sort_order": range(1, len(metrics) + 1),
    })
    meta = {"region": REGION,
            "functionCoverageExceptions": documented_exclusions(), **meta_extra}
    return build_deep_assessment_bundle(rows, mapping, {}, meta)


def _payload():
    fields = {
        "data": pd.DataFrame({"site": ["a"], "value": [1.0]}),
        "session_name": "gate-test",
        "region_of_applicability": REGION,
        "app_data_loaded": True,
    }
    return sio.dump_session_fields(fields, session_name="gate-test")


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


META = {"assessmentName": "Gate Test", "region": REGION, "author": "tester"}


def test_three_metric_function_is_refused_without_approval(libroot):
    bundle = _bundle(("m1", "m2", "m3"))
    with pytest.raises(ValueError, match="SELECT-01"):
        lib.publish_version("gate-test", dict(META), _payload(), bundle)
    # Refused before anything was written.
    assert not (libroot / "assessments" / "gate-test").exists()


def test_recorded_approval_publishes_and_lands_in_meta(libroot):
    bundle = _bundle(("m1", "m2", "m3"))
    fid = bundle["metricsByFunction"][0]["functionId"]
    meta = dict(META, portfolioApprovals=[
        {"functionId": fid, "approvedBy": "reviewer", "note": "complementary set"}])
    v = lib.publish_version("gate-test", meta, _payload(), bundle)
    written = json.loads((libroot / "assessments" / "gate-test" / f"v{v}" /
                          "meta.json").read_text(encoding="utf-8"))
    assert written["portfolioApprovals"][0]["approvedBy"] == "reviewer"


def test_provenance_state_is_visible_in_meta_and_catalog(libroot):
    lib.publish_version("no-prov", dict(META), _payload(), _bundle())
    meta = json.loads((libroot / "assessments" / "no-prov" / "v1" /
                       "meta.json").read_text(encoding="utf-8"))
    assert meta["provenance"] == "absent"
    catalog = json.loads((libroot / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog["assessments"] if e["assessmentId"] == "no-prov")
    assert entry["provenanceState"] == "absent"

    doc = pv.build_interactive_provenance(
        _bundle(), {"perImperv": {"status": "auto_ok", "decision": "auto_finalized"}},
        region=REGION, publisher="tester", session_name="gate-test")
    lib.publish_version("with-prov", dict(META), _payload(), _bundle(),
                        provenance=doc)
    catalog = json.loads((libroot / "catalog.json").read_text(encoding="utf-8"))
    entry = next(e for e in catalog["assessments"] if e["assessmentId"] == "with-prov")
    assert entry["provenanceState"] == "present"


def test_reference_tier_is_stamped_through_the_bundle():
    bundle = _bundle(referenceTier="best_available")
    assert bundle["referenceTier"] == "best_available"
    for block in bundle["metricsByFunction"]:
        for m in block["metrics"]:
            assert m["referenceTier"] == "best_available"


def test_reviewer_decisions_merge_into_records_and_queue():
    doc = pv.build_interactive_provenance(
        _bundle(("m1", "m2", "m3")),
        {"m2": {"status": "degenerate", "decision": "pending",
                "reasons": ["degenerate"]}},
        region=REGION, publisher="tester", session_name="s")
    assert doc["reviewQueue"]["counts"]["open"] >= 1
    decisions = [{"rule_id": "CURVE-07", "subject": "m2", "action": "accept",
                  "rationale": "Accepted as preliminary with the flag.",
                  "reviewer": "owner", "date": "2026-08-21"}]
    merged = pv.apply_reviewer_decisions(doc, decisions)
    rec = next(r for r in merged["records"]
               if r["rule_id"] == "CURVE-07" and r["subject"] == "m2")
    assert rec["reviewer"] == "owner"
    assert rec["reviewer_action"] == "accept"
    item = next(i for i in merged["reviewQueue"]["items"] if i["subject"] == "m2")
    assert item["status"] == "resolved"
    assert merged["reviewQueue"]["counts"]["open"] == 0
    assert merged["reviewerDecisionsUnmatched"] == []


def test_unmatched_reviewer_decisions_are_reported_and_bad_actions_raise():
    doc = pv.build_interactive_provenance(
        _bundle(), {}, region=REGION, publisher="t", session_name="s")
    merged = pv.apply_reviewer_decisions(
        doc, [{"rule_id": "STRAT-09", "subject": "Nope", "action": "reject",
               "rationale": "x"}])
    assert merged["reviewerDecisionsUnmatched"] == [
        {"rule_id": "STRAT-09", "subject": "Nope", "action": "reject"}]
    with pytest.raises(ValueError, match="unknown reviewer action"):
        pv.apply_reviewer_decisions(doc, [{"rule_id": "X", "subject": "y",
                                           "action": "yolo"}])


def test_interactive_provenance_accounts_for_every_rule():
    from streamcurves import methodology
    doc = pv.build_interactive_provenance(
        _bundle(("m1", "m2", "m3")),
        {"m1": {"status": "auto_ok", "decision": "auto_finalized"},
         "m2": {"status": "degenerate", "decision": "pending",
                "reasons": ["degenerate"]}},
        region=REGION, screening_preset="functional",
        publisher="tester", session_name="s")
    applied = set(doc["rules_applied"])
    not_evaluated = {r["rule_id"] for r in doc["rules_not_evaluated"]}
    assert applied | not_evaluated == set(methodology.rule_ids())
    assert {"CURVE-01", "CURVE-07", "SELECT-01", "REF-01"} <= applied
    assert doc["manifest"]["mode"] == "interactive_session"
    assert doc["manifest"]["inputsDigestNote"]
    # The flagged, undecided curve is in the queue.
    items = doc["reviewQueue"]["items"]
    assert any(i["subject"] == "m2" for i in items)
