"""Tests for streamcurves/session_io.py — JSON session round-trips."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from streamcurves import session_io as sio


def _sample_fields() -> dict:
    data = pd.DataFrame(
        {
            "site": ["a", "b", "c"],
            "value": [1.5, np.nan, 3.0],
            "count": pd.array([1, 2, None], dtype="Int64"),
            "strat": pd.Categorical(["x", "y", "x"], categories=["x", "y", "z"], ordered=True),
        }
    )
    curve = pd.DataFrame(
        {"point_order": [1, 2, 3], "metric_value": [0.0, 5.0, 10.0], "index_score": [0.0, 0.5, 1.0]}
    )
    return {
        "data": data,
        "metric_config": {"m1": {"column_name": "value", "higher_is_better": True}},
        "strat_config": {"s1": {"column_name": "strat", "levels": ["x", "y", "z"]}},
        "config_version": 2,
        "current_metric": "m1",
        "reference_curve": curve,
        "metric_phase_cache": {
            "m1": {
                "phase1_screening": pd.DataFrame({"metric": ["m1"], "p_value": [0.03]}),
                "config_version": 2,
                "stratum_results": {"x": {"reference_curve": curve}},
                "current_stratum_level": "x",
                "note_score": math.nan,
            }
        },
        "decision_log": pd.DataFrame({"metric": ["m1"], "phase": [4], "note": [None]}),
        "app_data_loaded": True,
        "custom_groupings": {},
        "session_name": "unit-test",
    }


def _roundtrip(fields: dict) -> dict:
    payload = sio.dump_session_fields(fields, session_name=fields.get("session_name"))
    text = sio.dumps_session(payload)
    # strict JSON: no NaN/Infinity literals
    json.loads(text)
    loaded = sio.load_session_payload(text)
    return sio.decode_session_fields(loaded)


def test_roundtrip_frames_dtypes_and_categoricals():
    fields = _sample_fields()
    out = _roundtrip(fields)

    df = out["data"]
    pd.testing.assert_frame_equal(df, fields["data"])
    assert isinstance(df["strat"].dtype, pd.CategoricalDtype)
    assert df["strat"].cat.ordered
    assert list(df["strat"].cat.categories) == ["x", "y", "z"]
    assert str(df["count"].dtype) == "Int64"
    assert math.isnan(df["value"].iloc[1])


def test_roundtrip_nested_cache_and_nan_scalars():
    out = _roundtrip(_sample_fields())
    cache = out["metric_phase_cache"]["m1"]
    pd.testing.assert_frame_equal(
        cache["stratum_results"]["x"]["reference_curve"],
        _sample_fields()["reference_curve"],
    )
    assert math.isnan(cache["note_score"])
    assert cache["config_version"] == 2
    assert out["config_version"] == 2
    assert out["app_data_loaded"] is True
    # unset session fields decode as None
    assert out["phase2_ranking"] is None


def test_dump_is_idempotent():
    fields = _sample_fields()
    once = _roundtrip(fields)
    twice = _roundtrip(once)
    pd.testing.assert_frame_equal(once["data"], twice["data"])
    assert once["metric_config"] == twice["metric_config"]


def test_lenient_field_drops_unserializable_with_warning(caplog):
    fields = _sample_fields()
    fields["completed_metrics"] = {
        "m1": {"curve_row": pd.DataFrame({"metric": ["m1"]}), "curve_plot": object()}
    }
    with caplog.at_level("WARNING", logger="streamcurves"):
        payload = sio.dump_session_fields(fields)
    out = sio.decode_session_fields(sio.load_session_payload(sio.dumps_session(payload)))
    assert "curve_plot" not in out["completed_metrics"]["m1"]
    assert "curve_row" in out["completed_metrics"]["m1"]
    assert any("dropping non-serializable" in r.message for r in caplog.records)


def test_strict_field_raises_on_unserializable():
    fields = _sample_fields()
    fields["input_metadata"] = {"handle": object()}
    with pytest.raises(TypeError):
        sio.dump_session_fields(fields)


def test_reserved_type_key_rejected():
    fields = _sample_fields()
    fields["input_metadata"] = {"__type__": "sneaky"}
    with pytest.raises(ValueError):
        sio.dump_session_fields(fields)


def test_invalid_payloads_raise_user_facing_errors(tmp_path):
    with pytest.raises(ValueError, match="not a valid StreamCurves session"):
        sio.load_session_payload("{\"app\": \"other\"}\n")
    with pytest.raises(ValueError, match="not a valid StreamCurves session"):
        sio.load_session_payload("not json at all\n")
    newer = json.dumps({"app": "streamcurves", "schema_version": 99, "fields": {}})
    with pytest.raises(ValueError, match="newer StreamCurves"):
        sio.load_session_payload(newer)


def test_write_and_read_file(tmp_path):
    payload = sio.dump_session_fields(_sample_fields(), session_name="disk")
    path = tmp_path / ("disk" + sio.SESSION_SUFFIX)
    sio.write_session(payload, path)
    loaded = sio.load_session_payload(path)
    assert loaded["session_name"] == "disk"
    out = sio.decode_session_fields(loaded)
    assert out["current_metric"] == "m1"


def test_inf_roundtrip():
    fields = _sample_fields()
    fields["input_metadata"] = {"upper": math.inf, "lower": -math.inf}
    out = _roundtrip(fields)
    assert out["input_metadata"]["upper"] == math.inf
    assert out["input_metadata"]["lower"] == -math.inf


def test_region_of_applicability_roundtrips():
    fields = _sample_fields()
    fields["region_of_applicability"] = {
        "kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains",
    }
    out = _roundtrip(fields)
    assert out["region_of_applicability"] == {
        "kind": "ecoregion", "code": "55", "name": "Eastern Corn Belt Plains",
    }


def test_region_of_applicability_polygon_roundtrips():
    fields = _sample_fields()
    rings = [[[-84.0, 40.0], [-83.0, 40.0], [-83.0, 41.0], [-84.0, 40.0]]]
    fields["region_of_applicability"] = {
        "kind": "polygon", "code": "USER", "name": "Custom area", "polygon": rings,
    }
    out = _roundtrip(fields)
    assert out["region_of_applicability"]["kind"] == "polygon"
    assert out["region_of_applicability"]["polygon"] == rings
