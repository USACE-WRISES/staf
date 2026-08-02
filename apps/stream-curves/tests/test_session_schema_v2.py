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


def test_candidate_sites_registered():
    assert "candidate_sites" in sio.SESSION_FIELDS


def test_candidate_sites_survive_save_and_reopen():
    # The regression: re-entry used to rebuild candidates from the screening
    # table, which has no coordinates and reuses `state` for the EASI run state,
    # so a reopened project lost its sites and mislabelled the rest.
    from views.import_map import _restore_candidate_sites

    candidates = pd.DataFrame([
        {"site_id": "A", "lat": 40.32, "lon": -84.63, "state": "Ohio",
         "ag_eco9": "TPL", "huc8": "H05120101", ".source": "nrsa"},
        {"site_id": "B", "lat": 39.91, "lon": -84.30, "state": "Ohio",
         "ag_eco9": "TPL", "huc8": "H05080001", ".source": "nrsa"},
    ])
    screening = pd.DataFrame([
        {"site_id": "A", "state": "succeeded", "final_decision": "retained"},
        {"site_id": "B", "state": "failed", "final_decision": "pending"},
    ])

    payload = sio.dump_session_fields({"candidate_sites": candidates,
                                       "easi_screening_sites": screening})
    decoded = sio.decode_session_fields(sio.load_session_payload(
        sio.dumps_session(payload)))

    restored = _restore_candidate_sites(decoded["candidate_sites"],
                                        decoded["easi_screening_sites"])
    assert list(restored["site_id"]) == ["A", "B"]
    assert list(restored["lat"]) == [40.32, 39.91]      # coordinates survive
    assert list(restored["lon"]) == [-84.63, -84.30]
    assert list(restored["state"]) == ["Ohio", "Ohio"]  # US state, not run state
    assert list(restored["huc8"]) == ["H05120101", "H05080001"]


def test_v1_session_migrates_to_v2():
    v1 = {"app": "streamcurves", "schema_version": 1, "app_version": "0.0.1",
          "created": "2026-01-01T00:00:00+00:00", "session_name": "old",
          "fields": {"data": None, "config_version": 3}}
    migrated = sio.load_session_payload(json.dumps(v1))
    assert migrated["schema_version"] == 2
    # existing fields preserved; screening state introduced empty
    assert migrated["fields"]["config_version"] == 3
    for f in ("candidate_sites", "easi_screening_sites",
              "easi_screening_metrics", "easi_screening_criteria"):
        assert f in migrated["fields"] and migrated["fields"][f] is None


def test_function_coverage_exceptions_survive_save_and_reopen():
    """A revision must not have to re-argue every documented gap."""
    from streamcurves import session_io as sio

    exceptions = [{
        "functionId": "reach-inflow",
        "reason": "no-suitable-metric",
        "justification": "No condition indicator for reach inflow exists at this scale.",
        "recordedBy": "gtmenichino",
    }]
    payload = sio.dump_session_fields(
        {"session_name": "cov", "function_coverage_exceptions": exceptions},
        session_name="cov",
    )
    assert "function_coverage_exceptions" in sio.SESSION_FIELDS
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert back["function_coverage_exceptions"] == exceptions


def test_a_session_written_before_the_coverage_gate_reads_as_no_exceptions():
    from streamcurves import session_io as sio

    payload = sio.dump_session_fields({"session_name": "old"}, session_name="old")
    payload["fields"].pop("function_coverage_exceptions", None)
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert not back.get("function_coverage_exceptions")


def test_metric_redundancy_survives_save_and_reopen():
    """RED-01 evidence used to be computed on every run and then dropped from the
    session, so only the run folder CSV kept it."""
    import pandas as pd

    from streamcurves import session_io as sio

    matrix = pd.DataFrame([{
        "metric_a": "chem_PTL", "metric_b": "chem_NTL_DISS",
        "function_a": "Nutrient cycling", "function_b": "Nutrient cycling",
        "same_function": True, "spearman": 0.87, "pearson": 0.81,
        "red01_spearman_flag": True, "code_pearson_flag": True, "divergence": 0.06,
    }])
    payload = sio.dump_session_fields(
        {"session_name": "red", "metric_redundancy": matrix}, session_name="red")
    assert "metric_redundancy" in sio.SESSION_FIELDS
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    pd.testing.assert_frame_equal(back["metric_redundancy"], matrix)


def test_a_session_written_before_redundancy_reads_as_not_computed():
    """Purely additive: decode returns None for a field the payload lacks, so the
    ten already-published assessments need no migration."""
    from streamcurves import session_io as sio

    payload = sio.dump_session_fields({"session_name": "old"}, session_name="old")
    payload["fields"].pop("metric_redundancy", None)
    back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
    assert back.get("metric_redundancy") is None
