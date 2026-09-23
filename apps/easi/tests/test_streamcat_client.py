"""StreamCat request parameters at the HTTP boundary, including the other AOI."""
from types import SimpleNamespace

import pytest

from easi.datasources import streamcat


@pytest.fixture(autouse=True)
def clean_fetch_cache():
    streamcat._fetch.cache_clear()
    yield
    streamcat._fetch.cache_clear()


def test_other_model_uses_the_verified_primary_query_parameter(monkeypatch):
    calls = []

    def get(url, *, params, timeout):
        calls.append((url, params, timeout))
        # Observed EPA behavior on 2026-09-15: areaOfInterest=other returns
        # only COMID; aoi=other returns the unsuffixed biological model.
        row = {"COMID": 8566985}
        if params.get("aoi") == "other":
            row["PRG_BMMI0809"] = 0.607666666666667
        return SimpleNamespace(status_code=200, json=lambda: {"items": [row]})

    monkeypatch.setattr(streamcat.requests, "get", get)
    row = streamcat.metrics_by_comid(8566985, ["prg_bmmi0809"], aoi="other")
    assert row["prg_bmmi0809"] == pytest.approx(0.607666666666667)
    assert calls == [(streamcat._PRIMARY,
                      {"name": "prg_bmmi0809", "aoi": "other", "comid": "8566985"}, 25.0)]
    assert streamcat.suffix("other") == ""


@pytest.mark.parametrize("aoi,suffix", [
    ("watershed", "ws"), ("catchment", "cat"),
    ("riparian_watershed", "wsrp100"), ("riparian_catchment", "catrp100"),
])
def test_standard_aoi_parameters_and_response_columns_stay_unchanged(monkeypatch, aoi, suffix):
    calls = []
    response = {"COMID": 8566985, "PCTIMP2019WS": 8, "HYDCAT": .7, "PCTCONIF2019WSRP100": 12}

    def get(url, *, params, timeout):
        calls.append(params)
        return SimpleNamespace(status_code=200, json=lambda: {"items": [response]})

    monkeypatch.setattr(streamcat.requests, "get", get)
    row = streamcat.metrics_by_comid(8566985, ["pctimp2019"], aoi=aoi)
    assert calls == [{"name": "pctimp2019", "areaOfInterest": aoi, "comid": "8566985"}]
    assert row == {"comid": 8566985.0, "pctimp2019ws": 8.0, "hydcat": .7, "pctconif2019wsrp100": 12.0}
    assert streamcat.suffix(aoi) == suffix


@pytest.mark.parametrize("aoi,parameter", [("other", "aoi"), ("watershed", "areaOfInterest")])
def test_primary_failure_preserves_the_query_on_the_mirror(monkeypatch, aoi, parameter):
    calls = []

    def request(url, params, timeout):
        calls.append((url, params))
        return None if url == streamcat._PRIMARY else {"items": [{"COMID": 1, "model": None}]}

    monkeypatch.setattr(streamcat, "_request", request)
    assert streamcat.metrics_by_comid(1, ["model"], aoi=aoi) == {"comid": 1.0, "model": None}
    expected = {"name": "model", parameter: aoi, "comid": "1"}
    assert calls == [(streamcat._PRIMARY, expected), (streamcat._MIRROR, expected)]


def test_an_unanswered_request_is_asked_again_and_an_answer_is_kept(monkeypatch):
    answers = [None, None, {"items": [{"COMID": 7, "PCTIMP2019WS": 3}]}, None]
    calls = []

    def request(url, params, timeout):
        calls.append(url)
        return answers.pop(0)

    monkeypatch.setattr(streamcat, "_request", request)
    assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {}          # both hosts silent
    assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {"comid": 7.0, "pctimp2019ws": 3.0}
    assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {"comid": 7.0, "pctimp2019ws": 3.0}
    assert calls == [streamcat._PRIMARY, streamcat._MIRROR, streamcat._PRIMARY]


def test_an_answer_with_no_row_is_kept(monkeypatch):
    calls = []
    monkeypatch.setattr(streamcat, "_request",
                        lambda url, params, timeout: calls.append(url) or {"items": []})
    assert streamcat.metrics_by_comid(8, ["pctimp2019"]) == {}
    assert streamcat.metrics_by_comid(8, ["pctimp2019"]) == {}
    assert calls == [streamcat._PRIMARY]
