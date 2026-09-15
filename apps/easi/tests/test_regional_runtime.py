"""Runtime criteria routing, report provenance, and edited-section strata."""
from __future__ import annotations

import asyncio
import html
import json

import pytest

from easi import assessment, config, geomorph, watershed
from easi.metrics import geomorphology, registry
from easi.national import client
from test_batch_parity import _ctx, _stub
from test_national_client import _record, _write_dataset


@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_live_fetch_uses_the_correct_model_area_and_name(criteria_set, monkeypatch):
    _stub(monkeypatch)
    calls = []

    def streamcat(comid, names, aoi="watershed"):
        calls.append((list(names), aoi))
        return {"PRG_BMMI0809": 0.42} if aoi == "other" else {"PCTIMP2019WS": 8.0}

    monkeypatch.setattr(assessment.streamcat, "metrics_by_comid", streamcat)
    ctx = _ctx()
    asyncio.run(assessment.assess(ctx, metric_ids=[]))
    assert ctx.extras["streamcat"]["pctimp2019ws"] == 8.0
    if criteria_set == "regional":
        assert len(calls) == 2 and (["prg_bmmi0809"], "other") in calls
        assert all("prG_BMMI" not in names for names, _ in calls)
        assert ctx.extras["streamcat"]["prg_bmmi0809"] == 0.42
    else:
        assert calls == [(registry.STREAMCAT_NAMES, "watershed")]
        assert "prg_bmmi0809" not in ctx.extras["streamcat"]


def test_substrate_anchor_and_recompute_selection_follow_switch(monkeypatch):
    mid = geomorphology.SUBSTRATE_ID
    for selected, expected in [("regional", "watershed"), ("legacy", "surrogateComid"),
                               ("regional", "watershed")]:
        monkeypatch.setenv("EASI_CRITERIA_SET", selected)
        assert registry.metric_anchor(mid) == expected
        assert (mid in registry.watershed_metric_ids()) == (selected == "regional")
        rows = [{"metricId": mid, "name": "Bed composition", "rating": "Fair"}]
        assessment._annotate_anchors(rows, None, watershed_layer=watershed.from_engine({"metrics": {}}))
        assert rows[0]["anchor"] == expected


@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_recompute_runs_substrate_only_for_regional(criteria_set, monkeypatch):
    _stub(monkeypatch)
    ctx = _ctx()
    report = asyncio.run(assessment.assess(ctx))
    called = []
    mid = geomorphology.SUBSTRATE_ID
    row = next(row for row in report["metricRows"] if row["metricId"] == mid)
    assert row["scale"] == ("W" if criteria_set == "regional" else "R")
    original = registry.REGISTRY[mid]

    def substrate(ctx):
        called.append(mid)
        return original(ctx)

    monkeypatch.setitem(registry.REGISTRY, mid, substrate)
    assessment.recompute_watershed_rows(report, ctx)
    assert called == ([mid] if criteria_set == "regional" else [])


def test_active_metadata_preserves_the_legacy_definitions(monkeypatch):
    mids = ["low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity",
            geomorphology.SUBSTRATE_ID,
            "population-support-biological-integrity-ibi-community-condition"]
    legacy_info = {mid: dict(config.METRIC_REGISTRY[mid]) for mid in mids}
    legacy_copy = {mid: config.METRIC_DEFINITIONS[mid] for mid in mids}
    for selected in ["regional", "legacy", "regional"]:
        monkeypatch.setenv("EASI_CRITERIA_SET", selected)
        info = config.metric_registry()
        for mid in mids:
            assert config.METRIC_REGISTRY[mid] == legacy_info[mid]
            assert config.METRIC_DEFINITIONS[mid] == legacy_copy[mid]
            if selected == "legacy":
                assert info[mid] == legacy_info[mid]
                assert config.metric_definition(mid) == legacy_copy[mid]
            else:
                assert "nrsa" not in info[mid]["datasource"]
                assert config.metric_definition(mid) != legacy_copy[mid]
        assert info[geomorphology.SUBSTRATE_ID]["scale"] == ("W" if selected == "regional" else "R")
    import app
    definition = config.metric_definition(mids[-1])
    # This is the definition used by the actual report-card tooltip caller.
    tip = str(app._metric_card_tip({"metricId": mids[-1], "name": "Population support"}))
    assert definition in html.unescape(html.unescape(tip))



@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_definition_fallback_preserves_original_assessment_statements(criteria_set):
    changed = "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity"
    unchanged = next(mid for mid in config.METRIC_DEFINITIONS
                     if mid not in {changed, geomorphology.SUBSTRATE_ID,
                                    "population-support-biological-integrity-ibi-community-condition"})
    original = "The original assessment library statement."
    assert config.metric_definition(unchanged, fallback=original) == original
    assert config.metric_definition(unchanged, fallback="") == ""
    assert config.metric_definition(unchanged) == config.METRIC_DEFINITIONS[unchanged]
    if criteria_set == "legacy":
        assert config.metric_definition(changed, fallback=original) == original
        assert config.metric_definition(changed) == config.METRIC_DEFINITIONS[changed]
    else:
        definition = config.metric_definition(changed, fallback=original)
        assert definition.startswith("Unvalidated screening proxy")
        assert "modeled monthly flow variability" in definition
        assert definition == config.metric_definition(changed)


def test_edited_stages_carry_strata_into_all_geometry_traces(monkeypatch, criteria_set):
    monkeypatch.setattr(geomorph, "derive_from_stages", lambda *a, **k: {
        "entrenchment_ratio": 2.1, "bank_height_ratio": 1.4})
    strata = {"l2": "8.3", "slope_class": "lt_0.5"}
    rows = assessment.rate_metrics_from_stages(
        {"stations": [], "elevs": [], "fcode": 46006}, 1, 2, strata=strata)
    assert len(rows) == 4
    assert all(row["scoring"]["context"]["strata"] == strata for row in rows.values())


@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_manifest_criteria_survive_summary_and_recalled_report(tmp_path, monkeypatch, criteria_set):
    path = tmp_path / "ds"
    manifest = _write_dataset(path)
    monkeypatch.setattr(assessment, "assess_preloaded", lambda *a, **k: {})
    ds = client.Dataset(base=str(path))
    assert ds.summary()["criteria_set"] == "legacy"
    assert ds.summary()["criteria_current"] == (criteria_set == "legacy")
    pre = client.score_record(_record(), dataset=ds)["precomputed"]
    assert pre["criteria_set"] == "legacy" and pre["criteria_current"] == (criteria_set == "legacy")
    manifest["criteria_set"] = "regional"
    (path / client.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    ds = client.Dataset(base=str(path))
    assert ds.summary()["criteria_current"] == (criteria_set == "regional")
    assert client.criteria_status({}) == {"criteria_set": None, "criteria_current": None}


def test_mismatch_notice_is_in_the_report_header(criteria_set):
    import app
    rep = {"precomputed": {"criteria_set": "legacy", "criteria_current": False}}
    text = str(app._header_with_map({}, rep, None))
    assert "national map and dashboard use legacy criteria" in text
    assert "Opened reports use regional criteria" in text
    rep["precomputed"]["criteria_current"] = True
    assert "Opened reports" not in str(app._header_with_map({}, rep, None))
