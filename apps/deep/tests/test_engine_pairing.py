"""The train/serve pairing rule and the engine auto-pull gating.

Engine-computed values (exact watershed) must never score against curves
fitted on StreamCat predictors; they score only when the bundle's
``predictorSource`` records engine predictors. The rule lives at the scoring
layer (restored sessions recompute desktop values), the auto-pull gate keeps
adapters on the trained sources, and the bake passthrough preserves the
bundle stamp. Fully offline."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from deep import assessments, measure
from deep.curves import engine_pairing_advisory, metric_index, metric_warning
from deep.metrics import computed
from deep.metrics.base import AnalysisContext
from deep.models import MeasuredValue

ASC = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]
_SC_SPEC = {"curve": {"points": ASC}}
_ENGINE_SPEC = {"curve": {"points": ASC},
                "predictorSource": "site-engine v0.1.0"}


# --------------------------------------------------------------------------- #
# scoring-layer rule
# --------------------------------------------------------------------------- #
def test_engine_value_refused_against_streamcat_curve():
    mv = MeasuredValue("m", value=0.5, origin="desktop", engine=True)
    assert metric_index(mv, _SC_SPEC) is None
    warning = metric_warning(mv, _SC_SPEC)
    assert "reference only" in warning
    assert "StreamCat predictors" in warning


def test_engine_value_scores_against_engine_curve():
    mv = MeasuredValue("m", value=0.5, origin="desktop", engine=True)
    assert metric_index(mv, _ENGINE_SPEC) == 0.5
    assert engine_pairing_advisory(mv, _ENGINE_SPEC) is None


def test_field_and_plain_desktop_values_are_unaffected():
    for mv in (MeasuredValue("m", value=0.5),
               MeasuredValue("m", value=0.5, origin="desktop")):
        assert metric_index(mv, _SC_SPEC) == 0.5
        assert engine_pairing_advisory(mv, _SC_SPEC) is None


def test_edit_clears_the_engine_flag_on_load():
    # _on_measure_set stamps origin="field" on any user edit; the flag must not
    # survive the round-trip, so an edited value scores normally.
    d = MeasuredValue("m", value=0.5, origin="desktop", engine=True).to_dict()
    d["origin"] = "field"
    back = MeasuredValue.from_dict(d)
    assert back.engine is False
    assert metric_index(back, _SC_SPEC) == 0.5


# --------------------------------------------------------------------------- #
# bundle declaration
# --------------------------------------------------------------------------- #
def test_predictor_source_defaults_to_streamcat():
    assert assessments.predictor_source_of({}) == "streamcat"
    assert assessments.predictor_source_of({"predictorSource": None}) == "streamcat"
    loaded = types.SimpleNamespace(raw={})
    assert assessments.predictor_source_of(loaded) == "streamcat"


def test_predictor_source_reads_the_stamp():
    loaded = types.SimpleNamespace(raw={"predictorSource": "site-engine v0.1.0"})
    assert assessments.predictor_source_of(loaded) == "site-engine v0.1.0"


# --------------------------------------------------------------------------- #
# auto-pull gating (adapters keep the trained sources unless allowed)
# --------------------------------------------------------------------------- #
def _fake_engine(monkeypatch, record):
    calls = {"n": 0}

    def compute_site(lat, lon, config=None):
        calls["n"] += 1
        return record
    vendor = types.ModuleType("deep._vendor.site_engine")
    vendor.compute_site = compute_site
    vendor.ENGINE_VERSION = "0.1.0"
    monkeypatch.setitem(sys.modules, "deep._vendor.site_engine", vendor)
    monkeypatch.setattr(computed, "site_engine_available", lambda: True)
    return calls


def _engine_record():
    return {"status": "ok", "engineVersion": "0.1.0",
            "metrics": {"imperviousPctWatershed": {"value": 12.3},
                        "cropPctWatershed": {"value": 40.0},
                        "hayPasturePctWatershed": {"value": 5.0}}}


def test_adapters_prefer_engine_only_when_allowed(monkeypatch):
    calls = _fake_engine(monkeypatch, _engine_record())
    monkeypatch.setitem(
        computed.__dict__, "_streamcat", lambda ctx: {"pctimp2019ws": 7.0})

    blocked = AnalysisContext(lat=40.0, lon=-83.0)
    cv = computed._impervious(blocked)
    assert cv.value == 7.0 and cv.engine is False       # trained source kept
    assert calls["n"] == 0                              # engine never invoked

    allowed = AnalysisContext(lat=40.0, lon=-83.0)
    allowed.extras["allow_engine"] = True
    cv = computed._impervious(allowed)
    assert cv.value == 12.3 and cv.engine is True
    assert "Site engine v0.1.0" in cv.source


def test_compute_metrics_only_threads_the_assessment(monkeypatch):
    _fake_engine(monkeypatch, _engine_record())
    monkeypatch.setitem(
        computed.__dict__, "_streamcat", lambda ctx: {"pctimp2019ws": 7.0})
    ids = ["catchment-hydrology-impervious-cover"]
    ci = {"lat": 40.0, "lon": -83.0}

    engine_bundle = {"predictorSource": "site-engine v0.1.0"}
    out = measure.compute_metrics_only(ci, ids, assessment=engine_bundle)
    entry = out["catchment-hydrology-impervious-cover"]
    assert entry["engine"] is True and entry["value"] == 12.3

    out = measure.compute_metrics_only(ci, ids, assessment={})
    entry = out["catchment-hydrology-impervious-cover"]
    assert entry["engine"] is False and entry["value"] == 7.0

    out = measure.compute_metrics_only(ci, ids)          # legacy call shape
    assert out["catchment-hydrology-impervious-cover"]["engine"] is False


# --------------------------------------------------------------------------- #
# bake passthrough (tmp library, the test_bake_library fixture pattern)
# --------------------------------------------------------------------------- #
def test_bake_preserves_the_predictor_source(tmp_path, monkeypatch):
    import json

    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))

    aid = "engine-built-pilot"
    bundle = {
        "schemaVersion": 1, "tier": "detailed", "assessmentId": aid,
        "assessmentName": "Engine Pilot", "sourceCitation": "t",
        "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
        "library": {"libraryId": aid, "version": 1,
                    "updatedAt": "2026-08-29T00:00:00Z"},
        "predictorSource": "site-engine v0.1.0",
        "metricsByFunction": [
            {"functionId": "catchment-hydrology",
             "functionName": "Catchment hydrology", "discipline": "Hydrology",
             "metrics": [{"metricId": "m1", "metricName": "M1",
                          "predictorSource": "site-engine v0.1.0",
                          "curve": {"points": [{"x": 0, "y": 0},
                                               {"x": 1, "y": 1}]}}]}],
    }
    vdir = root / "assessments" / aid / "v1"
    vdir.mkdir(parents=True)
    (vdir / "assessment.deep.json").write_text(json.dumps(bundle), "utf-8")
    (root / "assessments" / aid / "manifest.json").write_text(json.dumps(
        {"schemaVersion": 2, "assessmentId": aid, "assessmentName": "Engine Pilot",
         "latestVersion": 1, "versions": [{"version": 1}]}), "utf-8")
    (root / "assessments" / aid / "status.json").write_text(json.dumps(
        {"schemaVersion": 2, "assessmentId": aid,
         "history": [{"version": 1, "status": "preliminary", "actor": "t",
                      "timestamp": "2026-08-29T00:00:00Z"}]}), "utf-8")
    (root / "catalog.json").write_text(json.dumps(
        {"schemaVersion": 2, "generatedAt": None, "assessments": [
            {"assessmentId": aid, "assessmentName": "Engine Pilot",
             "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
             "latestVersion": 1, "defaultVersion": 1, "latestCertified": 0,
             "latestPreliminary": 1,
             "latestUpdatedAt": "2026-08-29T00:00:00Z"}]}), "utf-8")

    script = (Path(__file__).resolve().parents[1] / "scripts"
              / "bake_library_into_deep.py")
    spec = importlib.util.spec_from_file_location("bake_script_pairing", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.bake(out=tmp_path / "data")

    baked = json.loads((tmp_path / "data" / "bundles"
                        / f"{aid}.deep.json").read_text("utf-8"))
    assert baked["predictorSource"] == "site-engine v0.1.0"
    m = baked["metricsByFunction"][0]["metrics"][0]
    assert m["predictorSource"] == "site-engine v0.1.0"
    # and the loader reads the stamp back for the pairing rule
    assert assessments.predictor_source_of(baked) == "site-engine v0.1.0"
