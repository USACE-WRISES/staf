"""Tests for streamcurves.terrain (port of R/16_terrain_3dep.R).

Pure ``parse_*`` functions run against canned 3DEP / NLDI GeoJSON payloads;
fetchers run against a mocked requests.Session (patched into
streamcurves.datasources, whose plumbing terrain uses). Live smoke tests are
marked ``@pytest.mark.live`` and excluded by default.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
import requests

import streamcurves.datasources as ds
import streamcurves.terrain as terrain

# ── mocked session machinery ─────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, json_data=None, text=None, status=200):
        self.status_code = status
        self._json = json_data
        self.text = text if text is not None else (json.dumps(json_data) if json_data else "")

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.handler(method, url, kwargs)


def _install(monkeypatch, handler) -> FakeSession:
    fake = FakeSession(handler)
    monkeypatch.setattr(ds, "_SESSION", fake)
    return fake


def _always(response):
    return lambda method, url, kwargs: response


@pytest.fixture(autouse=True)
def _fresh_cache():
    ds.clear_ds_cache()
    yield
    ds.clear_ds_cache()


# ── canned payloads ──────────────────────────────────────────────────────────


def threedep_payload(values, locations=None, resolution=10):
    """Build a getSamples response like the 3DEP ImageServer returns."""
    samples = []
    for i, v in enumerate(values):
        s = {"locationId": i, "value": v, "rasterId": 1, "resolution": resolution}
        if locations is not None:
            x, y = locations[i]
            s["location"] = {"x": x, "y": y, "spatialReference": {"wkid": 102100}}
        samples.append(s)
    return {"samples": samples}


FLOWLINE_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[-83.0, 40.0], [-83.001, 40.001], [-83.002, 40.0015]],
            },
            "properties": {
                "nhdplus_comid": "13293452",
                "totdasqkm": 42.5,
                "gnis_name": "Big Darby Creek",
            },
        }
    ],
}

BASIN_RING = [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1], [0.0, 0.0]]

BASIN_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [BASIN_RING]},
            "properties": {},
        }
    ],
}

NLDI_POSITION_JSON = {
    "type": "FeatureCollection",
    "features": [{"type": "Feature", "properties": {"comid": "13293452"}}],
}


# ── parse_3dep_samples ───────────────────────────────────────────────────────


class TestParse3depSamples:
    def test_aligned_stations_and_nodata_drop(self):
        j = threedep_payload(["100.1", "100.2", "NoData", "100.4", "100.5", "100.6"])
        out = terrain.parse_3dep_samples(j, stations_m=[0, 10, 20, 30, 40, 50])
        assert out is not None
        assert out["stations"].tolist() == [0, 10, 30, 40, 50]  # NoData station dropped
        assert out["elevs"].tolist() == [100.1, 100.2, 100.4, 100.5, 100.6]
        assert out["resolution_m"] == 10.0

    def test_station_axis_rebuilt_from_projected_locations(self):
        # Projected (metre) coordinates keep plain Euclidean spacing:
        # 3-4-5 triangles at 1e6 scale -> 5e6 m steps.
        locs = [(0, 4e6), (3e6, 8e6), (6e6, 12e6), (9e6, 16e6), (12e6, 20e6)]
        j = threedep_payload(["1", "2", "3", "4", "5"], locations=locs)
        out = terrain.parse_3dep_samples(j)
        assert out["stations"].tolist() == [0, 5e6, 10e6, 15e6, 20e6]

    def test_station_axis_rebuilt_from_lonlat_locations_is_metres(self):
        # getSamples echoes the request SR (4326): degree coordinates must be
        # measured geodesically, not Euclidean (the R port's degree-scale
        # station-axis bug — widths rounded to 0).
        locs = [(0.000, 0), (0.001, 0), (0.002, 0), (0.003, 0), (0.004, 0)]
        j = threedep_payload(["1", "2", "3", "4", "5"], locations=locs)
        out = terrain.parse_3dep_samples(j)
        step = terrain.haversine_m(0.0, 0.0, 0.001, 0.0)  # ~111.2 m at the equator
        assert step > 100
        assert out["stations"] == pytest.approx(
            [0, step, 2 * step, 3 * step, 4 * step], rel=1e-9
        )

    def test_mismatched_stations_m_rebuilds(self):
        locs = [(0, 4e6), (3e6, 8e6), (6e6, 12e6), (9e6, 16e6), (12e6, 20e6)]
        j = threedep_payload(["1", "2", "3", "4", "5"], locations=locs)
        out = terrain.parse_3dep_samples(j, stations_m=[0, 1, 2])  # wrong length
        assert out["stations"].tolist() == [0, 5e6, 10e6, 15e6, 20e6]

    def test_index_fallback_without_locations(self):
        j = threedep_payload(["1", "2", "3", "4", "5"])
        out = terrain.parse_3dep_samples(j)
        assert out["stations"].tolist() == [0, 1, 2, 3, 4]  # R: seq_len(m) - 1

    def test_numeric_values_accepted(self):
        j = threedep_payload([100.5, 101, 102, 103, 104])
        out = terrain.parse_3dep_samples(j)
        assert out["elevs"].tolist() == [100.5, 101, 102, 103, 104]

    def test_fewer_than_five_valid_returns_none(self):
        j = threedep_payload(["1", "2", "3", "4", "NoData"])
        assert terrain.parse_3dep_samples(j) is None

    def test_empty_or_missing_samples(self):
        assert terrain.parse_3dep_samples({"samples": []}) is None
        assert terrain.parse_3dep_samples({}) is None
        assert terrain.parse_3dep_samples(None) is None


# ── 3DEP fetchers ────────────────────────────────────────────────────────────


class TestSampleTransect3dep:
    PTS = [[-83.010, 39.996], [-83.008, 39.996], [-83.006, 39.996],
           [-83.004, 39.996], [-83.002, 39.996], [-83.000, 39.996]]

    def test_request_shape_and_parse(self, monkeypatch):
        fake = _install(
            monkeypatch, _always(FakeResponse(threedep_payload(["1", "2", "3", "4", "5", "6"])))
        )
        out = terrain.sample_transect_3dep(self.PTS, stations_m=[0, 10, 20, 30, 40, 50])
        assert out["elevs"].tolist() == [1, 2, 3, 4, 5, 6]
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == terrain.THREEDEP_IMAGESERVER + "/getSamples"
        form = call["data"]
        assert form["geometryType"] == "esriGeometryPolyline"
        assert form["sampleCount"] == 6
        assert form["returnFirstValueOnly"] == "true"
        assert form["interpolation"] == "RSP_BilinearInterpolation"
        assert form["f"] == "json"
        geom = json.loads(form["geometry"])
        assert geom["spatialReference"] == {"wkid": 4326}
        assert geom["paths"] == [self.PTS]

    def test_less_than_two_points(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({})))
        assert terrain.sample_transect_3dep([[-83.0, 40.0]]) is None
        assert fake.calls == []

    def test_http_failure(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=503)))
        assert terrain.sample_transect_3dep(self.PTS) is None

    def test_extra_columns_ignored(self, monkeypatch):
        fake = _install(
            monkeypatch, _always(FakeResponse(threedep_payload(["1", "2", "3", "4", "5"])))
        )
        pts3 = [[-83.0, 40.0, 9.9], [-83.1, 40.1, 9.9], [-83.2, 40.2, 9.9],
                [-83.3, 40.3, 9.9], [-83.4, 40.4, 9.9]]
        terrain.sample_transect_3dep(pts3)
        geom = json.loads(fake.calls[0]["data"]["geometry"])
        assert geom["paths"][0][0] == [-83.0, 40.0]  # z dropped (R pts[, 1:2])


class TestSampleMultipoint3dep:
    def test_request_shape_and_nan_kept(self, monkeypatch):
        fake = _install(
            monkeypatch, _always(FakeResponse(threedep_payload(["10.5", "NoData"])))
        )
        out = terrain.sample_multipoint_3dep([[-83.0, 40.0], [-83.1, 40.1]])
        assert out["elevs"][0] == 10.5
        assert math.isnan(out["elevs"][1])  # no NoData drop, no <5 rule
        assert out["resolution_m"] == 10.0
        form = fake.calls[0]["data"]
        assert form["geometryType"] == "esriGeometryMultipoint"
        assert "sampleCount" not in form
        geom = json.loads(form["geometry"])
        assert geom["points"] == [[-83.0, 40.0], [-83.1, 40.1]]

    def test_empty_input_no_request(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({})))
        assert terrain.sample_multipoint_3dep(np.empty((0, 2))) is None
        assert fake.calls == []

    def test_http_failure(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        assert terrain.sample_multipoint_3dep([[-83.0, 40.0]]) is None

    def test_missing_samples_key(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse({"error": "boom"})))
        assert terrain.sample_multipoint_3dep([[-83.0, 40.0]]) is None

    def test_empty_samples_raises_like_r(self, monkeypatch):
        # NOTE(parity): R evaluates samples[[1]]$resolution and errors
        # ("subscript out of bounds") when the samples array is empty.
        _install(monkeypatch, _always(FakeResponse({"samples": []})))
        with pytest.raises(IndexError):
            terrain.sample_multipoint_3dep([[-83.0, 40.0]])


# ── NLDI GeoJSON parsers ─────────────────────────────────────────────────────


class TestParseNldiFlowlineCoords:
    def test_linestring_feature_collection(self):
        pts = terrain.parse_nldi_flowline_coords(FLOWLINE_FC)
        assert pts.shape == (3, 2)
        assert pts[0].tolist() == [-83.0, 40.0]
        assert pts[2].tolist() == [-83.002, 40.0015]

    def test_multilinestring_merged_in_order(self):
        j = {
            "features": [
                {
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [
                            [[-83.0, 40.0], [-83.001, 40.001]],
                            [[-83.002, 40.002], [-83.003, 40.003]],
                        ],
                    }
                }
            ]
        }
        pts = terrain.parse_nldi_flowline_coords(j)
        assert pts.shape == (4, 2)
        assert pts[:, 0].tolist() == [-83.0, -83.001, -83.002, -83.003]

    def test_multiple_features_concatenated(self):
        j = {
            "features": [
                {"geometry": {"type": "Point", "coordinates": [-83.0, 40.0]}},  # skipped
                {"geometry": {"type": "LineString", "coordinates": [[-1.0, 1.0], [-2.0, 2.0]]}},
                {"geometry": {"type": "LineString", "coordinates": [[-3.0, 3.0]]}},
            ]
        }
        pts = terrain.parse_nldi_flowline_coords(j)
        assert pts[:, 0].tolist() == [-1.0, -2.0, -3.0]

    def test_bare_feature(self):
        j = {"geometry": {"type": "LineString", "coordinates": [[-1.0, 1.0], [-2.0, 2.0]]}}
        pts = terrain.parse_nldi_flowline_coords(j)
        assert pts.shape == (2, 2)

    def test_z_coordinate_dropped(self):
        j = {"geometry": {"type": "LineString", "coordinates": [[-1.0, 1.0, 9.0], [-2.0, 2.0, 9.0]]}}
        pts = terrain.parse_nldi_flowline_coords(j)
        assert pts.shape == (2, 2)

    def test_none_cases(self):
        assert terrain.parse_nldi_flowline_coords({}) is None
        assert terrain.parse_nldi_flowline_coords({"features": []}) is None
        assert terrain.parse_nldi_flowline_coords(
            {"features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]}
        ) is None
        assert terrain.parse_nldi_flowline_coords(None) is None


class TestParseNldiBasinRing:
    def test_polygon_outer_ring_only(self):
        hole = [[0.02, 0.02], [0.04, 0.02], [0.04, 0.04], [0.02, 0.02]]
        j = {"features": [{"geometry": {"type": "Polygon", "coordinates": [BASIN_RING, hole]}}]}
        ring = terrain.parse_nldi_basin_ring(j)
        assert ring.shape == (5, 2)
        assert ring[1].tolist() == [0.1, 0.0]

    def test_multipolygon_first_outer_ring(self):
        j = {
            "features": [
                {"geometry": {"type": "MultiPolygon", "coordinates": [[BASIN_RING], [[BASIN_RING[0]]]]}}
            ]
        }
        ring = terrain.parse_nldi_basin_ring(j)
        assert ring.shape == (5, 2)

    def test_bare_feature(self):
        ring = terrain.parse_nldi_basin_ring(
            {"geometry": {"type": "Polygon", "coordinates": [BASIN_RING]}}
        )
        assert ring.shape == (5, 2)

    def test_none_cases(self):
        assert terrain.parse_nldi_basin_ring({}) is None
        assert terrain.parse_nldi_basin_ring({"features": []}) is None
        assert terrain.parse_nldi_basin_ring(
            {"features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]}
        ) is None


# ── geo helpers (fallback or streamcurves.geo — same formulas) ───────────────


class TestGeoFormulas:
    def test_spherical_area_equator_square(self):
        ring = np.array(BASIN_RING)
        area = terrain.spherical_polygon_area_m2(ring[:, 0], ring[:, 1])
        # 0.1 deg x 0.1 deg at the equator is ~123.9 km^2 (R = 6378137)
        assert 1.20e8 < area < 1.28e8

    def test_spherical_area_degenerate(self):
        assert terrain.spherical_polygon_area_m2([0, 1], [0, 1]) == 0.0

    def test_haversine(self):
        assert float(terrain.haversine_m(0, 0, 1, 0)) == pytest.approx(111194.9, rel=1e-4)
        assert float(terrain.haversine_m(5, 5, 5, 5)) == 0.0
        d = terrain.haversine_m(0, 0, np.array([1.0, 0.0]), np.array([0.0, 1.0]))
        assert np.allclose(d, [111194.9, 111194.9], rtol=1e-4)


# ── NLDI fetchers ────────────────────────────────────────────────────────────


class TestNldiBasinSqkm:
    def test_success_matches_spherical_area(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(BASIN_FC)))
        val = terrain.nldi_basin_sqkm(13293452)
        ring = np.array(BASIN_RING)
        expected = terrain.spherical_polygon_area_m2(ring[:, 0], ring[:, 1]) / 1e6
        assert val == pytest.approx(expected)
        assert 120 < val < 128
        call = fake.calls[0]
        assert call["url"] == terrain.NLDI_BASE + "/comid/13293452/basin"
        assert call["params"] == {"f": "json", "simplified": "true"}
        assert "basin:13293452" in ds._DS_CACHE

    def test_cached_second_call(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(BASIN_FC)))
        terrain.nldi_basin_sqkm(13293452)
        terrain.nldi_basin_sqkm(13293452)
        assert len(fake.calls) == 1

    def test_invalid_comid(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(BASIN_FC)))
        assert math.isnan(terrain.nldi_basin_sqkm("abc"))
        assert math.isnan(terrain.nldi_basin_sqkm(None))
        assert fake.calls == []

    def test_failure_nan_and_negative_cached(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(status=500)))
        assert math.isnan(terrain.nldi_basin_sqkm(7))
        assert math.isnan(terrain.nldi_basin_sqkm(7))
        assert len(fake.calls) == 1

    def test_many_aligned_with_progress(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(BASIN_FC)))
        ticks = []
        out = terrain.nldi_basin_sqkm_many([1, "x"], progress=lambda i, n: ticks.append((i, n)))
        assert out.shape == (2,)
        assert out[0] > 0 and math.isnan(out[1])
        assert ticks == [(1, 2), (2, 2)]


class TestNldiReach:
    def _handler(self, flowline=FLOWLINE_FC, position=NLDI_POSITION_JSON):
        def handler(method, url, kwargs):
            if url.endswith("/comid/position"):
                return FakeResponse(position)
            if url.endswith("/comid/13293452"):
                return FakeResponse(flowline)
            return FakeResponse(status=404)

        return handler

    def test_happy_path(self, monkeypatch):
        fake = _install(monkeypatch, self._handler())
        out = terrain.nldi_reach(40.1015, -83.0705)
        assert out is not None
        assert out["comid"] == 13293452
        assert out["da_sqkm"] == 42.5
        assert out["gnis_name"] == "Big Darby Creek"
        assert out["coords_lonlat"].shape == (3, 2)
        assert out["snap_lonlat"] == (-83.0705, 40.1015)
        assert [c["url"] for c in fake.calls] == [
            terrain.NLDI_BASE + "/comid/position",
            terrain.NLDI_BASE + "/comid/13293452",
        ]
        assert fake.calls[0]["params"]["coords"] == "POINT(-83.070500 40.101500)"

    def test_uppercase_props_and_missing_da(self, monkeypatch):
        fl = json.loads(json.dumps(FLOWLINE_FC))
        fl["features"][0]["properties"] = {"TotDASqKM": 99.9, "GNIS_NAME": "X Run"}
        _install(monkeypatch, self._handler(flowline=fl))
        out = terrain.nldi_reach(40.0, -83.0)
        assert out["da_sqkm"] == 99.9
        assert out["gnis_name"] == "X Run"

        fl["features"][0]["properties"] = {}
        ds.clear_ds_cache()
        out = terrain.nldi_reach(40.0, -83.0)
        assert math.isnan(out["da_sqkm"])
        assert out["gnis_name"] is None

    def test_position_miss_returns_none(self, monkeypatch):
        fake = _install(monkeypatch, self._handler(position={"features": []}))
        assert terrain.nldi_reach(40.0, -83.0) is None
        assert len(fake.calls) == 1  # never fetched the flowline

    def test_no_flowline_geometry(self, monkeypatch):
        fl = {"features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}]}
        _install(monkeypatch, self._handler(flowline=fl))
        assert terrain.nldi_reach(40.0, -83.0) is None

    def test_bare_feature_flowline_returns_none_like_r(self, monkeypatch):
        # NOTE(parity): R feat$features[[1]] errors on a bare Feature response,
        # so the whole lookup yields NULL even though the coords parsed.
        fl = {"geometry": {"type": "LineString", "coordinates": [[-1.0, 1.0], [-2.0, 2.0]]}}
        _install(monkeypatch, self._handler(flowline=fl))
        assert terrain.nldi_reach(40.0, -83.0) is None

    def test_invalid_input(self, monkeypatch):
        fake = _install(monkeypatch, self._handler())
        assert terrain.nldi_reach("y", "x") is None
        assert fake.calls == []

    def test_length_ft_accepted_but_unused(self, monkeypatch):
        _install(monkeypatch, self._handler())
        out = terrain.nldi_reach(40.1015, -83.0705, length_ft=500)
        assert out["comid"] == 13293452


# ── live smoke tests (excluded by default) ───────────────────────────────────


@pytest.mark.live
class TestLive:
    def test_sample_transect_3dep_live(self):
        lons = np.linspace(-83.010, -83.000, 20)
        lats = np.full(20, 39.996)
        out = terrain.sample_transect_3dep(np.column_stack([lons, lats]))
        assert out is not None
        assert len(out["elevs"]) >= 5
        assert np.all(np.isfinite(out["elevs"]))

    def test_nldi_reach_live(self):
        out = terrain.nldi_reach(40.1015, -83.0705)
        assert out is not None
        assert out["comid"] > 0
        assert out["coords_lonlat"].shape[1] == 2

    def test_nldi_basin_sqkm_live(self):
        comid = ds.nldi_comid(-83.0705, 40.1015)
        val = terrain.nldi_basin_sqkm(comid)
        assert val > 0
