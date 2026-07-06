"""Offline tests for the geocoder (mock requests: Photon + Nominatim fallback)."""
from __future__ import annotations

import easi.datasources.geocode as gc


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def _fake_get(photon=None, nominatim=None, photon_status=200, nominatim_status=200,
              photon_raise=False):
    def get(url, **kw):
        if "photon" in url:
            if photon_raise:
                raise RuntimeError("photon down")
            return _Resp(photon_status, photon if photon is not None else {"features": []})
        return _Resp(nominatim_status, nominatim if nominatim is not None else [])
    return get


def test_photon_returns_first_us_match(monkeypatch):
    photon = {"features": [
        {"properties": {"countrycode": "CA", "name": "Atlanta ON"},
         "geometry": {"coordinates": [-1.0, 1.0]}},                 # non-US, skipped
        {"properties": {"countrycode": "US", "name": "Atlanta", "state": "Georgia"},
         "geometry": {"coordinates": [-84.39, 33.75]}},             # lon, lat
    ]}
    monkeypatch.setattr(gc.requests, "get", _fake_get(photon=photon))
    assert gc.geocode_address("Atlanta, GA") == (33.75, -84.39)    # (lat, lon)


def test_stream_name_resolves_via_photon(monkeypatch):
    photon = {"features": [
        {"properties": {"countrycode": "US", "name": "Utoy Creek", "osm_value": "stream"},
         "geometry": {"coordinates": [-84.528, 33.741]}}]}
    monkeypatch.setattr(gc.requests, "get", _fake_get(photon=photon))
    assert gc.geocode_address("Utoy Creek, GA") == (33.741, -84.528)


def test_falls_back_to_nominatim_when_photon_empty(monkeypatch):
    monkeypatch.setattr(gc.requests, "get",
                        _fake_get(photon={"features": []},
                                  nominatim=[{"lat": "40.523", "lon": "-83.016"}]))
    assert gc.geocode_address("Olentangy River, OH") == (40.523, -83.016)


def test_falls_back_when_photon_errors(monkeypatch):
    monkeypatch.setattr(gc.requests, "get",
                        _fake_get(photon_raise=True, nominatim=[{"lat": "1.0", "lon": "2.0"}]))
    assert gc.geocode_address("anything") == (1.0, 2.0)


def test_none_on_no_match(monkeypatch):
    monkeypatch.setattr(gc.requests, "get", _fake_get(photon={"features": []}, nominatim=[]))
    assert gc.geocode_address("zzzznowhere") is None


def test_empty_input_short_circuits():
    assert gc.geocode_address("") is None
    assert gc.geocode_address("   ") is None
