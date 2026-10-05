"""The SFARI assessment file (the header's Save and Open).

Guards the contract in ``sfari.session``: a whole assessment (delineation + per-metric
Likert/notes/photos + per-function scores + pulled evidence + cross-section geometry) must survive
``dump`` -> ``load`` unchanged, in the STAF assessment file EASI and DEEP write too (owner,
2026-10-05), and a file SFARI saved before that format still opens. This is also the regression net
for the Save/Open repair (the app-side module/parameter name collision that broke every save and
open, and the load path that used to drop cross-section geometry). Pure/offline: no network, no
Shiny server run.
"""
import json

import pytest

from sfari import session
from sfari._vendor.staf_workbook import assessment_file
from sfari._vendor.staf_workbook.model.scenarios import BASELINE_ID, ScenarioSet
from sfari.models import EvidenceResult

# --- A representative assessment state (JSON-native types so equality is exact) ---

DELINEATION = {
    "comid": "9311402",
    "stream_name": "Wildcat Creek",
    "watershed_geojson": {"type": "FeatureCollection", "features": []},
    "reach_geojson": {"type": "Feature", "geometry": None},
    "delineation": {
        "snapped_lat": 39.12345,
        "snapped_lon": -84.51234,
        "drainage_area_sqkm": 42.7,
        "slope": 0.0031,
    },
}

METRIC_SCORES = {
    "catchment-impervious-cover": {"likert": "Agree", "note": "urbanizing", "photos": []},
    "channel-substrate-embeddedness": {"likert": None, "note": "", "photos": [{"id": "p1", "uri": "data:x"}]},
}

FUNCTION_SCORES = {
    "catchment-hydrology": {"score": 8, "note": "moderate"},
    "surface-water-storage": {"score": None, "note": ""},
}

ENTRIES = {"metric_scores": METRIC_SCORES, "function_scores": FUNCTION_SCORES}

EVIDENCE = {
    "catchment-impervious-cover": EvidenceResult(
        metric_id="catchment-impervious-cover",
        value=12.3,
        value_text="Impervious cover 12.3% (NLCD 2021)",
        suggested_likert="Agree",
        confidence="H",
        source="NLCD 2021 impervious",
        source_url="https://example.org/nlcd",
        status="ok",
        note="watershed mean",
    ).to_dict(),
    # A cross-section-sourced entry: distinguished only by its source string, and the
    # kind of evidence that must not be lost across a save/open round trip.
    "high-flow-dynamics-bankfull-discharge": EvidenceResult(
        metric_id="high-flow-dynamics-bankfull-discharge",
        value=82.06,
        value_text="Q 82.06 cfs, V 2.74 ft/s",
        source="Native cross-section hydraulics (Manning)",
        source_url="",
        status="ok",
        note="At the modeled bankfull stage.",
    ).to_dict(),
}

CROSS_SECTION = {
    "points": [[-14.0, 6.0], [-2.0, 0.0], [2.0, 0.0], [14.0, 6.0]],
    "lb": -2.0,
    "rb": 2.0,
    "bankfull_stage": 3.0,
    "slope": 0.002,
    "da": 42.7,
    "width_m": 6.4,
    "depth_m": 1.1,
    "division_name": "Eastern Highlands",
}


def _scenarios(entries=ENTRIES):
    return assessment_file.scenarios_block(ScenarioSet(), entries)


def _roundtrip(cross_section=CROSS_SECTION):
    text = session.dump(DELINEATION, EVIDENCE, cross_section, _scenarios())
    return session.load(text), text


def _existing(st):
    return assessment_file.scenario_set(st["scenarios"]).baseline.state


def test_dump_writes_the_staf_assessment_file():
    raw = json.loads(session.dump(DELINEATION, EVIDENCE, CROSS_SECTION, _scenarios()))
    assert raw["format"] == "staf-assessment" and raw["formatVersion"] == 1 and raw["tool"] == "SFARI"
    assert list(raw) == ["format", "formatVersion", "tool", "savedAt", "delineation", "toolData", "scenarios"]
    assert raw["toolData"] == {"evidence": EVIDENCE, "cross_section": CROSS_SECTION}
    assert raw["scenarios"]["items"][0]["state"] == ENTRIES


def test_full_state_roundtrips():
    st, _text = _roundtrip()
    assert st["delineation"] == DELINEATION
    assert _existing(st) == ENTRIES
    assert st["evidence"] == EVIDENCE
    assert st["cross_section"] == CROSS_SECTION


def test_cross_section_geometry_survives():
    st, _text = _roundtrip()
    xs = st["cross_section"]
    assert xs is not None
    assert xs["points"] == CROSS_SECTION["points"]
    assert xs["bankfull_stage"] == 3.0
    assert xs["division_name"] == "Eastern Highlands"


def test_evidence_preserves_cross_section_source_entry():
    st, _text = _roundtrip()
    ev = st["evidence"]["high-flow-dynamics-bankfull-discharge"]
    assert ev["source"] == "Native cross-section hydraulics (Manning)"
    assert ev["value_text"] == "Q 82.06 cfs, V 2.74 ft/s"
    # The auto-pulled entry survives intact alongside it.
    assert st["evidence"]["catchment-impervious-cover"]["value"] == 12.3


def test_no_cross_section_roundtrips_to_none():
    st, _text = _roundtrip(cross_section=None)
    assert st["cross_section"] is None


def test_a_file_saved_before_the_shared_format_still_opens():
    old = json.dumps({"schemaVersion": 1, "method": "SFARI", "delineation": DELINEATION,
                      "metric_scores": METRIC_SCORES, "function_scores": FUNCTION_SCORES,
                      "evidence": EVIDENCE, "cross_section": CROSS_SECTION})
    st = session.load(old)
    assert st["delineation"] == DELINEATION and st["evidence"] == EVIDENCE
    assert st["cross_section"] == CROSS_SECTION and _existing(st) == ENTRIES


def test_an_old_file_with_alternatives_keeps_every_scenario():
    sset = ScenarioSet()
    alt = sset.add("Restore riparian", "Replant the buffer")
    alt.state = {"metric_scores": {}, "function_scores": {"catchment-hydrology": {"score": 12}}}
    old = json.dumps({"schemaVersion": 1, "method": "SFARI", "delineation": {},
                      "metric_scores": METRIC_SCORES, "function_scores": FUNCTION_SCORES,
                      "scenarios": sset.to_json()})            # Existing Conditions at the top level
    back = assessment_file.scenario_set(session.load(old)["scenarios"])
    assert [s.name for s in back.items] == ["Existing Conditions", "Restore riparian"]
    assert back.baseline.state == ENTRIES and back.active == alt.id
    assert back.items[1].state["function_scores"]["catchment-hydrology"]["score"] == 12


def test_load_defaults_missing_keys_empty():
    st = session.load(json.dumps({"schemaVersion": 1, "method": "SFARI"}))
    assert st["delineation"] == {}
    assert _existing(st) == {"metric_scores": {}, "function_scores": {}}
    assert st["evidence"] == {}
    assert st["cross_section"] is None


def test_load_drops_legacy_function_na():
    text = json.dumps({"function_scores": {"catchment-hydrology": {"score": 8, "na": True}}})
    st = session.load(text)
    assert _existing(st)["function_scores"]["catchment-hydrology"] == {"score": 8}


def test_a_deep_or_easi_file_is_refused_with_the_tool_to_open_it_in():
    deep_old = json.dumps({"schemaVersion": 2, "method": "DEEP", "measured_values": {}})
    with pytest.raises(assessment_file.AssessmentFileError, match="Open it in DEEP"):
        session.load(deep_old)
    easi = assessment_file.dump("EASI", {}, {}, None)
    with pytest.raises(assessment_file.AssessmentFileError, match="Open it in EASI"):
        session.load(easi)


def test_delineation_engine_keys_roundtrip():
    # Since 2026-09 the delineation block may carry the anchor, the geometry-stripped
    # STAF site engine record, and the watershed basis.
    d = dict(DELINEATION, siteAnchor={"anchorKind": "hrSurrogate"},
             siteEngine={"status": "ok", "engineVersion": "0.2.0",
                         "watershed": {"polygon": None, "areaSqkm": 4.19}},
             watershedBasis="site-engine")
    st = session.load(session.dump(d, {}, None, _scenarios({})))
    assert st["delineation"]["siteEngine"]["engineVersion"] == "0.2.0"
    assert st["delineation"]["watershedBasis"] == "site-engine"
    assert st["delineation"]["siteAnchor"] == {"anchorKind": "hrSurrogate"}


def test_legacy_delineation_has_no_engine_keys():
    st = session.load(session.dump(DELINEATION, {}, None, _scenarios({})))
    assert st["delineation"].get("siteEngine") is None
    assert st["delineation"].get("watershedBasis") is None
    assert [s.id for s in assessment_file.scenario_set(st["scenarios"]).items] == [BASELINE_ID]
