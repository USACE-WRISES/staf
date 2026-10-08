"""The DEEP assessment file: the STAF assessment file EASI and SFARI write too (owner, 2026-10-05),
with the provenance fields round-tripping beside the assessment, DEEP's own v2 and v1 files still
opening, and a stable content digest (Part D1/D2)."""
from __future__ import annotations

import json

import pytest

from deep import session
from deep._vendor.staf_workbook import assessment_file
from deep._vendor.staf_workbook.model.scenarios import ScenarioSet


def _bundle(version=2, status=None):
    b = {
        "assessmentId": "demo-assess",
        "assessmentName": "Demo",
        "metricsByFunction": [
            {"functionId": "catchment-hydrology",
             "metrics": [{"metricId": "m1",
                          "curve": {"points": [{"x": 0, "y": 0}, {"x": 10, "y": 1}]}}]},
        ],
        "library": {"libraryId": "demo-assess", "version": version},
    }
    if status:
        b["status"] = status
    return b


def _values(mv):
    return assessment_file.scenarios_block(ScenarioSet(), {"measured_values": mv})


def _existing(st):
    return assessment_file.scenario_set(st["scenarios"]).baseline.state["measured_values"]


def test_the_file_round_trips_its_provenance():
    bundle = _bundle(version=3, status="certified")
    region = {"level3": {"code": "55", "name": "Eastern Corn Belt Plains"},
              "state": {"code": "OH", "abbr": "OH", "name": "Ohio"}}
    delin = {"delineation": {"comid": 42, "snapped_lat": 40.0, "snapped_lon": -83.5}}
    mv = {"m1": {"value": 5.0, "na": False, "note": ""}}

    text = session.dump(delin, bundle, _values(mv), region=region, completeness="complete",
                        result_state="final")
    raw = json.loads(text)
    assert raw["format"] == "staf-assessment" and raw["tool"] == "DEEP"
    assert list(raw["toolData"]) == ["assessment", "provenance"] and "measured_values" not in raw

    st = session.load(text)
    prov = st["provenance"]
    assert prov["assessmentId"] == "demo-assess"
    assert prov["version"] == 3
    assert prov["lifecycle"] == "certified"
    assert prov["region"]["state"]["code"] == "OH"
    assert prov["region"]["level3"]["code"] == "55"
    assert prov["completeness"] == "complete"
    assert prov["resultState"] == "final"
    assert prov["contentDigest"].startswith("sha256:")
    # The embedded bundle + measured values still resume standalone.
    assert st["assessment"]["assessmentId"] == "demo-assess"
    assert _existing(st)["m1"]["value"] == 5.0
    assert st["delineation"] == delin


def test_dump_positional_only_still_valid():
    # A caller passing only the positional args writes a valid file; provenance simply reflects
    # the bundle and an unresolved region.
    st = session.load(session.dump({}, _bundle(version=1), _values({})))
    assert st["provenance"]["version"] == 1
    assert st["provenance"]["region"] == {"level3": None, "state": None}


def test_a_v2_file_from_before_the_shared_format_still_opens():
    region = {"level3": {"code": "55", "name": "Eastern Corn Belt Plains"}, "state": None}
    sset = ScenarioSet()
    sset.add("Restore riparian", "")
    sset.current.state = {"measured_values": {"m1": {"value": 9.0}}}
    v2 = json.dumps({"schemaVersion": 2, "method": "DEEP", "delineation": {"delineation": {"comid": 7}},
                     "assessment": _bundle(version=2), "measured_values": {"m1": {"value": 2.0}},
                     "provenance": {"assessmentId": "demo-assess", "version": 2, "region": region},
                     "scenarios": sset.to_json()})
    st = session.load(v2)
    assert st["provenance"]["region"] == region and st["assessment"]["assessmentId"] == "demo-assess"
    back = assessment_file.scenario_set(st["scenarios"])
    assert back.baseline.state == {"measured_values": {"m1": {"value": 2.0}}}
    assert back.items[1].state == {"measured_values": {"m1": {"value": 9.0}}} and back.active == back.items[1].id


def test_v1_session_loads_via_migration():
    bundle = _bundle(version=1)
    v1 = json.dumps({
        "schemaVersion": 1,
        "method": "DEEP",
        "delineation": {"delineation": {"comid": 7}},
        "assessment": bundle,
        "measured_values": {"m1": {"value": 2.0}},
    })
    st = session.load(v1)
    prov = st["provenance"]
    assert prov["migratedFrom"] == 1
    assert prov["assessmentId"] == "demo-assess"
    assert prov["version"] == 1
    assert prov["lifecycle"] == "preliminary"                 # default when absent
    assert prov["region"] == {"level3": None, "state": None}  # v1 never resolved a region
    # Embedded bundle + values preserved so the current rules reconstruct scores.
    assert st["assessment"]["assessmentId"] == "demo-assess"
    assert _existing(st)["m1"]["value"] == 2.0


def test_versionless_session_migrates_as_v1():
    v0 = json.dumps({"assessment": _bundle(version=1), "measured_values": {}, "delineation": {}})
    st = session.load(v0)
    assert st["provenance"]["migratedFrom"] == 1


def test_an_unreadable_schema_version_is_refused():
    with pytest.raises(assessment_file.AssessmentFileError, match="schema version"):
        session.load(json.dumps({"schemaVersion": "two", "method": "DEEP"}))


def test_a_sfari_or_easi_file_is_refused_with_the_tool_to_open_it_in():
    with pytest.raises(assessment_file.AssessmentFileError, match="Open it in SFARI"):
        session.load(json.dumps({"schemaVersion": 1, "method": "SFARI", "metric_scores": {}}))
    with pytest.raises(assessment_file.AssessmentFileError, match="Open it in EASI"):
        session.load(assessment_file.dump("EASI", {}, {}, None))


def test_content_digest_is_stable_and_bundle_sensitive():
    # Same content, different key order (incl. nested) -> same digest.
    d1 = {"a": 1, "b": {"x": 1, "y": 2}}
    d2 = {"b": {"y": 2, "x": 1}, "a": 1}
    assert session.content_digest(d1) == session.content_digest(d2)
    # A change to the bundle changes the digest.
    assert session.content_digest(_bundle(version=1)) != session.content_digest(_bundle(version=2))
    # Empty bundle -> empty digest.
    assert session.content_digest({}) == ""


def test_lifecycle_status_defaults_and_sources():
    assert session.lifecycle_status({"library": {"status": "certified"}}) == "certified"
    assert session.lifecycle_status({"status": "CERTIFIED"}) == "certified"
    assert session.lifecycle_status({}) == "preliminary"
    assert session.lifecycle_status({"status": "weird"}) == "preliminary"
    # DEEP runs Drafts too since 2026-10-08 (owner), labeled: a draft reads as a draft, and a
    # status DEEP does not run still reads as preliminary
    assert session.lifecycle_status({"status": "draft"}) == "draft"
    assert session.lifecycle_status({"lifecycle": "retired"}) == "preliminary"


def test_status_labels_render_the_writer_vocabulary():
    assert session.status_label("certified") == "Final"
    assert session.status_label("preliminary") == "Preliminary"
    assert session.status_label("draft") == "Draft"
    assert session.status_label("weird_future_state") == "Weird_Future_State"
    assert session.status_label(None) == "Preliminary"


def test_bundle_digest_prefers_publisher_digest():
    b = _bundle(version=1)
    b["contentDigest"] = "sha256:canonical-upstream"
    assert session.bundle_digest(b) == "sha256:canonical-upstream"
    # Absent a publisher digest, falls back to the local content digest.
    assert session.bundle_digest(_bundle(version=1)).startswith("sha256:")
