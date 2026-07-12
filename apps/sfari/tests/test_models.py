"""EvidenceResult contract tests — the new ``field_value_text`` field.

``field_value_text`` (A1) is an additive, backward-compatible field: a concise,
self-identifying print value ("Impervious 12.3%") kept alongside the full
``value_text``. It must survive a ``to_dict`` -> ``from_dict`` round trip, and
``from_dict`` must still load legacy evidence dicts that predate the field
(defaulting it to ""). Pure/offline.
"""
from sfari.models import EvidenceResult


def test_field_value_text_roundtrips():
    r = EvidenceResult(
        metric_id="catchment-hydrology-impervious-surface-area",
        value=12.3,
        value_text="12.3% impervious (watershed)",
        field_value_text="Impervious 12.3%",
        confidence="H",
        source="EPA StreamCat pctimp2019ws",
        status="ok",
    )
    d = r.to_dict()
    assert d["field_value_text"] == "Impervious 12.3%"
    assert d["value_text"] == "12.3% impervious (watershed)"  # full form kept alongside
    back = EvidenceResult.from_dict(d)
    assert back == r
    assert back.field_value_text == "Impervious 12.3%"


def test_from_dict_tolerates_legacy_without_field_value_text():
    # A dict serialized before field_value_text existed: the key is simply absent.
    legacy = {
        "metric_id": "surface-water-storage-wetland-coverage",
        "value": 4.2,
        "value_text": "4.2% wetland (watershed; woody+herbaceous)",
        "suggested_likert": "Agree",
        "confidence": "M",
        "source": "EPA StreamCat pctwdwet+pcthbwet",
        "source_url": "",
        "status": "ok",
        "note": "",
    }
    assert "field_value_text" not in legacy
    back = EvidenceResult.from_dict(legacy)
    assert back.field_value_text == ""          # defaulted, not KeyError
    assert back.value_text == "4.2% wetland (watershed; woody+herbaceous)"
    assert back.metric_id == "surface-water-storage-wetland-coverage"
