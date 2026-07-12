"""Resumable-session round-trip tests.

Guards the Save/Open contract in ``sfari.session``: a whole assessment
(delineation + per-metric Likert/notes + per-function scores + pulled evidence
+ cross-section geometry) must survive ``dump`` -> ``load`` unchanged. This is
the regression net for the Save/Open repair (the app-side module/parameter name
collision that broke every save and open, and the load path that used to drop
cross-section geometry). Pure/offline: no network, no Shiny server run.
"""
import json

from sfari import session
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


def _roundtrip(cross_section=CROSS_SECTION):
    text = session.dump(DELINEATION, METRIC_SCORES, FUNCTION_SCORES, EVIDENCE, cross_section)
    return session.dump, session.load(text), text


def test_dump_is_valid_json_with_header():
    text = session.dump(DELINEATION, METRIC_SCORES, FUNCTION_SCORES, EVIDENCE, CROSS_SECTION)
    parsed = json.loads(text)
    assert parsed["schemaVersion"] == session.SCHEMA_VERSION
    assert parsed["method"] == "SFARI"


def test_full_state_roundtrips():
    _dump, st, _text = _roundtrip()
    assert st["delineation"] == DELINEATION
    assert st["metric_scores"] == METRIC_SCORES
    assert st["function_scores"] == FUNCTION_SCORES
    assert st["evidence"] == EVIDENCE
    assert st["cross_section"] == CROSS_SECTION


def test_cross_section_geometry_survives():
    _dump, st, _text = _roundtrip()
    xs = st["cross_section"]
    assert xs is not None
    assert xs["points"] == CROSS_SECTION["points"]
    assert xs["bankfull_stage"] == 3.0
    assert xs["division_name"] == "Eastern Highlands"


def test_evidence_preserves_cross_section_source_entry():
    _dump, st, _text = _roundtrip()
    ev = st["evidence"]["high-flow-dynamics-bankfull-discharge"]
    assert ev["source"] == "Native cross-section hydraulics (Manning)"
    assert ev["value_text"] == "Q 82.06 cfs, V 2.74 ft/s"
    # The auto-pulled entry survives intact alongside it.
    assert st["evidence"]["catchment-impervious-cover"]["value"] == 12.3


def test_no_cross_section_roundtrips_to_none():
    _dump, st, _text = _roundtrip(cross_section=None)
    assert st["cross_section"] is None


def test_load_defaults_missing_keys_empty():
    st = session.load(json.dumps({"schemaVersion": 1, "method": "SFARI"}))
    assert st["delineation"] == {}
    assert st["metric_scores"] == {}
    assert st["function_scores"] == {}
    assert st["evidence"] == {}
    assert st["cross_section"] is None


def test_load_drops_legacy_function_na():
    text = json.dumps({"function_scores": {"catchment-hydrology": {"score": 8, "na": True}}})
    st = session.load(text)
    assert st["function_scores"]["catchment-hydrology"] == {"score": 8}
