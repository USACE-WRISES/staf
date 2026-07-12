"""Session schema v2: EASI-screening persistence + v1->v2 migration."""
from __future__ import annotations

import json

import pandas as pd

from streamcurves import session_io as sio


def test_schema_version_is_2():
    assert sio.SCHEMA_VERSION == 2


def test_screening_fields_registered():
    for f in ("easi_screening_sites", "easi_screening_metrics",
              "easi_screening_criteria"):
        assert f in sio.SESSION_FIELDS


def test_screening_tables_roundtrip():
    fields = {
        "easi_screening_sites": pd.DataFrame([
            {"site_id": "REF-1", "auto_decision": "qualified",
             "final_decision": "retained", "eci": 0.82},
            {"site_id": "REF-2", "auto_decision": "excluded",
             "final_decision": "excluded", "eci": 0.30}]),
        "easi_screening_metrics": pd.DataFrame([
            {"site_id": "REF-1", "metric_id": "m1", "rating": "Good"}]),
        "easi_screening_criteria": {"criteria": "functional"},
    }
    payload = sio.dump_session_fields(fields)
    assert payload["schema_version"] == 2
    loaded = sio.load_session_payload(sio.dumps_session(payload))
    decoded = sio.decode_session_fields(loaded)
    assert decoded["easi_screening_criteria"] == {"criteria": "functional"}
    sites = decoded["easi_screening_sites"]
    assert list(sites["site_id"]) == ["REF-1", "REF-2"]
    assert list(sites["final_decision"]) == ["retained", "excluded"]


def test_v1_session_migrates_to_v2():
    v1 = {"app": "streamcurves", "schema_version": 1, "app_version": "0.0.1",
          "created": "2026-01-01T00:00:00+00:00", "session_name": "old",
          "fields": {"data": None, "config_version": 3}}
    migrated = sio.load_session_payload(json.dumps(v1))
    assert migrated["schema_version"] == 2
    # existing fields preserved; screening state introduced empty
    assert migrated["fields"]["config_version"] == 3
    for f in ("easi_screening_sites", "easi_screening_metrics",
              "easi_screening_criteria"):
        assert f in migrated["fields"] and migrated["fields"][f] is None
