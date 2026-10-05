"""The STAF assessment file (owner, 2026-10-05): Save writes and Open reads one JSON structure in
EASI, SFARI and DEEP. A tool opens only its own files, a newer format is refused with a reason, and
the files SFARI and DEEP wrote before the format still open through their own legacy readers."""
from __future__ import annotations

import json

import pytest

from staf_workbook import assessment_file as af
from staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet

DELIN = {"delineation": {"comid": 9327042, "snapped_lat": 43.69, "snapped_lon": -72.28},
         "siteAnchor": {"anchorKind": "v2Direct"}, "ctx_inputs": {"comid": 9327042}}


def _two(live):
    sset = ScenarioSet()
    sset.baseline.state = {"entries": "existing, as stored"}
    sset.add("Restore riparian", "Replant the buffer")        # now shown
    return sset, af.scenarios_block(sset, live)


def test_the_file_has_one_structure_in_every_tool():
    for tool in af.TOOLS:
        raw = json.loads(af.dump(tool, DELIN, {"x": 1}, ScenarioSet().to_json(include_baseline_state=True),
                                 saved_at="2026-10-05T12:00:00Z"))
        assert list(raw) == ["format", "formatVersion", "tool", "savedAt", "delineation", "toolData", "scenarios"]
        assert raw["format"] == "staf-assessment" and raw["formatVersion"] == 1 and raw["tool"] == tool
        assert raw["savedAt"] == "2026-10-05T12:00:00Z"
    with pytest.raises(ValueError):
        af.dump("StreamCurves", DELIN, {}, None)


def test_every_scenario_is_saved_with_its_state_the_shown_one_live():
    sset, block = _two({"entries": "the alternative, as shown"})
    assert [i["id"] for i in block["items"]] == [BASELINE_ID, "s2"] and block["active"] == "s2"
    assert block["items"][0]["state"] == {"entries": "existing, as stored"}
    assert block["items"][1]["state"] == {"entries": "the alternative, as shown"}
    back = af.parse(af.dump("SFARI", DELIN, {}, block), "SFARI")
    opened = af.scenario_set(back["scenarios"])
    assert [s.name for s in opened.items] == ["Existing Conditions", "Restore riparian"]
    assert opened.active == "s2" and opened.current.state == {"entries": "the alternative, as shown"}
    assert opened.baseline.state == {"entries": "existing, as stored"}
    assert opened.items[1].description == "Replant the buffer"


def test_a_file_round_trips_its_site_and_tool_data():
    text = af.dump("DEEP", DELIN, {"assessment": {"assessmentId": "a"}}, af.scenarios_block(ScenarioSet(), {"v": 1}))
    got = af.parse(text, "DEEP")
    assert got["delineation"] == DELIN and got["toolData"] == {"assessment": {"assessmentId": "a"}}
    assert af.scenario_set(got["scenarios"]).baseline.state == {"v": 1}


def test_no_scenarios_block_opens_as_existing_conditions_alone():
    sset = af.scenario_set(None)
    assert [s.id for s in sset.items] == [BASELINE_ID] and sset.baseline.state is None


@pytest.mark.parametrize("saved,shown,reason", [
    ("DEEP", "SFARI", "this is a DEEP assessment. Open it in DEEP."),
    ("SFARI", "EASI", "this is a SFARI assessment. Open it in SFARI."),
    ("EASI", "DEEP", "this is an EASI assessment. Open it in EASI.")])
def test_a_tool_opens_only_its_own_files(saved, shown, reason):
    text = af.dump(saved, DELIN, {}, None)
    with pytest.raises(af.AssessmentFileError) as err:
        af.parse(text, shown)
    assert str(err.value) == reason


def test_a_newer_format_is_refused_with_a_reason():
    raw = json.loads(af.dump("EASI", DELIN, {}, None))
    raw["formatVersion"] = af.FORMAT_VERSION + 1
    with pytest.raises(af.AssessmentFileError, match="newer version of STAF"):
        af.parse(json.dumps(raw), "EASI")


@pytest.mark.parametrize("text", ["not json", "[]", '"text"', json.dumps({"format": "other", "tool": "EASI"}),
                                  json.dumps({"format": "staf-assessment", "formatVersion": 1, "tool": "Other"})])
def test_anything_else_is_not_a_staf_assessment_file(text):
    with pytest.raises(af.AssessmentFileError, match="not a STAF assessment file"):
        af.parse(text, "EASI")


@pytest.mark.parametrize("version", [None, 0, "1", True, 1.5])
def test_the_format_version_must_be_a_whole_number(version):
    raw = json.loads(af.dump("SFARI", DELIN, {}, None))
    raw["formatVersion"] = version
    with pytest.raises(af.AssessmentFileError, match="no valid format version"):
        af.parse(json.dumps(raw), "SFARI")


@pytest.mark.parametrize("change,what", [
    (dict(delineation=[]), "delineation"),
    (dict(delineation={"ctx_inputs": []}), "ctx_inputs"),
    (dict(delineation={"siteAnchor": "v2"}), "siteAnchor"),
    (dict(delineation={"delineation": 4}), "delineation"),
    (dict(toolData=[1]), "tool data"),
    (dict(scenarios=[]), "scenarios"),
])
def test_a_malformed_block_is_refused_before_anything_opens(change, what):
    raw = json.loads(af.dump("SFARI", DELIN, {}, None))
    raw.update(change)
    with pytest.raises(af.AssessmentFileError, match=f"the saved {what} must be an object"):
        af.parse(json.dumps(raw), "SFARI")


def test_empty_blocks_read_as_empty():
    raw = json.loads(af.dump("SFARI", DELIN, {}, None))
    raw.update(delineation=None, toolData=None, scenarios=None)
    assert af.parse(json.dumps(raw), "SFARI") == {"delineation": {}, "toolData": {}, "scenarios": None}


def test_a_pre_format_file_needs_its_tools_legacy_reader():
    old = json.dumps({"schemaVersion": 1, "method": "SFARI", "delineation": DELIN, "metric_scores": {"m": 1}})
    with pytest.raises(af.AssessmentFileError, match="not a STAF assessment file"):
        af.parse(old, "SFARI")

    def legacy(raw):
        return {"delineation": raw["delineation"], "toolData": {},
                "scenarios": af.legacy_scenarios(raw.get("scenarios"), {"metric_scores": raw["metric_scores"]})}
    got = af.parse(old, "SFARI", legacy=legacy)
    assert af.scenario_set(got["scenarios"]).baseline.state == {"metric_scores": {"m": 1}}


@pytest.mark.parametrize("raw,tool", [
    ({"method": "DEEP"}, "DEEP"), ({"method": "sfari "}, "SFARI"), ({"measured_values": {}}, "DEEP"),
    ({"assessment": {}}, "DEEP"), ({"metric_scores": {}}, "SFARI"), ({"function_scores": {}}, "SFARI"),
    ({"delineation": {}}, None), ({"method": "other"}, None),
])
def test_a_pre_format_file_names_its_tool_by_its_method_or_its_keys(raw, tool):
    assert af.legacy_tool(raw) == tool


def test_a_pre_format_file_of_another_tool_is_refused_before_its_reader_runs():
    old = json.dumps({"schemaVersion": 2, "method": "DEEP", "measured_values": {}})
    with pytest.raises(af.AssessmentFileError, match="this is a DEEP assessment"):
        af.parse(old, "SFARI", legacy=lambda raw: pytest.fail("the SFARI reader must not run"))


def test_legacy_scenarios_put_existing_conditions_state_in_the_block():
    sset = ScenarioSet()
    sset.add("Alt", "")
    old_block = sset.to_json()                                  # alternatives only, as files used to be
    assert "state" not in old_block["items"][0]
    block = af.legacy_scenarios(old_block, {"v": "ec"})
    assert block["items"][0]["state"] == {"v": "ec"} and block["active"] == "s2"
    assert af.legacy_scenarios(None, {"v": "ec"})["items"] == [
        {"id": BASELINE_ID, "name": "Existing Conditions", "description": "", "state": {"v": "ec"}}]


def test_each_key_takes_one_line_and_the_geometry_stays_compact():
    ws = {"type": "Polygon", "coordinates": [[[1.5, 2.5], [3.5, 4.5], [1.5, 2.5]]]}
    text = af.dump("EASI", {"watershed_geojson": ws}, {}, None, saved_at="2026-10-05T12:00:00Z")
    lines = text.strip().split("\n")
    assert lines[0] == "{" and lines[-1] == "}" and len(lines) == 9
    assert lines[1] == '  "format": "staf-assessment",' and lines[3] == '  "tool": "EASI",'
    assert '  "delineation": {"watershed_geojson":{"type":"Polygon","coordinates":[[[1.5,2.5],[3.5,4.5],[1.5,2.5]]]}},' in lines
    assert json.loads(text)["delineation"]["watershed_geojson"] == ws


def test_numbers_from_numpy_and_sets_are_written_as_plain_json():
    class Number:
        def item(self):
            return 2.5
    raw = json.loads(af.dump("EASI", {"area": Number(), "owned": {"b", "a"}}, {}, None))
    assert raw["delineation"] == {"area": 2.5, "owned": ["a", "b"]}
