"""Tests for streamcurves.datasources (port of app/helpers/data_sources.R).

Pure ``parse_*`` functions run against canned JSON/CSV payloads; fetchers run
against a mocked requests.Session (no network). Live smoke tests are marked
``@pytest.mark.live`` and excluded by default (``-m "not live"``).
"""

from __future__ import annotations

import json
import math
import os

import pytest
import requests

import streamcurves.datasources as ds
from streamcurves.datasources import mmw as mmw_mod

# ── mocked session machinery ─────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, json_data=None, text=None, status=200):
        self.status_code = status
        self._json = json_data
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(json_data) if json_data is not None else ""

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Routes every request through ``handler(method, url, kwargs) -> FakeResponse``."""

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


@pytest.fixture()
def no_mmw_key(monkeypatch):
    monkeypatch.delenv("MMW_API_KEY", raising=False)


@pytest.fixture()
def mmw_key(monkeypatch):
    monkeypatch.setenv("MMW_API_KEY", "test-key")
    monkeypatch.setattr(mmw_mod, "_sleep", lambda s: None)
    monkeypatch.setattr(mmw_mod, "_last_request_ts", float("-inf"))


# ── canned payloads (shapes derived from the R parsing code) ─────────────────

NLDI_POSITION_JSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-83.0, 40.0]},
            "properties": {"identifier": "13293452", "comid": "13293452", "source": "comid"},
        }
    ],
}

STREAMCAT_ITEMS_JSON = {
    "items": [
        {
            "comid": 13293452,
            "pctimp2019ws": 4.27,
            "pctimp2019cat": 6.1,
            "pcturbmd2019wsrp100": 1.2,
            "pcturbmd2019catrp100": 0.9,
            "wsareasqkm": 55.3,
        },
        {
            "comid": 13293454,
            "pctimp2019ws": 2.11,
            "pctimp2019cat": 3.0,
            "pcturbmd2019wsrp100": 0.8,
            "pcturbmd2019catrp100": 0.5,
            "wsareasqkm": 12.9,
        },
    ]
}

STREAMCAT_CSV = "COMID,PCTIMP2019WS\n13293452,4.27\n13293454,2.11\n"

SS_META_JSON = [
    {"code": "DRNAREA", "name": "Drainage Area", "unit": "square miles"},
    {"code": "FOREST", "name": "Percent Forest"},  # no unit -> ""
]

SS_CALC_JSON = [
    {"code": "DRNAREA", "value": 24.79, "msg": "Local SSZonal successful"},
    {"code": "PRECIP", "value": 39.1, "msg": "Local SSZonal successful"},
    {"code": "FOREST", "value": 55.0, "msg": "Server SSZonal failed"},
]

MMW_SURVEYS = {
    "land/2019_2019": {
        "displayName": "Land",
        "categories": [
            {"code": "developed_open", "coverage": 0.05},
            {"code": "developed_low", "coverage": 0.10},
            {"code": "deciduous_forest", "coverage": 0.20},
            {"code": "mixed_forest", "coverage": 0.05},
            {"code": "pasture", "coverage": 0.15},
            {"code": "cultivated_crops", "coverage": 0.25},
            {"code": "woody_wetlands", "coverage": 0.02},
        ],
    },
    "soil": {
        "categories": [
            {"code": "a", "coverage": 0.3},
            {"code": "c", "coverage": 0.2},
            {"code": "d", "coverage": 0.1},
            {"code": "bd", "coverage": 0.05},
        ]
    },
    "terrain": {
        "categories": [
            {"type": "minimum", "elevation": 200.0, "slope": 0.0},
            {"type": "average", "elevation": 284.2, "slope": 7.3},
        ]
    },
    "climate": {"categories": [{"month": "Jan", "ppt": 7.6}, {"month": "Feb", "ppt": 6.4}]},
}

WATERSHED_GEOM = {
    "type": "Polygon",
    "coordinates": [[[-83.2, 40.0], [-83.1, 40.0], [-83.1, 40.1], [-83.2, 40.0]]],
}


# ── parse_nldi_comid ─────────────────────────────────────────────────────────


class TestParseNldiComid:
    def test_comid_key(self):
        assert ds.parse_nldi_comid(NLDI_POSITION_JSON) == 13293452

    def test_uppercase_comid(self):
        j = {"features": [{"properties": {"COMID": 42}}]}
        assert ds.parse_nldi_comid(j) == 42

    def test_identifier_fallback(self):
        j = {"features": [{"properties": {"identifier": "99"}}]}
        assert ds.parse_nldi_comid(j) == 99

    def test_empty_features(self):
        assert ds.parse_nldi_comid({"features": []}) is None
        assert ds.parse_nldi_comid({}) is None

    def test_no_properties(self):
        assert ds.parse_nldi_comid({"features": [{}]}) is None

    def test_non_numeric(self):
        j = {"features": [{"properties": {"comid": "not-a-number"}}]}
        assert ds.parse_nldi_comid(j) is None


# ── StreamCAT parsers ────────────────────────────────────────────────────────


class TestParseStreamcatJson:
    def test_ws_suffix_keeps_ws_and_suffixless(self):
        df = ds.parse_streamcat_json(STREAMCAT_ITEMS_JSON, "ws")
        assert list(df.columns) == ["COMID", "pctimp2019ws", "wsareasqkm"]
        assert df["COMID"].tolist() == [13293452, 13293454]
        assert df["pctimp2019ws"].tolist() == [4.27, 2.11]

    def test_cat_suffix(self):
        df = ds.parse_streamcat_json(STREAMCAT_ITEMS_JSON, "cat")
        # catrp100 does NOT end with "cat"; suffix-less wsareasqkm is kept
        assert list(df.columns) == ["COMID", "pctimp2019cat", "wsareasqkm"]

    def test_missing_metric_becomes_nan(self):
        j = {"items": [{"comid": 1, "pctimp2019ws": 4.0}, {"comid": 2}]}
        df = ds.parse_streamcat_json(j, "ws")
        assert math.isnan(df["pctimp2019ws"].iloc[1])

    def test_empty_items(self):
        for j in ({}, {"items": []}, None):
            df = ds.parse_streamcat_json(j, "ws")
            assert list(df.columns) == ["COMID"]
            assert len(df) == 0

    def test_no_comid_column_raises_like_r(self):
        # R: df[, c("COMID", keep)] errors when the API returned no comid column
        with pytest.raises(KeyError):
            ds.parse_streamcat_json({"items": [{"pctimp2019ws": 1.0}]}, "ws")


class TestParseStreamcatCsv:
    def test_basic(self):
        df = ds.parse_streamcat_csv(STREAMCAT_CSV)
        assert "COMID" in df.columns
        assert df["COMID"].tolist() == [13293452, 13293454]
        assert df["PCTIMP2019WS"].tolist() == [4.27, 2.11]

    def test_empty_text(self):
        for text in (None, "", "   "):
            df = ds.parse_streamcat_csv(text)
            assert list(df.columns) == ["COMID"]
            assert len(df) == 0

    def test_header_only(self):
        df = ds.parse_streamcat_csv("COMID,PCTIMP2019WS\n")
        assert list(df.columns) == ["COMID"]
        assert len(df) == 0


class TestParseStreamcatCatalog:
    def test_items_of_records(self):
        j = {"items": [{"name": "pctimp2019"}, {"name": "fert"}, {"metric": "rddens"}]}
        df = ds.parse_streamcat_catalog(j)
        assert df["name"].tolist() == ["pctimp2019", "fert", "rddens"]
        assert df["domain"].isna().all()

    def test_plain_string_items(self):
        df = ds.parse_streamcat_catalog({"metrics": ["a", "b"]})
        assert df["name"].tolist() == ["a", "b"]

    def test_parameters_key(self):
        df = ds.parse_streamcat_catalog({"parameters": [{"name": "x"}]})
        assert df["name"].tolist() == ["x"]

    def test_name_key_fallback(self):
        df = ds.parse_streamcat_catalog({"items": [], "name": ["a", "b"]})
        assert df["name"].tolist() == ["a", "b"]

    def test_empty(self):
        df = ds.parse_streamcat_catalog({})
        assert list(df.columns) == ["name", "domain"]
        assert len(df) == 0


# ── StreamStats parsers ──────────────────────────────────────────────────────


class TestParseSs:
    def test_bc_meta(self):
        df = ds.parse_ss_bc_meta(SS_META_JSON)
        assert list(df.columns) == ["code", "name", "unit"]
        assert df["code"].tolist() == ["DRNAREA", "FOREST"]
        assert df["unit"].tolist() == ["square miles", ""]

    def test_bc_meta_empty(self):
        assert ds.parse_ss_bc_meta([]) is None
        assert ds.parse_ss_bc_meta(None) is None

    def test_bcs_keeps_only_successful(self):
        out = ds.parse_ss_bcs(SS_CALC_JSON)
        assert out["DRNAREA"] == 24.79
        assert out["PRECIP"] == 39.1
        assert math.isnan(out["FOREST"])  # msg != "Local SSZonal successful"

    def test_bcs_missing_value(self):
        out = ds.parse_ss_bcs([{"code": "X", "msg": "Local SSZonal successful"}])
        assert math.isnan(out["X"])

    def test_bcs_empty(self):
        assert ds.parse_ss_bcs([]) == {}
        assert ds.parse_ss_bcs(None) == {}

    def test_core_bcs(self):
        bcs = ds.ss_core_bcs()
        assert bcs["DRNAREA"] == "Drainage area (sq mi)"
        assert list(bcs)[0] == "DRNAREA"
        assert len(bcs) == 6


# ── EPQS parser ──────────────────────────────────────────────────────────────


class TestParseEpqs:
    def test_value_string(self):
        assert ds.parse_epqs({"value": "252.61"}) == 252.61

    def test_value_number(self):
        assert ds.parse_epqs({"value": 199}) == 199.0

    def test_elevation_fallback(self):
        assert ds.parse_epqs({"elevation": 88.5}) == 88.5

    def test_missing(self):
        assert math.isnan(ds.parse_epqs({}))
        assert math.isnan(ds.parse_epqs({"value": "no data"}))


# ── MMW extraction helpers (pure) ────────────────────────────────────────────


class TestMmwExtract:
    def test_cov_sum(self):
        land = MMW_SURVEYS["land/2019_2019"]["categories"]
        s = mmw_mod._mmw_cov_sum(land, ("developed_open", "developed_low"))
        assert s == pytest.approx(0.15)

    def test_cov_sum_no_match_is_nan(self):
        land = MMW_SURVEYS["land/2019_2019"]["categories"]
        assert math.isnan(mmw_mod._mmw_cov_sum(land, ("nope",)))
        assert math.isnan(mmw_mod._mmw_cov_sum([], ("developed_low",)))

    def test_cov_sum_missing_coverage_counts_as_zero(self):
        assert mmw_mod._mmw_cov_sum([{"code": "c"}], ("c",)) == 0.0

    def test_terrain_avg(self):
        terr = MMW_SURVEYS["terrain"]["categories"]
        assert mmw_mod._mmw_terrain_avg(terr, "slope") == 7.3
        assert math.isnan(mmw_mod._mmw_terrain_avg([], "slope"))
        assert math.isnan(mmw_mod._mmw_terrain_avg([{"type": "average"}], "slope"))

    def test_extract_all_codes(self):
        assert ds.mmw_extract("mmw_developed_pct", MMW_SURVEYS) == pytest.approx(15.0)
        assert ds.mmw_extract("mmw_forest_pct", MMW_SURVEYS) == pytest.approx(25.0)
        assert ds.mmw_extract("mmw_agriculture_pct", MMW_SURVEYS) == pytest.approx(40.0)
        assert ds.mmw_extract("mmw_wetland_pct", MMW_SURVEYS) == pytest.approx(2.0)
        assert ds.mmw_extract("mmw_soil_cd_pct", MMW_SURVEYS) == pytest.approx(35.0)
        assert ds.mmw_extract("mmw_mean_slope_pct", MMW_SURVEYS) == 7.3
        assert ds.mmw_extract("mmw_mean_elev_m", MMW_SURVEYS) == 284.2
        assert ds.mmw_extract("mmw_annual_precip_cm", MMW_SURVEYS) == pytest.approx(14.0)

    def test_extract_missing_survey_or_code(self):
        assert math.isnan(ds.mmw_extract("mmw_developed_pct", {}))
        assert math.isnan(ds.mmw_extract("mmw_annual_precip_cm", {}))
        assert math.isnan(ds.mmw_extract("unknown_code", MMW_SURVEYS))

    def test_core_metrics_shape(self):
        meta = ds.mmw_core_metrics()
        assert set(meta) == {
            "mmw_developed_pct",
            "mmw_forest_pct",
            "mmw_agriculture_pct",
            "mmw_wetland_pct",
            "mmw_soil_cd_pct",
            "mmw_mean_slope_pct",
            "mmw_mean_elev_m",
            "mmw_annual_precip_cm",
        }
        assert meta["mmw_soil_cd_pct"]["category"] == "soil"


# ── nldi_comid fetcher ───────────────────────────────────────────────────────


class TestNldiComid:
    def test_success_and_request_shape(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        assert ds.nldi_comid(-83.1234567, 40.7654321) == 13293452
        call = fake.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == ds.NLDI_POSITION_URL
        assert call["params"] == {"coords": "POINT(-83.123457 40.765432)", "f": "json"}
        assert call["timeout"] == 30

    def test_cached_second_call(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        ds.nldi_comid(-83.0, 40.0)
        ds.nldi_comid(-83.0, 40.0)
        assert len(fake.calls) == 1
        assert "comid:-83.000000:40.000000" in ds._DS_CACHE

    def test_failure_returns_none_and_is_negative_cached(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(status=500)))
        assert ds.nldi_comid(-83.0, 40.0) is None
        assert ds.nldi_comid(-83.0, 40.0) is None  # cached miss, not refetched
        assert len(fake.calls) == 1

    def test_invalid_coords_no_request(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        assert ds.nldi_comid("abc", 40.0) is None
        assert ds.nldi_comid(None, None) is None
        assert fake.calls == []

    def test_nldi_comids_aligned_with_progress(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        ticks = []
        out = ds.nldi_comids([-83.0, -84.0], [40.0, 41.0], progress=lambda i, n: ticks.append((i, n)))
        assert out == [13293452, 13293452]
        assert ticks == [(1, 2), (2, 2)]


# ── streamcat fetchers ───────────────────────────────────────────────────────


class TestStreamcatMetrics:
    def test_primary_json(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(STREAMCAT_ITEMS_JSON)))
        df = ds.streamcat_metrics([13293452, 13293454], ["PCTIMP2019"])
        assert list(df.columns) == ["COMID", "pctimp2019ws", "wsareasqkm"]
        assert len(df) == 2
        call = fake.calls[0]
        assert call["url"] == ds.STREAMCAT_PRIMARY
        assert call["params"] == {
            "name": "pctimp2019",  # lower-cased
            "areaOfInterest": "watershed",
            "comid": "13293452,13293454",
        }

    def test_mirror_fallback_csv(self, monkeypatch):
        def handler(method, url, kwargs):
            if url == ds.STREAMCAT_PRIMARY:
                return FakeResponse(status=503)
            return FakeResponse(text=STREAMCAT_CSV)

        fake = _install(monkeypatch, handler)
        df = ds.streamcat_metrics([13293452, 13293454], ["pctimp2019"])
        assert df["COMID"].tolist() == [13293452, 13293454]
        assert "PCTIMP2019WS" in df.columns
        assert [c["url"] for c in fake.calls] == [ds.STREAMCAT_PRIMARY, ds.STREAMCAT_MIRROR]

    def test_both_fail_empty_frame(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        df = ds.streamcat_metrics([1, 2], ["pctimp2019"])
        assert list(df.columns) == ["COMID"]
        assert len(df) == 0

    def test_batching_and_progress(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(STREAMCAT_ITEMS_JSON)))
        ticks = []
        ds.streamcat_metrics([1, 2, 3], ["m"], batch=2, progress=lambda k, n: ticks.append((k, n)))
        primary = [c for c in fake.calls if c["url"] == ds.STREAMCAT_PRIMARY]
        assert [c["params"]["comid"] for c in primary] == ["1,2", "3"]
        assert ticks == [(1, 2), (2, 2)]

    def test_comids_deduped_and_invalid_dropped(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(STREAMCAT_ITEMS_JSON)))
        ds.streamcat_metrics(["7", 7, None, "x", 9], ["m"])
        assert fake.calls[0]["params"]["comid"] == "7,9"

    def test_empty_inputs(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(STREAMCAT_ITEMS_JSON)))
        assert len(ds.streamcat_metrics([], ["m"])) == 0
        assert len(ds.streamcat_metrics([1], [])) == 0
        assert fake.calls == []


class TestStreamcatCatalog:
    def test_network_success(self, monkeypatch):
        fake = _install(
            monkeypatch, _always(FakeResponse({"items": [{"name": "pctimp2019"}]}))
        )
        df = ds.streamcat_catalog()
        assert df["name"].tolist() == ["pctimp2019"]
        assert fake.calls[0]["url"] == ds.STREAMCAT_MIRROR
        assert fake.calls[0]["params"] is None  # parameterless

    def test_fallback_csv(self, monkeypatch, tmp_path):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        p = tmp_path / "catalog.csv"
        p.write_text("name,domain\npctimp2019,Land cover\n", encoding="utf-8")
        df = ds.streamcat_catalog(fallback_csv=p)
        assert df["name"].tolist() == ["pctimp2019"]
        assert df["domain"].tolist() == ["Land cover"]

    def test_default_fallback_is_bundled_csv(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        df = ds.streamcat_catalog()  # falls back to DATA_DIR/streamcat_metrics.csv
        assert len(df) > 0
        assert "name" in df.columns

    def test_no_fallback_available(self, monkeypatch, tmp_path):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        df = ds.streamcat_catalog(fallback_csv=tmp_path / "missing.csv")
        assert list(df.columns) == ["name", "domain"]
        assert len(df) == 0


# ── StreamStats fetchers ─────────────────────────────────────────────────────


class TestSsStateBcs:
    def test_success(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(SS_META_JSON)))
        df = ds.ss_state_bcs("oh")
        assert df["code"].tolist() == ["DRNAREA", "FOREST"]
        assert fake.calls[0]["url"] == ds.SS_HYDRO + "/basin-characteristics/OH"

    def test_failure(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        assert ds.ss_state_bcs("OH") is None

    def test_invalid_state(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(SS_META_JSON)))
        assert ds.ss_state_bcs(None) is None
        assert ds.ss_state_bcs("") is None
        assert fake.calls == []


class TestSsBasinCharacteristics:
    def test_success_alignment_and_411_workaround(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(SS_CALC_JSON)))
        out = ds.ss_basin_characteristics(40.1, -83.123456, "oh", ["PRECIP", "DRNAREA", "MISSING"])
        assert out["PRECIP"] == 39.1
        assert out["DRNAREA"] == 24.79
        assert math.isnan(out["MISSING"])
        assert list(out) == ["PRECIP", "DRNAREA", "MISSING"]  # aligned to request order
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == ds.SS_HYDRO + "/basin-characteristics/calculate-using-ssdelineate/"
        assert call["params"]["region"] == "OH"
        assert call["params"]["BCs"] == "PRECIP,DRNAREA,MISSING"  # request order, not sorted
        assert call["data"] == b""  # empty raw body (411 workaround)
        assert call["headers"]["Content-Type"] == "application/json"

    def test_failed_bc_is_nan(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(SS_CALC_JSON)))
        out = ds.ss_basin_characteristics(40.1, -83.1, "OH", ["FOREST"])
        assert math.isnan(out["FOREST"])

    def test_network_failure_all_nan(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=503)))
        out = ds.ss_basin_characteristics(40.1, -83.1, "OH", ["DRNAREA", "PRECIP"])
        assert all(math.isnan(v) for v in out.values())
        assert list(out) == ["DRNAREA", "PRECIP"]

    def test_cache_key_sorted_codes(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(SS_CALC_JSON)))
        ds.ss_basin_characteristics(40.1, -83.123456, "OH", ["PRECIP", "DRNAREA"])
        assert "ssbc:OH:-83.12346:40.10000:DRNAREA,PRECIP" in ds._DS_CACHE
        # same code set in a different order hits the cache
        ds.ss_basin_characteristics(40.1, -83.123456, "OH", ["DRNAREA", "PRECIP"])
        assert len(fake.calls) == 1

    def test_invalid_inputs_no_request(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(SS_CALC_JSON)))
        assert all(
            math.isnan(v) for v in ds.ss_basin_characteristics("x", -83.1, "OH", ["A"]).values()
        )
        assert ds.ss_basin_characteristics(40.1, -83.1, "OH", []) == {}
        assert all(
            math.isnan(v) for v in ds.ss_basin_characteristics(40.1, -83.1, None, ["A"]).values()
        )
        assert fake.calls == []


# ── EPQS fetcher ─────────────────────────────────────────────────────────────


class TestEpqsElev:
    def test_success(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({"value": "252.61"})))
        assert ds.epqs_elev(-83.1234567, 40.7654321) == 252.61
        call = fake.calls[0]
        assert call["url"] == ds.EPQS_URL
        assert call["params"] == {"x": -83.1234567, "y": 40.7654321, "units": "Meters", "wkid": 4326}
        assert "epqs:-83.123457:40.765432" in ds._DS_CACHE

    def test_failure_nan_and_cached(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse(status=500)))
        assert math.isnan(ds.epqs_elev(-83.0, 40.0))
        assert math.isnan(ds.epqs_elev(-83.0, 40.0))
        assert len(fake.calls) == 1

    def test_invalid_coords(self, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({"value": 1})))
        assert math.isnan(ds.epqs_elev(float("nan"), 40.0))
        assert fake.calls == []


# ── MMW fetchers ─────────────────────────────────────────────────────────────


class TestMmwGating:
    def test_unavailable_without_key(self, no_mmw_key, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({})))
        assert ds.mmw_token() is None
        assert ds.mmw_available() is False
        assert ds.mmw_delineate(40.0, -83.0) is None
        assert ds.mmw_analyze_geom(WATERSHED_GEOM, "soil") is None
        out = ds.mmw_site_metrics(40.0, -83.0, ["mmw_forest_pct"])
        assert math.isnan(out["mmw_forest_pct"])
        assert fake.calls == []

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("MMW_API_KEY", "abc123")
        assert ds.mmw_token() == "abc123"
        assert ds.mmw_available() is True


class TestMmwDelineate:
    def _handler(self, polls_until_complete=1):
        state = {"polls": 0}

        def handler(method, url, kwargs):
            if url.endswith("/api/watershed/"):
                return FakeResponse({"job": "job-1", "status": "started"})
            if "/api/jobs/job-1/" in url:
                state["polls"] += 1
                if state["polls"] >= polls_until_complete:
                    return FakeResponse(
                        {"status": "complete", "result": {"watershed": {"geometry": WATERSHED_GEOM}}}
                    )
                return FakeResponse({"status": "started"})
            return FakeResponse(status=404)

        return handler

    def test_happy_path(self, mmw_key, monkeypatch):
        fake = _install(monkeypatch, self._handler(polls_until_complete=2))
        geom = ds.mmw_delineate(40.05, -83.2)
        assert geom == WATERSHED_GEOM
        sub = fake.calls[0]
        assert sub["method"] == "POST"
        assert sub["url"] == ds.MMW_BASE + "/watershed/"
        assert sub["json"] == {
            "location": [40.05, -83.2],
            "snappingOn": True,
            "simplify": 0,
            "dataSource": "nhd",
        }
        assert sub["headers"]["Authorization"] == "Token test-key"
        assert sub["timeout"] == 60
        assert "mmwdel:-83.20000:40.05000" in ds._DS_CACHE

    def test_cached_second_call(self, mmw_key, monkeypatch):
        fake = _install(monkeypatch, self._handler())
        ds.mmw_delineate(40.05, -83.2)
        n = len(fake.calls)
        assert ds.mmw_delineate(40.05, -83.2) == WATERSHED_GEOM
        assert len(fake.calls) == n

    def test_failed_job_negative_cached(self, mmw_key, monkeypatch):
        def handler(method, url, kwargs):
            if url.endswith("/api/watershed/"):
                return FakeResponse({"job": "job-2"})
            return FakeResponse({"status": "failed"})

        fake = _install(monkeypatch, handler)
        assert ds.mmw_delineate(41.0, -82.0) is None
        n = len(fake.calls)
        assert ds.mmw_delineate(41.0, -82.0) is None  # cached None, no refetch
        assert len(fake.calls) == n

    def test_no_job_id(self, mmw_key, monkeypatch):
        _install(monkeypatch, _always(FakeResponse({"unexpected": True})))
        assert ds.mmw_delineate(41.0, -82.0) is None


class TestMmwAnalyze:
    def test_direct_survey_without_job(self, mmw_key, monkeypatch):
        fake = _install(
            monkeypatch, _always(FakeResponse({"survey": MMW_SURVEYS["soil"]}))
        )
        survey = ds.mmw_analyze_geom(WATERSHED_GEOM, "soil")
        assert survey == MMW_SURVEYS["soil"]
        assert fake.calls[0]["url"] == ds.MMW_BASE + "/analyze/soil/"
        assert fake.calls[0]["json"] == WATERSHED_GEOM
        assert len(fake.calls) == 1  # no polling

    def test_job_then_poll(self, mmw_key, monkeypatch):
        def handler(method, url, kwargs):
            if "/api/analyze/" in url:
                return FakeResponse({"job": "a-1"})
            return FakeResponse({"status": "complete", "result": {"survey": MMW_SURVEYS["terrain"]}})

        _install(monkeypatch, handler)
        assert ds.mmw_analyze_geom(WATERSHED_GEOM, "terrain") == MMW_SURVEYS["terrain"]

    def test_cached_per_geometry_and_category(self, mmw_key, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({"survey": {"categories": []}})))
        ds.mmw_analyze_geom(WATERSHED_GEOM, "soil")
        ds.mmw_analyze_geom(WATERSHED_GEOM, "soil")
        assert len(fake.calls) == 1
        ds.mmw_analyze_geom(WATERSHED_GEOM, "terrain")
        assert len(fake.calls) == 2
        gkey = json.dumps(WATERSHED_GEOM, separators=(",", ":"))[:96]
        assert f"mmwana:soil:{gkey}" in ds._DS_CACHE

    def test_none_geom(self, mmw_key, monkeypatch):
        fake = _install(monkeypatch, _always(FakeResponse({})))
        assert ds.mmw_analyze_geom(None, "soil") is None
        assert fake.calls == []


class TestMmwSiteMetrics:
    def test_end_to_end(self, mmw_key, monkeypatch):
        def handler(method, url, kwargs):
            if url.endswith("/api/watershed/"):
                return FakeResponse({"job": "w-1"})
            if "/api/jobs/w-1/" in url:
                return FakeResponse(
                    {"status": "complete", "result": {"watershed": {"geometry": WATERSHED_GEOM}}}
                )
            if "/api/analyze/terrain/" in url:
                return FakeResponse({"survey": MMW_SURVEYS["terrain"]})
            if "/api/analyze/climate/" in url:
                return FakeResponse({"job": "c-1"})
            if "/api/jobs/c-1/" in url:
                return FakeResponse(
                    {"status": "complete", "result": {"survey": MMW_SURVEYS["climate"]}}
                )
            return FakeResponse(status=404)

        _install(monkeypatch, handler)
        out = ds.mmw_site_metrics(
            40.05, -83.2, ["mmw_mean_slope_pct", "mmw_annual_precip_cm", "bogus"]
        )
        assert out["mmw_mean_slope_pct"] == 7.3
        assert out["mmw_annual_precip_cm"] == pytest.approx(14.0)
        assert math.isnan(out["bogus"])

    def test_delineation_failure_all_nan(self, mmw_key, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(status=500)))
        out = ds.mmw_site_metrics(40.05, -83.2, ["mmw_forest_pct", "mmw_mean_elev_m"])
        assert list(out) == ["mmw_forest_pct", "mmw_mean_elev_m"]
        assert all(math.isnan(v) for v in out.values())

    def test_poll_sleeps_before_first_check(self, mmw_key, monkeypatch):
        sleeps = []
        monkeypatch.setattr(mmw_mod, "_sleep", lambda s: sleeps.append(s))

        def handler(method, url, kwargs):
            if url.endswith("/api/watershed/"):
                return FakeResponse({"job": "w-9"})
            return FakeResponse(
                {"status": "complete", "result": {"watershed": {"geometry": WATERSHED_GEOM}}}
            )

        _install(monkeypatch, handler)
        ds.mmw_delineate(42.0, -80.0)
        assert 2 in sleeps  # mmw_poll's interval sleep happens before the first poll


# ── cache plumbing ───────────────────────────────────────────────────────────


class TestCache:
    def test_clear_ds_cache(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        ds.nldi_comid(-83.0, 40.0)
        assert ds._DS_CACHE
        ds.clear_ds_cache()
        assert ds._DS_CACHE == {}

    def test_cache_key_formats(self, monkeypatch):
        _install(monkeypatch, _always(FakeResponse(NLDI_POSITION_JSON)))
        ds.nldi_comid(-83.1234567, 40.7654321)
        assert set(ds._DS_CACHE) == {"comid:-83.123457:40.765432"}


# ── live smoke tests (excluded by default) ───────────────────────────────────


@pytest.mark.live
class TestLive:
    def test_nldi_comid_live(self):
        comid = ds.nldi_comid(-83.0705, 40.1015)
        assert isinstance(comid, int) and comid > 0

    def test_epqs_elev_live(self):
        elev = ds.epqs_elev(-83.0705, 40.1015)
        assert math.isfinite(elev) and -100 < elev < 4000

    def test_streamcat_metrics_live(self):
        comid = ds.nldi_comid(-83.0705, 40.1015)
        df = ds.streamcat_metrics([comid], ["PCTIMP2019"])
        assert len(df) == 1
        assert any(c.lower().endswith("ws") for c in df.columns if c != "COMID")

    def test_streamcat_catalog_live(self):
        df = ds.streamcat_catalog()
        assert len(df) > 0

    def test_ss_state_bcs_live(self):
        df = ds.ss_state_bcs("OH")
        assert df is not None and len(df) > 0
        assert "code" in df.columns

    def test_ss_basin_characteristics_live(self):
        out = ds.ss_basin_characteristics(40.1015, -83.0705, "OH", ["DRNAREA"])
        assert "DRNAREA" in out

    @pytest.mark.skipif(not os.environ.get("MMW_API_KEY"), reason="MMW_API_KEY not set")
    def test_mmw_delineate_live(self):
        geom = ds.mmw_delineate(39.8522, -75.5983)  # MMW's own demo point
        assert geom is None or geom.get("type") in ("Polygon", "MultiPolygon")
