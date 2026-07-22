"""Normalization and spatial-selection tests for revised EASI data sources."""
from __future__ import annotations

import pytest

from easi.datasources import attains, nid_barriers, wqp
from easi.metrics import physicochemistry
from easi.metrics.base import AnalysisContext


class _Response:
    def __init__(self, *, text="", payload=None, status=200):
        self.text = text
        self._payload = payload or {}
        self.status_code = status

    def json(self):
        return self._payload


def test_wqp_total_fraction_unit_qc_station_aggregation(monkeypatch):
    csv_text = (
        "MonitoringLocationIdentifier,MonitoringLocationName,LatitudeMeasure,"
        "LongitudeMeasure,ActivityStartDate,ResultSampleFractionText,"
        "ResultStatusIdentifier,ResultDetectionConditionText,ResultMeasureValue,"
        "ResultMeasure/MeasureUnitCode\n"
        'A,"River, upper",40.001,-83.0,2025-01-01,Total,Final,,1.0,mg/L\n'
        'A,"River, upper",40.001,-83.0,2025-02-01,Total,Final,,9000,ug/L\n'
        'B,River lower,40.01,-83.0,2024-01-01,Total,Final,,2.0,mg/L\n'
        'B,River lower,40.01,-83.0,2024-02-01,Dissolved,Final,,99,mg/L\n'
        'B,River lower,40.01,-83.0,2024-03-01,Total,Rejected,,99,mg/L\n'
        'B,River lower,40.01,-83.0,2024-04-01,Total,Final,Not Detected,0.1,mg/L\n'
        'B,River lower,40.01,-83.0,2024-05-01,Total,Final,,bad,mg/L\n'
        'B,River lower,40.01,-83.0,2024-06-01,Total,Final,,3,ppm\n'
    )
    monkeypatch.setattr(
        wqp.requests, "get", lambda *args, **kwargs: _Response(text=csv_text))
    summary = wqp.sample_summary("tn", 40.0, -83.0)
    # Station A median=(1+9)/2=5; station B median=2; balanced median=3.5.
    assert summary["value"] == 3.5
    assert summary["observation_count"] == 3
    assert summary["station_count"] == 2
    assert summary["date_start"] == "2024-01-01"
    assert summary["date_end"] == "2025-02-01"
    assert summary["excluded_count"] == 5
    assert summary["excluded"]["non_total_fraction"] == 1
    assert summary["excluded"]["rejected"] == 1
    assert summary["excluded"]["censored"] == 1
    assert summary["excluded"]["nonnumeric"] == 1
    assert summary["excluded"]["unsupported_unit"] == 1


def test_wqp_successful_query_with_no_valid_rows_is_not_failure(monkeypatch):
    csv_text = (
        "MonitoringLocationIdentifier,ResultSampleFractionText,"
        "ResultMeasureValue,ResultMeasure/MeasureUnitCode\n"
        "A,Dissolved,1.0,mg/L\n"
    )
    monkeypatch.setattr(
        wqp.requests, "get", lambda *args, **kwargs: _Response(text=csv_text))
    summary = wqp.sample_summary("tp", 40.0, -83.0)
    assert summary is not None and summary["query_ok"] is True
    assert summary["value"] is None


def test_nutrient_adapter_partial_rating_and_confidence(monkeypatch):
    summaries = {
        "tn": {
            "value": 0.2, "observation_count": 3, "station_count": 2,
            "nearest_distance_mi": 0.4, "excluded_count": 1,
            "date_start": "2023-01-01", "date_end": "2025-01-01",
        },
        "tp": {
            "value": None, "observation_count": 0, "station_count": 0,
            "nearest_distance_mi": None, "excluded_count": 2,
            "date_start": None, "date_end": None,
        },
    }
    monkeypatch.setattr(
        physicochemistry.wqp, "sample_summary",
        lambda param, lat, lon: summaries[param])
    monkeypatch.setattr(
        physicochemistry.geo, "nars9_at",
        lambda lat, lon: {"code": "XER", "name": "Xeric"})
    result = physicochemistry.nutrients(AnalysisContext(lat=40, lon=-83))
    assert result.rating == "Good"
    assert result.confidence == "M"
    assert result.scoring["completeness"] == "partial"
    assert "3 valid observation" in result.note


def test_nutrient_combined_confidence_requires_each_scored_analyte_to_qualify(
        monkeypatch):
    summaries = {
        "tn": {
            "value": 0.2, "observation_count": 4, "station_count": 1,
            "nearest_distance_mi": 0.4, "excluded_count": 0,
            "date_start": "2025-01-01", "date_end": "2025-04-01",
        },
        "tp": {
            "value": 0.02, "observation_count": 1, "station_count": 1,
            "nearest_distance_mi": 3.0, "excluded_count": 0,
            "date_start": "2025-02-01", "date_end": "2025-02-01",
        },
    }
    monkeypatch.setattr(
        physicochemistry.wqp, "sample_summary",
        lambda parameter, lat, lon: summaries[parameter])
    monkeypatch.setattr(
        physicochemistry.geo, "nars9_at",
        lambda lat, lon: {"code": "SAP", "name": "Southern Appalachians"})
    result = physicochemistry.nutrients(AnalysisContext(lat=40, lon=-83))
    assert result.rating in {"Good", "Fair", "Poor"}
    assert result.confidence == "L"


def test_attains_nearest_selection_uses_distance_not_impairment(monkeypatch):
    features = [
        {
            "attributes": {
                "assessmentunitidentifier": "FAR-IMPAIRED",
                "ircategory": "5", "isimpaired": "Y",
            },
            "geometry": {"paths": [[[-83.02, 39.99], [-83.02, 40.01]]]},
        },
        {
            "attributes": {
                "assessmentunitidentifier": "NEAR-GOOD",
                "ircategory": "2", "isimpaired": "N",
            },
            "geometry": {"paths": [[[-83.001, 39.99], [-83.001, 40.01]]]},
        },
    ]
    requested = []

    def fake_request(layer, lat, lon, buffer_m, timeout):
        requested.append(layer)
        return features if layer == 1 else []

    monkeypatch.setattr(attains, "_request", fake_request)
    result = attains.impairment_near_point(40.0, -83.0)
    assert result["assessment_unit"] == "NEAR-GOOD"
    assert result["distance_m"] < 200
    assert result["source_layer"] == 1
    assert requested == [0, 1, 2]


def test_attains_intersection_uses_assessed_geometries_not_catchment_layer(monkeypatch):
    requested = []

    def fake_request(layer, lat, lon, buffer_m, timeout):
        requested.append(layer)
        if layer == 2:
            return [{
                "attributes": {
                    "assessmentunitidentifier": "AREA-1",
                    "ircategory": "2",
                },
                "geometry": {
                    "rings": [[[-83.01, 39.99], [-82.99, 39.99],
                               [-82.99, 40.01], [-83.01, 40.01],
                               [-83.01, 39.99]]],
                },
            }]
        return []

    monkeypatch.setattr(attains, "_request", fake_request)
    result = attains.impairment_at_point(40.0, -83.0)
    assert result["assessment_unit"] == "AREA-1"
    assert result["source_layer"] == 2
    assert requested == [0, 1, 2]


def test_attains_nearest_compares_points_lines_and_areas(monkeypatch):
    features = {
        0: [{
            "attributes": {"assessmentunitidentifier": "POINT"},
            "geometry": {"points": [[-83.0005, 40.0]]},
        }],
        1: [{
            "attributes": {"assessmentunitidentifier": "LINE"},
            "geometry": {"paths": [[[-83.001, 39.99], [-83.001, 40.01]]]},
        }],
        2: [{
            "attributes": {"assessmentunitidentifier": "AREA"},
            "geometry": {
                "rings": [[[-83.01, 40.002], [-82.99, 40.002],
                           [-82.99, 40.004], [-83.01, 40.004],
                           [-83.01, 40.002]]],
            },
        }],
    }
    monkeypatch.setattr(
        attains, "_request",
        lambda layer, lat, lon, buffer_m, timeout: features[layer])
    result = attains.impairment_near_point(40.0, -83.0)
    assert result["assessment_unit"] == "POINT"
    assert result["source_layer"] == 0


def test_attains_category_mapping():
    ctx = AnalysisContext(lat=40, lon=-83)
    record = {"assessment_unit": "A", "ircategory": "4A", "match_type": "intersect",
              "distance_m": 0}
    result = physicochemistry._attains_result(record, "H")
    assert result.rating == "Fair"
    record["ircategory"] = "3"
    result = physicochemistry._attains_result(record, "H")
    assert result.rating is None and result.status == "not_assessed"


def test_nid_filters_to_true_geodesic_radius(monkeypatch):
    payload = {
        "features": [
            {"attributes": {"NAME": "inside"}, "geometry": {"x": -83.01, "y": 40.0}},
            {"attributes": {"NAME": "outside"}, "geometry": {"x": -83.03, "y": 40.0}},
        ]
    }
    monkeypatch.setattr(
        nid_barriers.requests, "get",
        lambda *args, **kwargs: _Response(payload=payload))
    dams = nid_barriers.barriers_near(40.0, -83.0, miles=1)
    assert [dam["name"] for dam in dams] == ["inside"]
    assert dams[0]["distance_m"] < 1609.344
