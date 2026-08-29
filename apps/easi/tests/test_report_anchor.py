"""Report exports and the siteAnchor: covered-network results stay byte-identical
(v2Direct adds nothing), routed results carry the substitution rows everywhere.
Offline."""
from __future__ import annotations

import json

from easi import report, routing


def _result() -> dict:
    rep = {
        "metricRows": [
            {"metricId": "catchment-hydrology-impervious-surface-cover",
             "name": "Impervious Surface Cover", "discipline": "Hydrology",
             "functionName": "Catchment hydrology", "functionId": "catchment-hydrology",
             "scale": "W", "confidence": "H", "rating": "Poor", "generatedRating": "Poor",
             "index": 0.195, "functionScore": 3, "valueText": "38% impervious",
             "criteria": ">25%", "source": "EPA StreamCat", "status": "ok",
             "overrideable": False},
        ],
        "functionScores": {"catchment-hydrology": 3},
        "subIndices": {"physical": 0.2, "chemical": 0.3, "biological": 0.45},
        "ecosystemConditionIndex": 0.32, "computedCount": 1, "totalCount": 20,
        "overridesApplied": [],
    }
    return {
        "delineation": {"gnis_name": "Rush Run", "comid": 5215053,
                        "huc12": "050600010025", "drainage_area_sqkm": 14.9,
                        "watershed_area_sqkm": 14.8, "reach_length_ft": 1000.0,
                        "snapped_lat": 40.0953, "snapped_lon": -83.0199,
                        "warnings": []},
        "report": rep,
        "watershed_geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates":
             [[[-83, 40], [-83.01, 40], [-83.01, 40.01], [-83, 40]]]},
             "properties": {}}]},
        "reach_geojson": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[-83, 40], [-83.001, 40.001]]}, "properties": {}}]},
    }


def _hr_anchor() -> dict:
    return {
        "anchorSchemaVersion": 1, "anchorKind": "hrSurrogate",
        "clickedPoint": {"lat": 40.0962, "lon": -83.0203},
        "clickedStream": {"network": "nhdplus-hr", "nhdplusId": 24000800021917,
                          "gnisName": None, "reachcode": "05060001001737",
                          "drainageAreaSqkm": 2.72, "slope": 0.0177,
                          "fcode": 46003, "streamOrder": 1, "vpuid": "0506",
                          "snapLat": 40.0958, "snapLon": -83.0201,
                          "snapDistFt": 42.0},
        "scoredReach": {"network": "nhdplus-v2", "comid": 5215053,
                        "gnisName": "Rush Run", "drainageAreaSqkm": 14.9,
                        "snapLat": 40.0953, "snapLon": -83.0199,
                        "snapDistFt": None},
        "routing": {"method": "nldi-hydrolocation-raindrop",
                    "routedDistanceFt": 291.4, "daRatio": 5.48,
                    "daRatioLimit": routing.DA_RATIO_MAX, "declined": False},
        "notes": ["Scored at the nearest downstream reach of the covered network."],
    }


def test_covered_exports_are_byte_identical_with_v2direct_anchor():
    plain = _result()
    anchored = _result()
    anchored["siteAnchor"] = routing.v2_anchor(5215053, 40.0953, -83.0199,
                                               40.0953, -83.0199, 8.0)
    assert report.build_csv(plain) == report.build_csv(anchored)
    assert report.build_geojson(plain) == report.build_geojson(anchored)


def test_surrogate_rows_in_csv():
    res = _result()
    res["siteAnchor"] = _hr_anchor()
    b = report.build_csv(res)
    assert b"Scored at surrogate reach" in b
    assert b"(unnamed stream)" in b                # clicked stream has no name
    assert b"Routed distance (ft)" in b
    assert b"Drainage area ratio" in b
    assert b"nldi-hydrolocation-raindrop" in b


def test_surrogate_block_in_geojson():
    res = _result()
    res["siteAnchor"] = _hr_anchor()
    gj = json.loads(report.build_geojson(res))
    pt = next(f for f in gj["features"] if f["properties"]["type"] == "point")
    sa = pt["properties"]["site_anchor"]
    assert sa["anchorKind"] == "hrSurrogate"
    assert sa["clickedStream"]["nhdplusId"] == 24000800021917

    plain = json.loads(report.build_geojson(_result()))
    pt = next(f for f in plain["features"] if f["properties"]["type"] == "point")
    assert "site_anchor" not in pt["properties"]


def test_anchor_column_only_for_routed_sites():
    plain = _result()
    csv_plain = report.build_csv(plain)
    assert b",Anchor" not in csv_plain

    routed = _result()
    routed["siteAnchor"] = _hr_anchor()
    routed["report"]["metricRows"][0]["anchor"] = "surrogateWatershed"
    routed["report"]["metricRows"][0]["anchorLabel"] = "surrogate watershed"
    csv_routed = report.build_csv(routed)
    assert b"Anchor" in csv_routed
    assert b"surrogate watershed" in csv_routed

    gj = json.loads(report.build_geojson(routed))
    pt = next(f for f in gj["features"] if f["properties"]["type"] == "point")
    mid = "catchment-hydrology-impervious-surface-cover"
    assert pt["properties"]["metrics"][mid]["anchor"] == "surrogate watershed"


def test_surrogate_banner_in_pdf():
    res = _result()
    res["siteAnchor"] = _hr_anchor()
    b = report.build_pdf(res)
    assert b[:4] == b"%PDF" and len(b) > 1000
