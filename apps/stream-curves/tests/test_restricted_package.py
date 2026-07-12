"""Restricted package: deterministic ZIP + full-detail contents + summary."""
from __future__ import annotations

import io
import zipfile

import pandas as pd

from streamcurves import packaging
from streamcurves import session_io as sio


def _session() -> dict:
    fields = {
        "data": pd.DataFrame({
            "..streamcurves_site_id": ["REF-1", "REF-2"],
            "lat": [44.1, 44.2], "lon": [-71.1, -71.2],
            "perRiffle": [3.1, 4.2],
        }),
        "metric_config": {"perRiffle": {"display_name": "Riffles"}},
        "easi_screening_sites": pd.DataFrame([
            {"site_id": "REF-1", "final_decision": "retained"},
            {"site_id": "REF-2", "final_decision": "excluded"},
        ]),
        "validation_records": [{"actor": "lab", "note": "checked"}],
    }
    return sio.dump_session_fields(fields, session_name="nh-pilot")


def _bundle() -> dict:
    return {
        "schemaVersion": 1, "assessmentId": "nh", "assessmentName": "NH",
        "metricsByFunction": [{"functionId": "f1", "functionName": "F1",
                               "metrics": [{"metricId": "m1"}]}],
        "region": {"kind": "ecoregion", "code": "58", "name": "Northeastern Highlands"},
        "scoringContract": {"methodVersion": "iqr-seed-v1"},
    }


def test_package_is_deterministic():
    session, bundle = _session(), _bundle()  # same inputs -> byte-identical archive
    zip1, sha1, _ = packaging.build_restricted_package(session, bundle)
    zip2, sha2, _ = packaging.build_restricted_package(session, bundle)
    assert zip1 == zip2
    assert sha1 == sha2
    assert len(sha1) == 64


def test_package_contains_full_detail():
    zip_bytes, _, _ = packaging.build_restricted_package(_session(), _bundle())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert "session.streamcurves.json" in names
        assert "assessment.deep.json" in names
        assert "screening/sites.csv" in names
        assert "validation.json" in names
        assert "reference/reference_data.csv" in names
        assert "MANIFEST.json" in names
        # full detail: coordinates ARE present in the restricted package
        ref = zf.read("reference/reference_data.csv").decode()
        assert "lat" in ref and "lon" in ref


def test_public_summary_counts():
    _, _, summary = packaging.build_restricted_package(_session(), _bundle())
    assert summary["n_reference_sites"] == 2
    assert summary["n_screened"] == 2
    assert summary["n_retained"] == 1
    assert summary["n_functions"] == 1
    assert summary["scoring_method_version"] == "iqr-seed-v1"


def test_workbook_included_when_provided():
    zip_bytes, _, _ = packaging.build_restricted_package(
        _session(), _bundle(), workbook_bytes=b"xlsx-bytes")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert zf.read("workbook.xlsx") == b"xlsx-bytes"
