"""Session schema v2: round-trip of the new provenance fields, v1 migration, and a
stable content digest (Part D1/D2)."""
from __future__ import annotations

import json

from deep import session


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


def test_v2_round_trips_new_fields():
    bundle = _bundle(version=3, status="certified")
    region = {"level3": {"code": "55", "name": "Eastern Corn Belt Plains"},
              "state": {"code": "OH", "abbr": "OH", "name": "Ohio"}}
    delin = {"delineation": {"comid": 42, "snapped_lat": 40.0, "snapped_lon": -83.5}}
    mv = {"m1": {"value": 5.0, "na": False, "note": ""}}

    text = session.dump(delin, bundle, mv, region=region, completeness="complete",
                        result_state="final")
    raw = json.loads(text)
    assert raw["schemaVersion"] == 2

    st = session.load(text)
    assert st["schemaVersion"] == 2
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
    assert st["measured_values"]["m1"]["value"] == 5.0


def test_dump_positional_only_still_valid_v2():
    # A caller passing only the three positional args produces a valid v2 session; provenance
    # simply reflects the bundle and an unresolved region.
    st = session.load(session.dump({}, _bundle(version=1), {}))
    assert st["schemaVersion"] == 2
    assert st["provenance"]["version"] == 1
    assert st["provenance"]["region"] == {"level3": None, "state": None}


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
    assert st["schemaVersion"] == 2
    prov = st["provenance"]
    assert prov["migratedFrom"] == 1
    assert prov["assessmentId"] == "demo-assess"
    assert prov["version"] == 1
    assert prov["lifecycle"] == "preliminary"                 # default when absent
    assert prov["region"] == {"level3": None, "state": None}  # v1 never resolved a region
    # Embedded bundle + values preserved so the current rules reconstruct scores.
    assert st["assessment"]["assessmentId"] == "demo-assess"
    assert st["measured_values"]["m1"]["value"] == 2.0


def test_versionless_session_migrates_as_v1():
    v0 = json.dumps({"assessment": _bundle(version=1), "measured_values": {}, "delineation": {}})
    st = session.load(v0)
    assert st["schemaVersion"] == 2
    assert st["provenance"]["migratedFrom"] == 1


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


def test_bundle_digest_prefers_publisher_digest():
    b = _bundle(version=1)
    b["contentDigest"] = "sha256:canonical-upstream"
    assert session.bundle_digest(b) == "sha256:canonical-upstream"
    # Absent a publisher digest, falls back to the local content digest.
    assert session.bundle_digest(_bundle(version=1)).startswith("sha256:")
