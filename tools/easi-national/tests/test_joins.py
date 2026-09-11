"""The point-service joins reproduce the app's own functions from bulk rows."""
from __future__ import annotations

import csv
import io
import json

import pytest

from builder.stages import joins, wqp as wqp_stage
from easi.datasources import attains as app_attains
from easi.datasources import nid_barriers as app_nid
from easi.datasources import wqp as app_wqp

LAT, LON = 38.05, -78.50          # near Charlottesville


def _wqp_csv(rows):
    fields = ["ResultIdentifier", "MonitoringLocationIdentifier", "MonitoringLocationName",
              "OrganizationIdentifier", "ActivityStartDate", "ResultMeasureValue",
              "ResultMeasure/MeasureUnitCode", "ResultSampleFractionText",
              "ResultStatusIdentifier", "ResultDetectionConditionText",
              "LatitudeMeasure", "LongitudeMeasure"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        writer.writerow({f: r.get(f, "") for f in fields})
    return buf.getvalue()


def _rows():
    # station A 1 mi away (2 good rows + 1 censored), station B 3 mi away (ug/L),
    # station C 20 mi away (must be ignored), station D dissolved fraction
    return [
        {"ResultIdentifier": "1", "MonitoringLocationIdentifier": "A", "ActivityStartDate": "2021-05-01",
         "ResultMeasureValue": "1.2", "ResultMeasure/MeasureUnitCode": "mg/l",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Accepted",
         "LatitudeMeasure": "38.0645", "LongitudeMeasure": "-78.50"},
        {"ResultIdentifier": "2", "MonitoringLocationIdentifier": "A", "ActivityStartDate": "2022-06-01",
         "ResultMeasureValue": "1.8", "ResultMeasure/MeasureUnitCode": "mg/l",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Final",
         "LatitudeMeasure": "38.0645", "LongitudeMeasure": "-78.50"},
        {"ResultIdentifier": "3", "MonitoringLocationIdentifier": "A", "ActivityStartDate": "2022-07-01",
         "ResultMeasureValue": "0.1", "ResultMeasure/MeasureUnitCode": "mg/l",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Final",
         "ResultDetectionConditionText": "Not Detected",
         "LatitudeMeasure": "38.0645", "LongitudeMeasure": "-78.50"},
        {"ResultIdentifier": "4", "MonitoringLocationIdentifier": "B", "ActivityStartDate": "2020-01-15",
         "ResultMeasureValue": "900", "ResultMeasure/MeasureUnitCode": "ug/l",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Accepted",
         "LatitudeMeasure": "38.0935", "LongitudeMeasure": "-78.50"},
        {"ResultIdentifier": "5", "MonitoringLocationIdentifier": "C", "ActivityStartDate": "2020-01-15",
         "ResultMeasureValue": "5.0", "ResultMeasure/MeasureUnitCode": "mg/l",
         "ResultSampleFractionText": "Total", "ResultStatusIdentifier": "Accepted",
         "LatitudeMeasure": "38.34", "LongitudeMeasure": "-78.50"},
        {"ResultIdentifier": "6", "MonitoringLocationIdentifier": "D", "ActivityStartDate": "2020-02-15",
         "ResultMeasureValue": "2.0", "ResultMeasure/MeasureUnitCode": "mg/l",
         "ResultSampleFractionText": "Dissolved", "ResultStatusIdentifier": "Accepted",
         "LatitudeMeasure": "38.06", "LongitudeMeasure": "-78.51"},
    ]


def test_wqp_join_reproduces_sample_summary(monkeypatch):
    text = _wqp_csv(_rows())
    # the app, fed the rows WQP would return within 5 mi (stations A, B, D)
    within = [r for r in _rows() if r["MonitoringLocationIdentifier"] != "C"]
    monkeypatch.setattr(app_wqp, "_fetch_csv", lambda *a, **k: _wqp_csv(within))
    expected = app_wqp.sample_summary("tn", LAT, LON)
    # the builder, from the bulk rows of a bigger bbox
    rows = wqp_stage.normalize_rows("tn", text)
    stations = {}
    for r in rows:
        s = stations.setdefault(r["station"], {"rows": [], "lat": None, "lon": None})
        s["rows"].append(r)
        if s["lat"] is None and r["lat"] is not None:
            s["lat"], s["lon"] = r["lat"], r["lon"]
    keys = [k for k, s in stations.items() if s["lat"] is not None]
    index = {"stations": stations, "keys": keys,
             "points": joins._Points([stations[k]["lat"] for k in keys], [stations[k]["lon"] for k in keys])}
    (x, y), = joins._project([LAT], [LON])
    got = joins.wqp_summary(index, "tn", LAT, LON, x, y)
    for key in ("value", "observation_count", "station_count", "date_start", "date_end",
                "nearest_distance_mi", "excluded_count", "excluded", "station_medians", "units"):
        assert got[key] == expected[key], key
    assert got["value"] == pytest.approx(1.2)          # median of station medians (1.5, 0.9)


def test_attains_lookup_matches_the_app_selection_rules():
    polygon = {"rings": [[[-78.51, 38.04], [-78.49, 38.04], [-78.49, 38.06], [-78.51, 38.06], [-78.51, 38.04]]]}
    line_far = {"paths": [[[-78.52, 38.08], [-78.50, 38.08]]]}      # ~3.3 km north
    line_near = {"paths": [[[-78.52, 38.055], [-78.50, 38.055]]]}   # ~0.55 km north
    rows = []
    for layer, geom, au, cat in ((2, polygon, "VA-AREA-1", "2"), (1, line_far, "VA-LINE-FAR", "5"),
                                 (1, line_near, "VA-LINE-NEAR", "4A")):
        b = joins.common.esri_bounds(geom)
        rows.append({"layer": layer, "assessment_unit": au, "assessment_name": au, "overallstatus": "x",
                     "isimpaired": "Y" if cat != "2" else "N", "ircategory": cat,
                     "geometry": json.dumps(geom), "minx": b[0], "miny": b[1], "maxx": b[2], "maxy": b[3]})
    import numpy as np
    index = {"rows": rows, "minx": np.array([r["minx"] for r in rows]), "miny": np.array([r["miny"] for r in rows]),
             "maxx": np.array([r["maxx"] for r in rows]), "maxy": np.array([r["maxy"] for r in rows]), "shapes": {}}
    exact, nearby = joins.attains_lookup(index, LAT, LON)
    assert exact["assessment_unit"] == "VA-AREA-1" and exact["match_type"] == "intersect"
    assert exact["distance_m"] == 0.0 and exact["source_layer"] == 2
    # nearest within 2 km: the polygon covers the point (distance 0) and wins the tie on id
    assert nearby["assessment_unit"] == "VA-AREA-1" and nearby["match_type"] == "nearby"
    # a point just west of the polygon: the near line (~330 m) beats the polygon
    # edge (~440 m); the far line is beyond 2 km
    exact2, nearby2 = joins.attains_lookup(index, 38.052, -78.515)
    assert exact2 == {}
    assert nearby2["assessment_unit"] == "VA-LINE-NEAR"
    assert nearby2["distance_m"] == pytest.approx(
        app_attains._distance_to_geometry_m(38.052, -78.515, line_near), abs=0.1)


def test_nid_lookup_matches_barriers_near_ordering():
    dams = [{"name": "Far", "storage": 1.0, "height": 2.0, "lat": LAT + 0.02, "lon": LON},     # ~2.2 km
            {"name": "Near", "storage": 3.0, "height": 4.0, "lat": LAT + 0.005, "lon": LON},   # ~0.56 km
            {"name": "Edge", "storage": 5.0, "height": 6.0, "lat": LAT + 0.0144, "lon": LON}]  # ~1.60 km
    index = {"rows": dams, "points": joins._Points([d["lat"] for d in dams], [d["lon"] for d in dams])}
    (x, y), = joins._project([LAT], [LON])
    got = joins.nid_lookup(index, LAT, LON, x, y)
    names = [d["name"] for d in got]
    assert names == ["Near", "Edge"]
    assert got[0]["distance_m"] == pytest.approx(app_nid._distance_m(LAT, LON, LAT + 0.005, LON), abs=0.1)
