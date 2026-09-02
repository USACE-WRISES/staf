"""Engine-sourced builds (D9, 2026-09-02).

Under ``--predictor-source site-engine`` the STAF site engine recomputes the six
scored landscape metrics with an engine analog at every retained site, the
per-site cache never stores a failure, the honesty report names each failed
or incomplete site, the re-sourced list rides the manifest and the digest,
and the bundle stamps ``predictorSource`` only on the re-sourced curves.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from streamcurves import provenance as pv
from streamcurves import regional_agent as ra
from streamcurves import site_engine_source as ses
from streamcurves.deep_export import build_deep_assessment_bundle

SIX = ["damdensws", "pctcrop2019ws", "pcthbwet2019ws", "pctimp2019ws",
       "pctwdwet2019ws", "rddensws"]


def _rec(status="ok", reason=None, **over):
    m = {"imperviousPctWatershed": 12.3, "cropPctWatershed": 40.0,
         "hayPasturePctWatershed": 5.0, "woodyWetlandPctWatershed": 1.5,
         "herbWetlandPctWatershed": 0.25, "roadDensity": 1.2345,
         "damDensityPerSqkm": 0.0213, "soilKFactor": 0.28,
         "damStoragePerSqkm": 10.0, "runoffDepthMm": 400.0}
    m.update(over)
    return {"status": status, "reason": reason, "watershed": {"areaSqkm": 12.5},
            "metrics": {k: {"value": v} for k, v in m.items()}}


# --------------------------------------------------------------------------- #
# se_site_record / se_site_metrics
# --------------------------------------------------------------------------- #
def test_se_site_record_extracts_both_groups_from_an_ok_record():
    rec = ses.se_site_record(40.0, -83.0, compute=lambda lat, lon, cfg: _rec())
    assert rec["status"] == "ok" and rec["reason"] is None
    v = rec["values"]
    assert v["se_pctimpws"] == 12.3 and v["se_agws"] == 45.0
    assert v["pctimp2019ws"] == 12.3 and v["pctcrop2019ws"] == 40.0
    assert v["pctwdwet2019ws"] == 1.5 and v["pcthbwet2019ws"] == 0.25
    assert v["rddensws"] == 1.2345 and v["damdensws"] == 0.0213
    assert v["se_wsareasqkm"] == 12.5
    assert rec["missing"] == [] and rec["seconds"] >= 0.0


def test_se_site_record_reports_refused_and_failed_with_the_engine_reason():
    refused = ses.se_site_record(0, 0, compute=lambda *a: _rec("refused", "watershed exceeds the budget"))
    assert refused["status"] == "refused" and "budget" in refused["reason"]
    assert refused["values"] == {}
    failed = ses.se_site_record(0, 0, compute=lambda *a: _rec("failed", "engine error: boom"))
    assert failed["status"] == "failed" and "boom" in failed["reason"]

    def explode(*a):
        raise RuntimeError("kaput")
    crashed = ses.se_site_record(0, 0, compute=explode)
    assert crashed["status"] == "failed" and "kaput" in crashed["reason"]


def test_se_site_record_marks_an_ok_record_missing_a_scored_analog_incomplete():
    rec = ses.se_site_record(0, 0, compute=lambda *a: _rec(roadDensity=None))
    assert rec["status"] == "incomplete"
    assert rec["missing"] == ["rddensws", "se_rddensws"]
    assert "rddensws" in rec["reason"]
    # a missing predictor with every scored analog present stays ok
    rec = ses.se_site_record(0, 0, compute=lambda *a: _rec(soilKFactor=None))
    assert rec["status"] == "ok" and rec["missing"] == ["se_kffactws"]


def test_zero_is_a_value_not_a_gap():
    rec = ses.se_site_record(0, 0, compute=lambda *a: _rec(damDensityPerSqkm=0.0, roadDensity=0.0))
    assert rec["status"] == "ok"
    assert rec["values"]["damdensws"] == 0.0 and rec["values"]["rddensws"] == 0.0


def test_se_site_metrics_keeps_the_compile_view_contract(monkeypatch):
    monkeypatch.setattr(ses, "se_site_record",
                        lambda lat, lon, **kw: {"status": "ok", "values": {
                            **{c: 1.0 for c in ses.se_codes()}, "pctimp2019ws": 1.0}})
    out = ses.se_site_metrics(0, 0)
    assert set(out) == set(ses.se_codes())          # predictors only
    monkeypatch.setattr(ses, "se_site_record",
                        lambda lat, lon, **kw: {"status": "failed", "values": {}})
    assert ses.se_site_metrics(0, 0) == {}


# --------------------------------------------------------------------------- #
# enrich_site_engine: cache, progress, report
# --------------------------------------------------------------------------- #
ROWS = [{"site_id": "A", "lat": 41.0, "lon": -72.0},
        {"site_id": "B", "lat": 42.0, "lon": -73.0}]


def _compute_by_site(spec):
    """``spec``: lat -> record; counts calls."""
    calls = []

    def compute(lat, lon, cfg):
        calls.append(lat)
        return spec[lat]
    return compute, calls


def test_enrich_never_caches_a_failure_and_retries_it(tmp_path):
    cache = tmp_path / "site_engine_cache.json"
    compute, calls = _compute_by_site({41.0: _rec(), 42.0: _rec("refused", "too big")})
    values, report = ses.enrich_site_engine(ROWS, cache_path=str(cache), compute=compute)
    assert report["status"] == "partial"
    assert report["failed_sites"] == [{"site_id": "B", "status": "refused", "reason": "too big"}]
    assert report["n_ok"] == 1 and report["n_sites"] == 2 and report["n_cached"] == 0
    assert "B" in report["reason"]
    assert values["A"]["pctimp2019ws"] == 12.3 and values["B"] == {}
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["schemaVersion"] == ses.SE_CACHE_SCHEMA
    entries = cached["sites"]
    b = next(e for k, e in entries.items() if k.startswith("B|"))
    assert b["status"] == "refused" and b["attempts"] == 1 and "values" not in b

    compute2, calls2 = _compute_by_site({42.0: _rec()})
    values, report = ses.enrich_site_engine(ROWS, cache_path=str(cache), compute=compute2)
    assert calls2 == [42.0]                             # A came from the cache
    assert report["status"] == "ok" and report["failed_sites"] == []
    assert report["n_cached"] == 1 and report["n_ok"] == 2
    assert values["B"]["rddensws"] == 1.2345


def test_enrich_reports_incomplete_sites_and_missing_predictor_values(tmp_path):
    compute, _ = _compute_by_site({41.0: _rec(woodyWetlandPctWatershed=None),
                                   42.0: _rec(runoffDepthMm=None)})
    values, report = ses.enrich_site_engine(ROWS, cache_path=str(tmp_path / "c.json"),
                                            compute=compute)
    assert report["status"] == "partial"
    assert len(report["incomplete_sites"]) == 1
    inc = report["incomplete_sites"][0]
    assert inc["site_id"] == "A" and inc["missing"] == ["pctwdwet2019ws"]
    assert "pctwdwet2019ws" in inc["reason"]
    assert report["missing_predictor_values"] == {"se_runoffmm": ["B"]}
    assert values["A"] == {}                           # incomplete never feeds a curve
    assert values["B"]["se_runoffmm"] is None and values["B"]["pctimp2019ws"] == 12.3


def test_enrich_progress_receives_each_site_with_status_and_seconds(tmp_path):
    compute, _ = _compute_by_site({41.0: _rec(), 42.0: _rec("failed", "engine error: x")})
    seen = []
    ses.enrich_site_engine(ROWS, cache_path=str(tmp_path / "c.json"), compute=compute,
                           progress=lambda i, n, info: seen.append((i, n, info)))
    assert [(i, n, info["site_id"], info["status"], info["cached"]) for i, n, info in seen] == [
        (1, 2, "A", "ok", False), (2, 2, "B", "failed", False)]
    assert all(info["seconds"] >= 0 for _, _, info in seen)
    assert seen[1][2]["reason"] == "engine error: x"


def test_enrich_ignores_a_cache_from_another_engine_version_or_schema(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"schemaVersion": 1, "sites": {}}), encoding="utf-8")
    compute, calls = _compute_by_site({41.0: _rec(), 42.0: _rec()})
    _, report = ses.enrich_site_engine(ROWS, cache_path=str(cache), compute=compute)
    assert len(calls) == 2 and report["cache"]["ignored"]
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["schemaVersion"] == ses.SE_CACHE_SCHEMA
    cached["engine"]["version"] = "0.0.0"
    cache.write_text(json.dumps(cached), encoding="utf-8")
    compute, calls = _compute_by_site({41.0: _rec(), 42.0: _rec()})
    _, report = ses.enrich_site_engine(ROWS, cache_path=str(cache), compute=compute)
    assert len(calls) == 2 and "version" in report["cache"]["ignored"]


def test_enrich_report_names_the_config_and_the_requested_columns(tmp_path):
    compute, _ = _compute_by_site({41.0: _rec(), 42.0: _rec()})
    _, report = ses.enrich_site_engine(ROWS, cache_path=str(tmp_path / "c.json"), compute=compute)
    assert report["requested_metrics"] == SIX
    assert set(SIX) <= set(report["requested"]) and set(ses.se_codes()) <= set(report["requested"])
    assert report["config"]["maxReaches"] and report["config"]["maxHops"]
    assert report["resourced_metrics"] == []            # filled by run_evidence


# --------------------------------------------------------------------------- #
# re-sourcing the scored columns
# --------------------------------------------------------------------------- #
def _frame():
    return pd.DataFrame({
        "site_id": ["A", "B", "C"],
        "pctimp2019ws": [1.0, 2.0, 3.0], "pctcrop2019ws": [10.0, 20.0, 30.0],
        "pctwdwet2019ws": [0.1, 0.2, 0.3], "pcthbwet2019ws": [0.01, 0.02, 0.03],
        "rddensws": [1.1, 2.2, 3.3], "damdensws": [0.0, 0.05, 0.1],
        "bfiws": [40.0, 50.0, 60.0], "rdcrsws": [0.01, 0.02, 0.03],
    })


def test_resource_metric_columns_swaps_only_the_analog_columns_and_keeps_nan_on_failure():
    values = {"A": {"pctimp2019ws": 12.3, "pctcrop2019ws": 40.0, "pctwdwet2019ws": 1.5,
                    "pcthbwet2019ws": 0.25, "rddensws": 1.2345, "damdensws": 0.0213},
              "B": {}}
    out, resourced = ses.resource_metric_columns(_frame(), values)
    assert resourced == SIX
    assert out.loc[0, "pctimp2019ws"] == 12.3 and out.loc[0, "damdensws"] == 0.0213
    assert math.isnan(out.loc[1, "pctimp2019ws"]) and math.isnan(out.loc[2, "rddensws"])
    assert out["bfiws"].tolist() == [40.0, 50.0, 60.0]        # no analog, untouched
    assert out["rdcrsws"].tolist() == [0.01, 0.02, 0.03]


def test_resource_metric_columns_never_creates_a_column_streamcat_did_not_return():
    frame = _frame().drop(columns=["damdensws"])
    out, resourced = ses.resource_metric_columns(frame, {"A": {"damdensws": 0.1, "pctimp2019ws": 5.0}})
    assert "damdensws" not in out.columns and resourced == [c for c in SIX if c != "damdensws"]
    assert out.loc[0, "pctimp2019ws"] == 5.0


def test_annotate_resourced_metric_config_sets_value_source_and_a_plain_note():
    cfg = {"pctimp2019ws": {"notes": "Watershed impervious surface (%)."},
           "bfiws": {"notes": "Base-flow index."}}
    out = ses.annotate_resourced_metric_config(cfg, ["pctimp2019ws"])
    assert out["pctimp2019ws"]["value_source"] == ses.engine_source_label()
    assert out["pctimp2019ws"]["value_source"].startswith("site-engine v")
    note = out["pctimp2019ws"]["notes"]
    assert "STAF site engine" in note and "exact watershed" in note
    assert ";" not in note and "—" not in note
    assert "value_source" not in out["bfiws"] and out["bfiws"]["notes"] == "Base-flow index."
    assert "value_source" not in cfg["pctimp2019ws"]            # a copy, never in place


# --------------------------------------------------------------------------- #
# run_evidence end to end (offline)
# --------------------------------------------------------------------------- #
_SC_COLS = ["pctimp2019ws", "pctcrop2019ws", "pcthay2019ws", "pctwdwet2019ws",
            "pcthbwet2019ws", "rddensws", "damdensws", "bfiws", "rdcrsws",
            "kffactws", "damnrmstorws", "runoffws", "precip8110ws", "elevws"]


@pytest.fixture(scope="module")
def engine_evidence():
    def fake_streamcat(data, directions, **kw):
        out = data.copy()
        for i, c in enumerate(_SC_COLS):
            out[c] = np.linspace(1.0 + i, 5.0 + i, len(out))
        return out, {"source": "streamcat", "status": "ok", "n_columns": len(_SC_COLS),
                     "reason": None, "requested": _SC_COLS}

    real_record = ses.se_site_record

    def fake_record(lat, lon, **kw):
        return real_record(lat, lon, compute=lambda *a: _rec(
            imperviousPctWatershed=round(100.0 + float(lat), 3)))

    # The patch lives only while the evidence is computed, so later tests in
    # this module see the real se_site_record again.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ra, "enrich_streamcat", fake_streamcat)
        mp.setattr(ses, "se_site_record", fake_record)
        return ra.run_evidence("55", "Eastern Corn Belt Plains", do_screen=False,
                               diagnostics_enabled=False, nrsa_dataset_id="legacy-1819",
                               predictor_source="site-engine")


def test_run_evidence_resources_the_scored_landscape_columns(engine_evidence):
    ev = engine_evidence
    assert ev["resourced_metrics"] == SIX
    data = ev["data"]
    assert np.allclose(data["pctimp2019ws"].astype(float),
                       100.0 + data["lat"].astype(float), atol=1e-3)
    assert (data["rddensws"] == 1.2345).all() and (data["damdensws"] == 0.0213).all()
    assert data["bfiws"].tolist() != [1.2345] * len(data)     # untouched StreamCat column
    for col in SIX:
        assert ev["metric_config"][col]["value_source"].startswith("site-engine v")
    assert "value_source" not in ev["metric_config"]["bfiws"]
    assert ev["predictor_source"].startswith("mixed (site-engine v")
    rep = ev["source_reports"][1]
    assert rep["source"] == "site_engine" and rep["status"] == "ok"
    assert rep["resourced_metrics"] == SIX


def test_assemble_carries_the_provenance_inputs_and_the_manifest_records_them(engine_evidence):
    """assemble once dropped predictor_source, the screening engine pin, and
    the screening dict, so no published manifest ever recorded them."""
    result = ra.assemble(engine_evidence)
    assert result["predictor_source"] == engine_evidence["predictor_source"]
    assert result["predictor_source_flag"] == "site-engine"
    assert result["resourced_metrics"] == SIX
    assert result["screening"] is engine_evidence["screening"]
    assert result["screening_watershed_engine"] == engine_evidence["screening_watershed_engine"]
    manifest = pv.build_run_manifest(result, argv=[])
    ps = manifest["inputs"]["predictor_source"]
    assert ps["source"].startswith("mixed (site-engine v")
    assert ps["resourced_metrics"] == SIX
    assert ps["report"]["status"] == "ok"
    payload = pv.digest_payload_from_manifest(manifest)
    assert payload["predictor_source"]["resourced_metrics"] == SIX


def test_the_digest_changes_with_the_resourced_list():
    base = {"region": {"l3_code": "71", "name": "Interior Plateau"},
            "screening_method": "functional",
            "screening_counts": {"n_screened": 71, "n_retained": 33},
            "source_reports": [None, {"source": "site_engine", "status": "ok",
                                      "engine": {"id": "site-engine", "version": "0.2.2",
                                                 "vendorSha": "abc"}}],
            "predictor_source": "site-engine v0.2.2", "predictor_source_flag": "site-engine"}
    predictors_only = pv.build_run_manifest(dict(base), argv=[])
    resourced = pv.build_run_manifest({**base, "resourced_metrics": SIX}, argv=[])
    assert predictors_only["inputsDigest"] != resourced["inputsDigest"]
    assert "resourced_metrics" not in pv.digest_payload_from_manifest(predictors_only)["predictor_source"]
    streamcat = pv.build_run_manifest({"region": base["region"], "screening_method": "functional",
                                       "screening_counts": {}, "source_reports": [None],
                                       "resourced_metrics": []}, argv=[])
    assert "predictor_source" not in streamcat["inputs"]


# --------------------------------------------------------------------------- #
# bundle stamps
# --------------------------------------------------------------------------- #
def _two_metric_case():
    def row(mk):
        return {"metric": mk, "display_name": np.nan, "higher_is_better": True,
                "curve_status": "complete", "stratum": np.nan,
                "curve_points": pd.DataFrame({"point_order": [1, 2], "metric_value": [0, 1],
                                              "index_score": [0, 1]})}
    mapping = pd.DataFrame({"metric_key": ["pctimp2019ws", "bfiws"],
                            "discipline": ["Hydrology", "Hydrology"],
                            "function_label": ["Catchment hydrology", "Streamflow regime"],
                            "sort_order": [1, 2]})
    return {"pctimp2019ws": row("pctimp2019ws"), "bfiws": row("bfiws")}, mapping


def _metrics_by_id(bundle):
    return {m["metricId"]: m for b in bundle["metricsByFunction"] for m in b["metrics"]}


def test_bundle_stamps_only_the_resourced_metrics():
    rows, mapping = _two_metric_case()
    cfg = {"pctimp2019ws": {"value_source": "site-engine v0.2.2", "notes": "recomputed"},
           "bfiws": {"notes": "streamcat"}}
    b = build_deep_assessment_bundle(rows, mapping, cfg,
                                     {"predictorSource": "mixed (site-engine v0.2.2 + streamcat)"})
    assert b["predictorSource"] == "mixed (site-engine v0.2.2 + streamcat)"
    m = _metrics_by_id(b)
    assert m["spring-pctimp2019ws"]["predictorSource"] == "site-engine v0.2.2"
    assert "predictorSource" not in m["spring-bfiws"]      # streamcat by absence


def test_bundle_per_metric_stamp_is_absent_when_nothing_was_resourced():
    rows, mapping = _two_metric_case()
    b = build_deep_assessment_bundle(rows, mapping, {}, {"predictorSource": "site-engine v0.2.2"})
    assert b["predictorSource"] == "site-engine v0.2.2"
    assert all("predictorSource" not in m for m in _metrics_by_id(b).values())
    b = build_deep_assessment_bundle(rows, mapping, {"bfiws": {"value_source": "streamcat"}}, {})
    assert "predictorSource" not in b
    assert all("predictorSource" not in m for m in _metrics_by_id(b).values())


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def test_science_report_names_the_recomputed_metrics():
    from streamcurves.science_report import build_science_support_html
    ctx = {"session_meta": {"predictor_source": "mixed (site-engine v0.2.2 + streamcat)",
                            "resourced_metrics": ["pctimp2019ws", "rddensws"]}, "metrics": {}}
    html = build_science_support_html(ctx)
    assert "Recomputed by the STAF site engine:" in html
    assert "pctimp2019ws, rddensws" in html
    html = build_science_support_html({"session_meta": {"predictor_source": "streamcat"}, "metrics": {}})
    assert "Recomputed by" not in html


def test_review_packet_lists_engine_failures_and_recomputed_metrics():
    from streamcurves import review_packet as rp
    ok = {"source": "site_engine", "status": "ok", "n_columns": 13, "reason": None,
          "n_ok": 33, "n_sites": 33, "n_cached": 12, "failed_sites": [], "incomplete_sites": [],
          "resourced_metrics": SIX}
    lines = rp.source_report_lines(ok)
    text = "\n".join(lines)
    assert "site_engine: ok (13 columns)" in text
    assert "33 of 33 sites computed, 12 from the run's cache" in text
    assert "Scored metrics recomputed by the STAF site engine: " + ", ".join(SIX) in text
    bad = {"source": "site_engine", "status": "partial", "n_columns": 13, "reason": "2 site(s) failed",
           "n_ok": 31, "n_sites": 33, "n_cached": 0,
           "failed_sites": [{"site_id": "NRS18_NH_10016", "status": "refused", "reason": "too big"}],
           "incomplete_sites": [{"site_id": "NRS18_NH_10017", "missing": ["rddensws"],
                                 "reason": "engine record lacks rddensws"}],
           "resourced_metrics": []}
    text = "\n".join(rp.source_report_lines(bad))
    assert "NRS18_NH_10016 refused: too big" in text
    assert "NRS18_NH_10017 incomplete: engine record lacks rddensws" in text
    assert "recomputed by the STAF site engine: none" in text
    streamcat = {"source": "streamcat", "status": "ok", "n_columns": 11, "reason": None,
                 "cache": {"from_cache": True}}
    assert rp.source_report_lines(streamcat) == [
        "- streamcat: ok (11 columns), read from the run's cache"]
    for line in lines:
        assert "—" not in line


def test_workbook_round_trip_keeps_value_source():
    from streamcurves import workbook
    assert "value_source" in workbook._CURATED_EXTRA_TEXT_FIELDS


def test_engine_config_override_reaches_the_engine_and_the_report(tmp_path):
    """A training point 194 ft from its HR flowline failed the engine's 150 ft
    default on the first NEH engine stage (2026-09-02); the override is a
    recorded build input, never a silent default change."""
    seen = []

    def compute(lat, lon, cfg):
        seen.append(dict(cfg))
        return _rec()
    _, report = ses.enrich_site_engine(ROWS[:1], cache_path=str(tmp_path / "c.json"),
                                       compute=compute, config={"snapTolFt": 300.0})
    assert seen == [{"includeGeometry": False, "snapTolFt": 300.0}]
    assert report["config"]["snapTolFt"] == 300.0
    _, report = ses.enrich_site_engine(ROWS[:1], cache_path=str(tmp_path / "e.json"),
                                       compute=compute, config={"maxReaches": 8000})
    assert seen[-1]["maxReaches"] == 8000 and report["config"]["maxReaches"] == 8000
    assert report["config"]["maxHops"] == 200                # the engine default, recorded
    _, report = ses.enrich_site_engine(ROWS[:1], cache_path=str(tmp_path / "d.json"),
                                       compute=compute)
    assert report["config"]["snapTolFt"] == 150.0          # the engine default, recorded


def test_the_snap_tolerance_flag_reaches_run_evidence():
    from pathlib import Path as _P
    text = (_P(__file__).resolve().parents[1] / "scripts" / "run_region_batch.py").read_text(encoding="utf-8")
    assert 'add_argument("--engine-snap-tolerance-ft"' in text
    start = text.index("ra.run_evidence(")
    assert "engine_config=_engine_config(a)" in text[start:start + 900]
    ns = text.index("argparse.Namespace(")
    assert "engine_snap_tolerance_ft=a.engine_snap_tolerance_ft" in text[ns:ns + 900]
    assert "engine_max_reaches=a.engine_max_reaches" in text[ns:ns + 900]
    assert 'add_argument("--engine-max-reaches"' in text
    assert 'add_argument("--engine-max-hops"' in text
