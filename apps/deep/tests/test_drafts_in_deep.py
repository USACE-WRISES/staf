"""DEEP runs Draft, Preliminary and Final versions, each labeled, and never a version StreamCurves'
Show in DEEP turned off (owner, 2026-10-08). An assessment's default version is its newest Final
one, else its newest Preliminary one, else its newest Draft, in the bake and live alike, and a
version the live library withholds (hidden, or a status DEEP does not run) drops its baked copy
in a local DEEP at once."""
from __future__ import annotations

import json

import pytest

from deep import assessments, config, library, session
from test_library_cache import _publish, _set_status


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    library.clear_cache()
    yield root
    library.clear_cache()


def _show(root, aid, version, visible):
    """What StreamCurves' Show in DEEP appends to the assessment's visibility.json."""
    path = root / "assessments" / aid / "visibility.json"
    doc = (json.loads(path.read_text("utf-8")) if path.is_file()
           else {"assessmentId": aid, "history": []})
    doc["history"].append({"version": version, "visibleInDeep": visible, "actor": "t"})
    path.write_text(json.dumps(doc), encoding="utf-8")


def _refs(bundles):
    return sorted(b["assessmentRef"] for b in bundles)


def test_deep_runs_three_statuses():
    assert library._ELIGIBLE == ("draft", "preliminary", "certified")
    assert session.LIFECYCLES == library._ELIGIBLE


def test_the_lifecycle_reads_three_states_and_defaults_to_preliminary():
    assert session.lifecycle_status({"lifecycle": "draft"}) == "draft"
    assert session.lifecycle_status({"lifecycle": "Certified"}) == "certified"
    assert session.lifecycle_status({"lifecycle": "retired"}) == "preliminary"
    assert session.lifecycle_status({}) == "preliminary"
    assert session.status_label("draft") == "Draft" and session.status_label("certified") == "Final"


def test_default_pointers_prefer_final_then_preliminary_then_draft():
    def recs(*pairs):
        return [{"assessmentId": "a", "version": v, "lifecycle": s} for v, s in pairs]

    assert library.default_pointers(recs((1, "preliminary"), (2, "draft")))["a"]["defaultVersion"] == 1
    assert library.default_pointers(recs((1, "certified"), (2, "preliminary"), (3, "draft")))["a"] == {
        "defaultVersion": 1, "latestCertified": 1, "latestPreliminary": 2, "latestDraft": 3}
    assert library.default_pointers(recs((1, "draft"), (4, "draft")))["a"]["defaultVersion"] == 4
    assert library.default_pointers(recs((2, "under_review")))["a"]["defaultVersion"] == 2, \
        "a status DEEP does not run reads as preliminary"
    assert library.default_pointers([]) == {}


def test_a_hidden_version_is_withheld_and_shown_again_it_returns(libroot):
    _publish(libroot, "a-test", 1)
    _publish(libroot, "a-test", 2, status="draft")
    bundles, withheld = library.served_and_withheld()
    assert _refs(bundles) == ["a-test@v1", "a-test@v2"] and not withheld
    assert [b["lifecycle"] for b in sorted(bundles, key=lambda b: b["version"])] == [
        "preliminary", "draft"]
    _show(libroot, "a-test", 1, False)
    bundles, withheld = library.served_and_withheld()
    assert _refs(bundles) == ["a-test@v2"] and withheld == {"a-test@v1"}
    _show(libroot, "a-test", 1, True)
    assert _refs(library.all_eligible_bundles()) == ["a-test@v1", "a-test@v2"], "the last record wins"


def test_a_version_with_a_status_deep_does_not_run_is_withheld_too(libroot):
    _publish(libroot, "a-test", 1)
    _set_status(libroot, "a-test", 1, "retired")
    assert library.served_and_withheld() == ([], frozenset({"a-test@v1"}))


def test_a_local_hide_drops_the_baked_copy(libroot, monkeypatch):
    baked = [{"assessmentId": "a-test", "assessmentRef": "a-test@v1", "version": 1,
              "lifecycle": "preliminary", "assessmentName": "baked a"},
             {"assessmentId": "b-test", "assessmentRef": "b-test@v1", "version": 1,
              "lifecycle": "preliminary", "assessmentName": "baked b"}]
    monkeypatch.setattr(config, "assessments_doc", lambda: {"assessments": baked})
    _publish(libroot, "a-test", 1)
    assert [r["assessmentRef"] for r in config._registry_records()] == ["a-test@v1", "b-test@v1"]
    _show(libroot, "a-test", 1, False)
    assert [r["assessmentRef"] for r in config._registry_records()] == ["b-test@v1"]
    assert config.default_ref_for("a-test") is None


def test_the_live_default_is_the_newest_reviewed_version_else_the_newest_draft(libroot):
    _publish(libroot, "nh-test", 1)
    _publish(libroot, "nh-test", 2, status="draft")
    assert config.default_ref_for("nh-test") == "nh-test@v1"
    _set_status(libroot, "nh-test", 1, "draft")
    assert config.default_ref_for("nh-test") == "nh-test@v2"
    _publish(libroot, "nh-test", 3, status="certified")
    assert config.default_ref_for("nh-test") == "nh-test@v3"
    _show(libroot, "nh-test", 3, False)
    assert config.default_ref_for("nh-test") == "nh-test@v2", "a hidden version is never the default"


def test_versions_are_offered_final_then_preliminary_then_draft(libroot):
    _publish(libroot, "nh-test", 1, status="draft")
    _publish(libroot, "nh-test", 2)
    _publish(libroot, "nh-test", 3, status="draft")
    _publish(libroot, "nh-test", 4, status="certified")
    entry = next(c for c in assessments.covering_refs(44.0, -71.0)
                 if c["assessmentId"] == "nh-test")
    assert entry["refs"] == ["nh-test@v4", "nh-test@v2", "nh-test@v3", "nh-test@v1"]
    assert entry["lifecycleByRef"]["nh-test@v3"] == "draft"
    assert "nh-test" in assessments.covering_assessments(44.0, -71.0)
