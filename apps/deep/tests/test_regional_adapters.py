"""The regional landscape adapters: the ``spring-*ws`` ids the regional bundles
score.

One batched StreamCat request per site, the site engine -> StreamCat -> NLCD
layering under the auto-pull gate, the two StreamCat-only ids (base flow index
and road-stream crossings have no engine analog), the anchor label on a routed
site, the end-to-end ``compute_metrics_only`` shape, and the pairing rule on a
mixed bundle. Fully offline."""
from __future__ import annotations

import json
import re
import sys
import types

import pytest

from deep import config, curves, measure
from deep.metrics import computed
from deep.metrics.base import AnalysisContext

EIGHT = ["spring-pctimp2019ws", "spring-pctcrop2019ws", "spring-pctwdwet2019ws",
         "spring-pcthbwet2019ws", "spring-rddensws", "spring-damdensws",
         "spring-bfiws", "spring-rdcrsws"]
ENGINE_SIX = EIGHT[:6]
STREAMCAT_ONLY = EIGHT[6:]

# A real StreamCat row (COMID 5214461, 2026-09-02). The zeros are real values.
SC = {"pctimp2019ws": 2.91, "pctcrop2019ws": 75.32, "pcthay2019ws": 8.3,
      "pctwdwet2019ws": 0.07, "pcthbwet2019ws": 0.0, "rddensws": 2.0611,
      "damdensws": 0.0, "bfiws": 21.4496, "rdcrsws": 0.0058}
SC_VALUES = {"spring-pctimp2019ws": 2.91, "spring-pctcrop2019ws": 75.32,
             "spring-pctwdwet2019ws": 0.07, "spring-pcthbwet2019ws": 0.0,
             "spring-rddensws": 2.0611, "spring-damdensws": 0.0,
             # bfi keeps two decimals, the densities and crossings four
             "spring-bfiws": 21.45, "spring-rdcrsws": 0.0058}
ENGINE_VALUES = {"spring-pctimp2019ws": 12.3, "spring-pctcrop2019ws": 40.0,
                 "spring-pctwdwet2019ws": 1.5, "spring-pcthbwet2019ws": 0.0,
                 "spring-rddensws": 1.2345, "spring-damdensws": 0.0}
ASC = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]
ENGINE_STAMP = "site-engine v0.2.2"
MIXED_STAMP = "mixed (site-engine v0.2.2 + streamcat)"


def _fake_engine(monkeypatch, record):
    calls = {"n": 0}

    def compute_site(lat, lon, config=None):
        calls["n"] += 1
        return record
    vendor = types.ModuleType("deep._vendor.site_engine")
    vendor.compute_site = compute_site
    vendor.ENGINE_VERSION = "0.2.2"
    monkeypatch.setitem(sys.modules, "deep._vendor.site_engine", vendor)
    monkeypatch.setattr(computed, "site_engine_available", lambda: True)
    return calls


def _engine_record(**extra):
    metrics = {"imperviousPctWatershed": {"value": 12.3},
               "cropPctWatershed": {"value": 40.0},
               "hayPasturePctWatershed": {"value": 5.0},
               "woodyWetlandPctWatershed": {"value": 1.5},
               "herbWetlandPctWatershed": {"value": 0.0},
               "roadDensity": {"value": 1.2345},
               "damDensityPerSqkm": {"value": 0.0}}
    metrics.update(extra)
    return {"status": "ok", "engineVersion": "0.2.2", "metrics": metrics}


def _stub_datasource(monkeypatch, name, **attrs):
    """Stand a stub in for ``deep.datasources.<name>`` on both import paths
    (the sys.modules entry and the package attribute a real import binds)."""
    import deep.datasources as pkg
    stub = types.SimpleNamespace(**attrs)
    monkeypatch.setitem(sys.modules, f"deep.datasources.{name}", stub)
    monkeypatch.setattr(pkg, name, stub, raising=False)
    return stub


def _ctx(allow_engine=False, **extras):
    ctx = AnalysisContext(lat=40.31125, lon=-83.05615, comid=5214461)
    if allow_engine:
        ctx.extras["allow_engine"] = True
    ctx.extras.update(extras)
    return ctx


def _sc_patch(monkeypatch, row=SC):
    monkeypatch.setitem(computed.__dict__, "_streamcat", lambda ctx: dict(row))


# --------------------------------------------------------------------------- #
# (a) one batched request
# --------------------------------------------------------------------------- #
def test_one_batched_streamcat_request_carries_every_name(monkeypatch):
    monkeypatch.setattr(computed, "site_engine_available", lambda: False)
    calls = []

    def fake(comid, names, aoi="watershed", timeout=25.0):
        calls.append((comid, list(names)))
        return dict(SC)
    _stub_datasource(monkeypatch, "streamcat", metrics_by_comid=fake)
    out = computed.compute_for(EIGHT, _ctx())
    assert len(calls) == 1
    assert calls[0] == (5214461, list(computed._STREAMCAT_NAMES))
    assert set(computed._STREAMCAT_NAMES) == {
        "pctimp2019", "pctcrop2019", "pcthay2019", "pctwdwet2019", "pcthbwet2019",
        "rddens", "damdens", "bfi", "rdcrs"}
    assert set(out) == set(EIGHT)


# --------------------------------------------------------------------------- #
# (b) the gate: StreamCat answers all eight closed, the engine six open
# --------------------------------------------------------------------------- #
def test_streamcat_answers_all_eight_when_the_gate_is_closed(monkeypatch):
    calls = _fake_engine(monkeypatch, _engine_record())
    _sc_patch(monkeypatch)
    out = computed.compute_for(EIGHT, _ctx())
    assert set(out) == set(EIGHT)
    for mid, want in SC_VALUES.items():
        cv = out[mid]
        assert cv.value == want, mid                    # zeros preserved
        assert cv.engine is False and cv.basis == "streamcat", mid
        assert cv.source.startswith("StreamCat lookup engine "), mid
    assert calls["n"] == 0                              # engine never invoked


def test_engine_answers_six_when_the_gate_is_open(monkeypatch):
    calls = _fake_engine(monkeypatch, _engine_record())
    _sc_patch(monkeypatch)
    out = computed.compute_for(EIGHT, _ctx(allow_engine=True))
    assert set(out) == set(EIGHT)
    for mid, want in ENGINE_VALUES.items():
        cv = out[mid]
        assert cv.value == want, mid                    # engine zeros preserved
        assert cv.engine is True and cv.basis == "site-engine", mid
        assert "STAF site engine v" in cv.source, mid
    for mid in STREAMCAT_ONLY:
        cv = out[mid]
        assert cv.value == SC_VALUES[mid] and cv.engine is False, mid
        assert cv.basis == "streamcat", mid
    assert calls["n"] == 1                              # one run, cached on ctx


# --------------------------------------------------------------------------- #
# (c) (d) the StreamCat-only ids
# --------------------------------------------------------------------------- #
def test_bfi_and_crossings_never_come_from_the_engine(monkeypatch):
    bogus = _engine_record(bfiws={"value": 99.0}, rdcrsws={"value": 99.0},
                           baseFlowIndex={"value": 99.0},
                           roadCrossings={"value": 99.0})
    _fake_engine(monkeypatch, bogus)
    _sc_patch(monkeypatch)
    out = computed.compute_for(STREAMCAT_ONLY, _ctx(allow_engine=True))
    assert out["spring-bfiws"].value == 21.45
    assert out["spring-rdcrsws"].value == 0.0058
    assert all(cv.engine is False and cv.basis == "streamcat" for cv in out.values())


def test_crossings_keep_the_served_units_and_carry_the_caution(monkeypatch):
    _sc_patch(monkeypatch)
    cv = computed._road_crossings(_ctx())
    assert cv.value == 0.0058                           # four decimals, not zeroed
    assert cv.source.startswith("StreamCat lookup engine rdcrs (watershed)")
    assert "as served" in cv.source and "100 times" in cv.source
    assert "—" not in cv.source and ";" not in cv.source
    assert cv.confidence == "M"


# --------------------------------------------------------------------------- #
# (e) (f) the anchor: labeled on a routed site, NLCD when routing is declined
# --------------------------------------------------------------------------- #
def test_anchor_labels_the_density_on_a_routed_site(monkeypatch):
    _sc_patch(monkeypatch)
    hr_only = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
               "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8, "declined": False}}
    cv = computed._road_density(_ctx(site_anchor=hr_only))
    assert cv.value == 2.0611 and cv.basis == "streamcat"
    assert cv.source.startswith(
        "StreamCat lookup engine rddens (watershed), describes the nearest covered reach")


def test_declined_routing_falls_back_to_nlcd_for_land_cover_only(monkeypatch):
    monkeypatch.setattr(computed, "site_engine_available", lambda: False)
    sc_calls = []
    _stub_datasource(monkeypatch, "streamcat",
                     metrics_by_comid=lambda *a, **k: sc_calls.append(a) or dict(SC))
    _stub_datasource(monkeypatch, "nlcd", watershed_landcover=lambda gj: {
        "impervious_pct": 3.3, "crop_pct": 61.0, "woody_wetland_pct": 0.4,
        "herb_wetland_pct": 0.0})
    declined = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
                "routing": {"declined": True, "daRatio": 14.0}}
    ctx = _ctx(site_anchor=declined, watershed_basis="site-engine")
    ctx.watershed_geojson = {"type": "FeatureCollection", "features": []}
    out = computed.compute_for(EIGHT, ctx)
    assert sc_calls == []                               # no COMID-keyed lookup at all
    assert set(out) == {"spring-pctimp2019ws", "spring-pctcrop2019ws",
                        "spring-pctwdwet2019ws", "spring-pcthbwet2019ws"}
    assert {cv.basis for cv in out.values()} == {"nlcd"}
    assert out["spring-pctimp2019ws"].value == 3.3
    assert out["spring-pctcrop2019ws"].value == 61.0
    assert out["spring-pctwdwet2019ws"].value == 0.4
    assert out["spring-pcthbwet2019ws"].value == 0.0    # a real zero survives
    assert all("exact watershed polygon, STAF site engine" in cv.source
               for cv in out.values())


def test_nlcd_fallback_keywords_match_the_engine_table():
    # Imported inside the test: a top-level import would bind the real module
    # before the sys.modules stubs in test_engine_pairing run.
    engine_lc = pytest.importorskip("deep._vendor.site_engine.metrics.landcover")
    from deep.datasources import nlcd
    table = engine_lc._CLASS_KEYWORDS
    assert nlcd.CROP_KEYWORDS == table["crop"]
    assert nlcd.WOODY_WETLAND_KEYWORDS == table["woodyWetland"]
    assert nlcd.HERB_WETLAND_KEYWORDS == table["herbWetland"]


# --------------------------------------------------------------------------- #
# (g) end to end through measure.compute_metrics_only
# --------------------------------------------------------------------------- #
def test_compute_metrics_only_end_to_end_with_a_prefetched_record(monkeypatch):
    calls = _fake_engine(monkeypatch, {"status": "failed"})
    _sc_patch(monkeypatch)
    ci = {"lat": 40.31125, "lon": -83.05615, "comid": 5214461}
    shape = {"value", "na", "note", "origin", "source", "engine", "basis"}

    out = measure.compute_metrics_only(
        ci, EIGHT, assessment={"predictorSource": ENGINE_STAMP},
        engine_record=_engine_record())
    assert set(out) == set(EIGHT)
    assert all(set(e) == shape for e in out.values())
    assert all(e["origin"] == "desktop" and e["na"] is False for e in out.values())
    assert {mid for mid, e in out.items() if e["engine"]} == set(ENGINE_SIX)
    assert {out[mid]["basis"] for mid in ENGINE_SIX} == {"site-engine"}
    assert {out[mid]["basis"] for mid in STREAMCAT_ONLY} == {"streamcat"}
    assert all("STAF site engine v0.2.2" in out[mid]["source"] for mid in ENGINE_SIX)
    assert all(out[mid]["value"] == ENGINE_VALUES[mid] for mid in ENGINE_SIX)
    assert all(out[mid]["value"] == SC_VALUES[mid] for mid in STREAMCAT_ONLY)
    assert calls["n"] == 0                              # prefetched record reused

    out = measure.compute_metrics_only(
        ci, EIGHT, assessment={"predictorSource": MIXED_STAMP},
        engine_record=_engine_record())
    assert {mid for mid, e in out.items() if e["engine"]} == set(ENGINE_SIX)

    out = measure.compute_metrics_only(ci, EIGHT, assessment={},
                                       engine_record=_engine_record())
    assert not any(e["engine"] for e in out.values())
    assert {e["basis"] for e in out.values()} == {"streamcat"}
    assert calls["n"] == 0


# --------------------------------------------------------------------------- #
# (h) the pairing rule reads the per-metric stamp on a mixed bundle
# --------------------------------------------------------------------------- #
def _mixed_bundle():
    return {
        "predictorSource": MIXED_STAMP,
        "metricsByFunction": [
            {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
             "discipline": "Hydrology",
             "metrics": [{"metricId": "spring-pctimp2019ws",
                          "metricName": "Impervious surface",
                          "predictorSource": ENGINE_STAMP,
                          "curve": {"points": ASC}}]},
            {"functionId": "streamflow-regime", "functionName": "Streamflow regime",
             "discipline": "Hydrology",
             "metrics": [{"metricId": "spring-bfiws", "metricName": "Base flow index",
                          "curve": {"points": ASC}}]}]}


def test_pairing_rule_on_a_mixed_bundle():
    bundle = _mixed_bundle()
    state = {"spring-pctimp2019ws": {"value": 0.5, "origin": "desktop",
                                     "engine": True, "basis": "site-engine"},
             "spring-bfiws": {"value": 0.5, "origin": "desktop",
                              "engine": False, "basis": "streamcat"}}
    _sc, fres = curves.score_site(bundle, measure.measured_from_state(state))
    assert fres["catchment-hydrology"].metric_indices["spring-pctimp2019ws"] == 0.5
    assert fres["streamflow-regime"].metric_indices["spring-bfiws"] == 0.5
    assert fres["streamflow-regime"].na is False

    state["spring-bfiws"]["engine"] = True              # engine value, unstamped curve
    _sc, fres = curves.score_site(bundle, measure.measured_from_state(state))
    assert fres["streamflow-regime"].metric_indices["spring-bfiws"] is None
    assert "reference only" in fres["streamflow-regime"].metric_warnings["spring-bfiws"]
    assert fres["catchment-hydrology"].metric_indices["spring-pctimp2019ws"] == 0.5
    mv = measure.measured_from_state(state)["spring-bfiws"]
    spec = bundle["metricsByFunction"][1]["metrics"][0]
    assert curves.metric_index(mv, spec) is None
    assert "reference only" in curves.metric_warning(mv, spec)


# --------------------------------------------------------------------------- #
# (i) every spring-*ws id the shipped bundles score has an adapter
# --------------------------------------------------------------------------- #
def test_every_regional_landscape_id_in_the_bundles_has_an_adapter():
    found = set()
    for path in (config.DATA_DIR / "bundles").glob("*.deep.json"):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        for fn in bundle.get("metricsByFunction") or []:
            for m in fn.get("metrics") or []:
                mid = str(m.get("metricId") or "")
                if re.fullmatch(r"spring-[a-z0-9]+ws", mid):
                    found.add(mid)
    assert found == set(EIGHT)
    assert found <= computed.computable_ids()
