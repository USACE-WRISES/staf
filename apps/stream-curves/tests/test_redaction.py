"""Public-package redaction: identity is stripped, analytic signal survives."""
from __future__ import annotations

import pandas as pd

from streamcurves import redaction
from streamcurves import session_io as sio


def _sample_payload() -> dict:
    data = pd.DataFrame({
        "..streamcurves_site_id": ["REF-1", "REF-2", "REF-3"],
        "..streamcurves_site_label": ["Trout Brook", "Cold Creek", "Mill River"],
        "lat": [44.1, 44.2, 44.3],
        "lon": [-71.1, -71.2, -71.3],
        "comid": [1234, 5678, 9012],
        "perRiffle": [3.1, 4.2, 2.8],       # a metric column (kept)
        "bankHeight": [1.2, 1.5, 1.1],       # a predictor column (kept)
        "ecoregion": ["58", "58", "58"],     # a stratification column (kept)
    })
    fields = {
        "data": data,
        "metric_config": {"perRiffle": {"display_name": "Riffles"}},
        "predictor_config": {"bankHeight": {}},
        "strat_config": {"ecoregion": {"display_name": "Ecoregion"}},
        "upload_filename": "secret_sites.csv",
        "data_fingerprint": "abc123",
        "input_metadata": {"source": "field crew A"},
        "site_mask_config": {"masked_site_ids": [2], "site_label_column": "..streamcurves_site_id"},
        "easi_screening_sites": pd.DataFrame([
            {"site_id": "REF-1", "final_decision": "retained", "eci": 0.82, "lat": 44.1},
            {"site_id": "REF-2", "final_decision": "excluded", "eci": 0.30, "lat": 44.2},
        ]),
        "easi_screening_metrics": pd.DataFrame([{"site_id": "REF-1", "metric_id": "m1"}]),
        "easi_screening_criteria": {"criteria": "functional"},
        "site_exclusions": [{"site_id": "REF-2", "reason": "eci fail"}],
        "validation_records": [{"actor": "lab", "note": "checked"}],
    }
    return sio.dump_session_fields(fields, session_name="nh-pilot")


def test_original_has_violations():
    assert redaction.redaction_violations(_sample_payload())


def test_redacted_is_clean():
    redacted, report = redaction.redact_session_payload(_sample_payload())
    assert redaction.redaction_violations(redacted) == []
    assert report["site_count"] == 3


def test_redaction_keeps_analytic_columns_only():
    redacted, _ = redaction.redact_session_payload(_sample_payload())
    fields = sio.decode_session_fields(redacted)
    data = fields["data"]
    cols = set(data.columns)
    # analytic columns survive
    assert {"perRiffle", "bankHeight", "ecoregion"}.issubset(cols)
    # identity + coordinates are gone
    assert not ({"lat", "lon", "comid", "..streamcurves_site_label"} & cols)
    # opaque sequential ids
    assert list(data["..streamcurves_site_id"]) == ["S0001", "S0002", "S0003"]


def test_redaction_nulls_identity_fields():
    redacted, _ = redaction.redact_session_payload(_sample_payload())
    fields = sio.decode_session_fields(redacted)
    for f in ("upload_filename", "data_fingerprint", "input_metadata",
              "site_mask_config", "validation_records", "site_exclusions"):
        assert fields.get(f) in (None, [], {}), f
    assert fields.get("easi_screening_sites") is None
    assert fields.get("easi_screening_metrics") is None


def test_redaction_aggregate_screening_summary():
    redacted, _ = redaction.redact_session_payload(_sample_payload())
    fields = sio.decode_session_fields(redacted)
    summary = fields["easi_screening_criteria"]["public_screening_summary"]
    assert summary["n_screened"] == 2
    assert summary["n_retained"] == 1
    assert summary["criteria"] == "functional"


def test_redacted_session_still_restores():
    # The redacted payload is a valid session envelope that decodes cleanly.
    redacted, _ = redaction.redact_session_payload(_sample_payload())
    reloaded = sio.load_session_payload(sio.dumps_session(redacted))
    fields = sio.decode_session_fields(reloaded)
    assert isinstance(fields["data"], pd.DataFrame)
    assert fields["metric_config"] == {"perRiffle": {"display_name": "Riffles"}}
