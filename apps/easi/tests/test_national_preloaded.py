"""The precomputed path scores exactly like the live path.

``assessment.assess_preloaded`` over an evidence record must reproduce the
committed parity golden that ``tests/test_batch_parity.py`` locks for the live
``assess`` with the same stubbed inputs, and the point-service providers must
answer only for the registered point.
"""
from __future__ import annotations

import json

import pytest

from easi import assessment
from easi.datasources import attains, nas, nid_barriers, wqp
from easi.national import method_version, providers, records
from easi.national import client
from test_batch_parity import BIEGER, GOLDEN, REACH_GEOMORPH, STREAMCAT, _parity_view

LAT, LON = 40.10, -83.10


def _record(**overrides) -> dict:
    rec = {
        "comid": 1234567, "huc4": "0102", "huc8": "01020304", "huc12": "010203040506",
        "vpu": "01", "gnis_name": "Test Creek", "streamorde": 3, "fcode": 46006,
        "totdasqkm": 50.0, "lengthkm": 2.0, "slope": 0.005, "sinuosity": 1.2,
        "lat": LAT, "lon": LON, "hydroseq": 1, "dnhydroseq": 0, "levelpathi": 1,
        "tocomid": 0,
        "streamcat": dict(STREAMCAT), "nrsa": None,
        "bankfull": {**BIEGER, "extrapolated": False, "fit_range_sqkm": None},
        "attains_exact": {}, "attains_nearby": {}, "wqp_tn": None, "wqp_tp": None,
        "nid_dams": [], "nas_taxa": [], "nas_scope": "huc12",
        "geomorph": dict(REACH_GEOMORPH),
    }
    rec.update(overrides)
    return rec


@pytest.fixture
def clean_providers():
    providers.uninstall()
    yield
    providers.uninstall()


def test_preloaded_matches_the_live_parity_golden(clean_providers):
    report = client.score_record(_record(), cross_section=False)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert _parity_view(report) == golden
    assert report["precomputed"]["schema"] == 1
    assert report["precomputed"]["method_current"] is True
    assert report["basin"]["rows"]


def test_preloaded_is_deterministic(clean_providers):
    a = _parity_view(client.score_record(_record(), cross_section=False))
    b = _parity_view(client.score_record(_record(), cross_section=False))
    assert a == b


def test_apply_evidence_populates_the_context_like_the_prefetch():
    ctx = records.build_context(_record())
    assert ctx.comid == 1234567 and ctx.huc12 == "010203040506"
    assert ctx.drainage_area_sqkm == 50.0 and ctx.stream_order == 3
    assert ctx.extras["streamcat"]["pctimp2019ws"] == 8.0
    assert ctx.extras["watershed"]["provider"] == "streamcat"
    assert ctx.extras["reach_geomorph"]["entrenchment_ratio"] == 2.5
    assert ctx.extras["reach_geomorph"]["bankfull_extrapolated"] is False
    assert ctx.extras["siteAnchor"]["anchorKind"] == "v2Direct"
    assert ctx.extras["siteAnchor"]["scoredReach"]["gnisName"] == "Test Creek"
    assert ctx.extras["nrsa"] is None


def test_streamcat_row_drops_nulls_and_lowercases():
    row = records.streamcat_row({"streamcat": {"PctImp2019Ws": 8, "kffactws": None,
                                               "prg_bmmiws": "0.7"}})
    assert row == {"pctimp2019ws": 8.0, "prg_bmmiws": 0.7}


def test_providers_answer_only_for_the_registered_point(clean_providers, monkeypatch):
    calls = []
    monkeypatch.setattr(attains, "impairment_at_point", lambda lat, lon, **k: calls.append(("at", lat, lon)) or {"live": True})
    monkeypatch.setattr(attains, "impairment_near_point", lambda lat, lon, **k: calls.append(("near", lat, lon)) or {"live": True})
    monkeypatch.setattr(wqp, "sample_summary", lambda p, lat, lon, **k: calls.append(("wqp", p)) or {"live": True})
    monkeypatch.setattr(nid_barriers, "barriers_near", lambda lat, lon, **k: calls.append(("nid", lat, lon)) or [{"live": True}])
    monkeypatch.setattr(nas, "established_taxa", lambda huc12=None, huc8=None, **k: calls.append(("nas", huc12, huc8)) or ["live"])
    stored = _record(attains_exact={"assessment_unit": "AU1", "ircategory": "5"},
                     attains_nearby={"assessment_unit": "AU2", "ircategory": "4A"},
                     wqp_tn={"parameter": "tn", "value": 1.5},
                     nid_dams=[{"name": "Dam", "distance_m": 10.0}],
                     nas_taxa=["Corbicula fluminea"])
    with providers.preloaded(stored):
        assert providers.active_points() == 1
        assert attains.impairment_at_point(LAT, LON) == {"assessment_unit": "AU1", "ircategory": "5"}
        assert attains.impairment_near_point(LAT, LON)["ircategory"] == "4A"
        assert wqp.sample_summary("tn", LAT, LON)["value"] == 1.5
        assert wqp.sample_summary("tp", LAT, LON) is None       # stored None: unavailable
        assert wqp.sample_summary("temp", LAT, LON) is None
        assert nid_barriers.barriers_near(LAT, LON, miles=1.0) == [{"name": "Dam", "distance_m": 10.0}]
        assert nas.established_taxa(huc12="010203040506", huc8="01020304") == ["Corbicula fluminea"]
        # a different point and a different HUC still reach the live functions
        assert attains.impairment_at_point(41.0, -84.0) == {"live": True}
        assert nas.established_taxa(huc12="999999999999", huc8="99999999") == ["live"]
        # a stored answer is a copy: mutating it never leaks into the registry
        attains.impairment_at_point(LAT, LON)["mutated"] = True
        assert "mutated" not in attains.impairment_at_point(LAT, LON)
    assert providers.active_points() == 0
    assert attains.impairment_at_point(LAT, LON) == {"live": True}
    assert calls[0] == ("at", 41.0, -84.0)


def test_nested_preloads_keep_a_refcount(clean_providers):
    rec = _record()
    with providers.preloaded(rec):
        with providers.preloaded(rec):
            assert providers.active_points() == 1
        assert providers.active_points() == 1
    assert providers.active_points() == 0


def test_method_version_is_a_stable_short_digest():
    v = method_version()
    assert len(v) == 12 and v == method_version()


def test_assess_preloaded_never_touches_the_network(clean_providers, monkeypatch):
    # any of the prefetch sources being called means the seam leaked
    for module, name in ((assessment.streamcat, "metrics_by_comid"),
                         (assessment.wbd, "huc12_at_point"),
                         (assessment.threedep, "reach_geomorphology"),
                         (assessment.nrsa, "evidence_for_reach"),
                         (assessment.nlcd, "watershed_landcover")):
        def _boom(*a, _name=name, **k):
            raise AssertionError(f"{_name} was called")
        monkeypatch.setattr(module, name, _boom)
    report = client.score_record(_record(), cross_section=False)
    assert report["computedCount"] >= 12
