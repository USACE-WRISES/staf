"""Tests for library schema v2 — content fingerprints, the append-only lifecycle
status record, catalog lifecycle pointers, v1 back-compat, and the canonical-publish
gate (Part F of the StreamCurves revision plan).

STAF_LIBRARY_ROOT points the library at a tmp dir so these never touch the real
apps/library/ tree.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from streamcurves import library as lib
from streamcurves import session_io as sio
from streamcurves.deep_export import build_deep_assessment_bundle

REGION = {"kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains"}


def _bundle(y2: float = 0.3) -> dict:
    """A minimal DEEP bundle; ``y2`` tunes one curve point so two bundles can differ."""
    rows = {
        "perImperv": {
            "metric": "perImperv",
            "curve_status": "complete",
            "stratum": np.nan,
            "curve_points": pd.DataFrame(
                {"metric_value": [0, 9, 25, 75], "index_score": [1, 0.7, y2, 0]}
            ),
        }
    }
    mapping = pd.DataFrame(
        {
            "metric_key": ["perImperv"],
            "discipline": ["Hydrology"],
            "function_label": ["Catchment hydrology"],
            "sort_order": [1],
        }
    )
    return build_deep_assessment_bundle(rows, mapping, {}, {"region": REGION})


def _session_payload() -> dict:
    return sio.dump_session_fields(
        {"session_name": "ecbp-draft", "region_of_applicability": REGION, "app_data_loaded": True},
        session_name="ecbp-draft",
    )


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    monkeypatch.delenv("STAF_LIBRARY_MAINTAINER", raising=False)
    return root


# --------------------------------------------------------------------------- #
# Content digest
# --------------------------------------------------------------------------- #
def test_content_digest_is_stable_across_key_order():
    b = _bundle()
    reordered = {k: b[k] for k in reversed(list(b.keys()))}
    assert lib.content_digest(b) == lib.content_digest(reordered)
    assert lib.content_digest(b).startswith("sha256:")


def test_content_digest_changes_when_curves_change():
    assert lib.content_digest(_bundle(0.3)) != lib.content_digest(_bundle(0.9))


def test_content_digest_ignores_polygon_and_provenance():
    """The fingerprint is analytical: adding a library block, updatedAt, or a region
    outline polygon must not move it, but the region *code* is part of it."""
    b = _bundle()
    base = lib.content_digest(b)

    noisy = dict(b)
    noisy["library"] = {"version": 7, "updatedAt": "2026-07-11T00:00:00Z", "author": "x"}
    noisy["region"] = {**REGION, "polygon": {"type": "Polygon", "coordinates": [[[0, 0]]]}}
    noisy["contentDigest"] = "sha256:whatever"
    assert lib.content_digest(noisy) == base

    diff_region = dict(b, region={"kind": "state", "code": "OH", "name": "Ohio"})
    assert lib.content_digest(diff_region) != base


def test_publish_stamps_content_digest_on_bundle_library_and_meta(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    bundle = lib.load_version_bundle("ecbp", 1)
    digest = bundle["contentDigest"]
    assert digest.startswith("sha256:")
    assert bundle["library"]["contentDigest"] == digest
    meta = json.loads((libroot / "assessments" / "ecbp" / "v1" / "meta.json").read_text("utf-8"))
    assert meta["contentDigest"] == digest
    # It is the digest of the analytical content (region polygon excluded).
    assert lib.content_digest(bundle) == digest


# --------------------------------------------------------------------------- #
# Lifecycle status record
# --------------------------------------------------------------------------- #
def test_publish_defaults_to_preliminary(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    assert lib.version_status("ecbp", 1) == "preliminary"


def test_status_record_roundtrips_and_is_append_only(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    before = lib.read_status("ecbp")["history"]
    assert len(before) == 1 and before[0]["status"] == "preliminary"

    lib.set_version_status("ecbp", 1, "under_review", "reviewer-amy", note="in EcoPCX queue")
    lib.set_version_status("ecbp", 1, "certified", "maintainer-jane", note="EcoPCX complete")
    assert lib.version_status("ecbp", 1) == "certified"

    history = lib.read_status("ecbp")["history"]
    assert [h["status"] for h in history] == ["preliminary", "under_review", "certified"]
    # append-only: the original record is preserved verbatim
    assert history[0] == before[0]
    last = history[-1]
    assert set(last) == {"version", "status", "actor", "timestamp", "note"}
    assert last["actor"] == "maintainer-jane" and last["note"] == "EcoPCX complete"


def test_status_change_does_not_change_content_digest(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    digest = lib.load_version_bundle("ecbp", 1)["contentDigest"]
    lib.set_version_status("ecbp", 1, "certified", "maintainer-jane")
    assert lib.load_version_bundle("ecbp", 1)["contentDigest"] == digest


def test_set_version_status_validates_enum_actor_and_version(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    with pytest.raises(ValueError, match="Unknown status"):
        lib.set_version_status("ecbp", 1, "approved", "jane")
    with pytest.raises(ValueError, match="actor"):
        lib.set_version_status("ecbp", 1, "certified", "")
    with pytest.raises(ValueError, match="no version"):
        lib.set_version_status("ecbp", 99, "certified", "jane")


# --------------------------------------------------------------------------- #
# Catalog lifecycle pointers
# --------------------------------------------------------------------------- #
def _entry(aid: str) -> dict:
    return next(a for a in lib.list_assessments() if a["assessmentId"] == aid)


def test_catalog_carries_lifecycle_pointers(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    e = _entry("ecbp")
    assert e["latestPreliminary"] == 1
    assert e["latestCertified"] == 0
    assert e["defaultVersion"] == 1  # only a preliminary exists
    assert e["contentDigest"] == lib.load_version_bundle("ecbp", 1)["contentDigest"]

    # Second version, then certify v1: certified wins for defaultVersion even though a
    # newer preliminary exists, and contentDigest tracks the *latest* version.
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle(0.5))
    lib.set_version_status("ecbp", 1, "certified", "maintainer-jane")
    e = _entry("ecbp")
    assert e["latestVersion"] == 2
    assert e["latestPreliminary"] == 2
    assert e["latestCertified"] == 1
    assert e["defaultVersion"] == 1
    assert e["contentDigest"] == lib.load_version_bundle("ecbp", 2)["contentDigest"]


def test_catalog_and_manifest_schema_version_bumped(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    assert lib.read_catalog()["schemaVersion"] == 2
    assert lib.read_manifest("ecbp")["schemaVersion"] == 2


# --------------------------------------------------------------------------- #
# v1 back-compat (a v1 catalog/manifest lacking status reads as all-preliminary)
# --------------------------------------------------------------------------- #
def test_v1_catalog_and_manifest_read_as_all_preliminary(libroot):
    aid = "legacy-ecbp"
    adir = libroot / "assessments" / aid
    (adir / "v1").mkdir(parents=True)
    # A schema-v1 manifest: no contentDigest on versions, and there is no status.json.
    (adir / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "assessmentId": aid,
                "assessmentName": "Legacy ECBP",
                "region": REGION,
                "stateCode": "",
                "stateName": "",
                "latestVersion": 1,
                "versions": [{"version": 1, "updatedAt": "2026-01-01T00:00:00Z", "author": "old"}],
            }
        ),
        encoding="utf-8",
    )
    # A schema-v1 catalog with only the old fields still reads back unchanged.
    (libroot / "catalog.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-01-01T00:00:00Z",
                "assessments": [
                    {
                        "assessmentId": aid,
                        "assessmentName": "Legacy ECBP",
                        "region": REGION,
                        "stateCode": "",
                        "stateName": "",
                        "latestVersion": 1,
                        "latestUpdatedAt": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert lib.read_catalog()["schemaVersion"] == 1  # v1 file read as-is
    assert lib.read_manifest(aid)["schemaVersion"] == 1
    assert lib.version_status(aid, 1) == "preliminary"  # no status.json -> preliminary

    # Regenerating upgrades the catalog to v2 pointers, treating the v1 version as
    # preliminary and leaving contentDigest empty (none was recorded in v1).
    lib._regenerate_catalog()
    cat = lib.read_catalog()
    assert cat["schemaVersion"] == 2
    e = _entry(aid)
    assert e["latestPreliminary"] == 1
    assert e["latestCertified"] == 0
    assert e["defaultVersion"] == 1
    assert e["contentDigest"] is None


# --------------------------------------------------------------------------- #
# Canonical-publish gate
# --------------------------------------------------------------------------- #
def test_can_publish_canonical_requires_flag_writable_and_maintainer(libroot, monkeypatch):
    # writable() is True (libroot exists), but the flag is unset by the fixture.
    assert lib.can_publish_canonical("jane") is False
    reason = lib.publish_gate_reason("jane")
    assert reason and "STAF_LIBRARY_PUBLISH" in reason

    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    # flag + writable + maintainer arg -> allowed
    assert lib.can_publish_canonical("jane") is True
    assert lib.publish_gate_reason("jane") is None
    # flag + writable but no maintainer -> blocked with a maintainer message
    assert lib.can_publish_canonical("") is False
    assert lib.can_publish_canonical(None) is False
    assert "maintainer" in lib.publish_gate_reason("").lower()
    # maintainer can arrive via the environment instead of the argument
    monkeypatch.setenv("STAF_LIBRARY_MAINTAINER", "env-jane")
    assert lib.can_publish_canonical() is True


def test_gate_blocks_when_not_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "absent"))
    monkeypatch.setenv("STAF_LIBRARY_PUBLISH", "1")
    assert lib.writable() is False
    assert lib.can_publish_canonical("jane") is False
    assert "not writable" in lib.publish_gate_reason("jane")


def test_publish_version_itself_is_not_env_gated(libroot):
    """Ordinary local users (no STAF_LIBRARY_PUBLISH) can still run publish_version; the
    env gate is a UI-layer guard, not a hard block in the writer."""
    assert "STAF_LIBRARY_PUBLISH" not in __import__("os").environ
    v = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    assert v == 1


# --------------------------------------------------------------------------- #
# Validation records + certification gating
# --------------------------------------------------------------------------- #
def test_validation_defaults_unvalidated(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    assert lib.version_validation_state("ecbp", 1) == "unvalidated"


def test_cannot_mark_validated_without_a_record(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    with pytest.raises(ValueError, match="without at least one validation record"):
        lib.set_version_validation("ecbp", 1, "validated", {"n_checks": 1}, "maintainer-jane")


def test_validation_record_then_validated(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    lib.add_validation_record("ecbp", 1,
                              {"method": "independent recompute", "outcome": "match"},
                              actor="checker-amy", note="spot check")
    state = lib.set_version_validation("ecbp", 1, "validated",
                                       {"n_checks": 1, "outcome": "match"}, "maintainer-jane")
    assert state == "validated"
    assert lib.version_validation_state("ecbp", 1) == "validated"
    assert lib.version_validation_summary("ecbp", 1)["n_checks"] == 1
    # records + history are append-only
    doc = lib.read_validation("ecbp")
    assert len(doc["records"]) == 1 and doc["records"][0]["actor"] == "checker-amy"
    assert len(doc["history"]) == 1


def test_catalog_carries_validation_state(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    lib.add_validation_record("ecbp", 1, {"method": "recompute"}, actor="amy")
    lib.set_version_validation("ecbp", 1, "validated", {"n_checks": 1}, "jane")
    e = _entry("ecbp")
    assert e["validationState"] == "validated"
    assert e["validationSummary"]["n_checks"] == 1


def test_publish_final_writer_sequence(libroot):
    """The Publish page's Final path: publish (seeds preliminary), attach a
    validation record, mark validated, then certify. Pins the exact call order
    the UI runs and the catalog pointers DEEP consumes."""
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                        _session_payload(), _bundle())
    lib.add_validation_record(
        "ecbp", 1,
        {"method": "independent recompute", "checker": "amy", "outcome": "match"},
        actor="maintainer-jane", note="publish-time validation")
    n_records = len(lib._validation_records_for("ecbp", 1))
    lib.set_version_validation("ecbp", 1, "validated",
                               {"n_records": n_records}, "maintainer-jane")
    lib.set_version_status("ecbp", 1, "certified", "maintainer-jane",
                           note="Published as Final; validated and certified at publish.")

    assert lib.version_status("ecbp", 1) == "certified"
    assert lib.version_validation_state("ecbp", 1) == "validated"
    e = _entry("ecbp")
    assert e["latestCertified"] == 1
    assert e["defaultVersion"] == 1
    assert e["validationState"] == "validated"


def test_revision_stamps_supersedes_version(libroot):
    lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION}, _session_payload(), _bundle())
    v2 = lib.publish_version("ecbp", {"assessmentName": "ECBP", "region": REGION},
                             _session_payload(), _bundle(0.5))
    assert v2 == 2
    b2 = lib.load_version_bundle("ecbp", 2)
    assert b2["library"]["supersedesVersion"] == 1
    b1 = lib.load_version_bundle("ecbp", 1)
    assert b1["library"]["supersedesVersion"] is None
    # the prior version's status is NOT auto-changed by a revision
    assert lib.version_status("ecbp", 1) == "preliminary"
