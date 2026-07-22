"""Bundled NRSA evidence and deterministic reach-matching tests."""
from __future__ import annotations

from datetime import date

from easi.datasources import nrsa


def _record(comid, *, lat=40.0, lon=-83.0, sampled="2019-07-17", uid="1"):
    return {
        "uid": uid, "siteId": f"SITE-{uid}", "date": sampled,
        "comid": comid, "lat": lat, "lon": lon, "protocol": "WADEABLE",
        "wettedPct": 80.0, "embeddednessPct": 30.0,
        "benthicClass": "Good", "fishClass": "Fair",
    }


def test_bundled_asset_contains_mink_brook_evidence():
    record = next(item for item in nrsa._records() if item["siteId"] == "NRS18_NH_10009")
    assert record["comid"] == 9327030
    assert record["wettedPct"] == 100.0
    assert record["embeddednessPct"] == 32.36363636
    assert record["benthicClass"] == record["fishClass"] == "Good"


def test_exact_comid_precedes_connected_nearby(monkeypatch):
    monkeypatch.setattr(nrsa, "_records", lambda: (
        _record(10, lat=40.02, uid="exact"),
        _record(11, lat=40.001, uid="near"),
    ))
    monkeypatch.setattr(nrsa, "_connected_comids", lambda *args: {10, 11})
    result = nrsa.evidence_for_reach(10, 40.0, -83.0, as_of=date(2026, 7, 18))
    assert result["siteId"] == "SITE-exact"
    assert result["matchType"] == "exact" and result["distanceMi"] == 0


def test_connected_nearby_requires_nldi_confirmation(monkeypatch):
    monkeypatch.setattr(nrsa, "_records", lambda: (_record(11, lat=40.01),))
    monkeypatch.setattr(nrsa, "_connected_comids", lambda *args: {10, 11})
    matched = nrsa.evidence_for_reach(10, 40.0, -83.0, as_of=date(2026, 7, 18))
    assert matched["matchType"] == "connected_nearby"
    monkeypatch.setattr(nrsa, "_connected_comids", lambda *args: None)
    assert nrsa.evidence_for_reach(
        10, 40.0, -83.0, as_of=date(2026, 7, 18)) is None


def test_stale_record_is_not_used(monkeypatch):
    monkeypatch.setattr(nrsa, "_records", lambda: (
        _record(10, sampled="2015-01-01"),))
    assert nrsa.evidence_for_reach(
        10, 40.0, -83.0, as_of=date(2026, 7, 18)) is None
