"""Regional identities are shared by live and stored-evidence assessments."""
from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import math
from pathlib import Path

import pytest

from easi import assessment, config, geo
from easi.metrics import physicochemistry
from easi.national import SCHEMA_VERSION, client, records
from test_batch_parity import _ctx, _stub
from test_national_preloaded import _record


@pytest.mark.parametrize("slope, expected", [
    (0.0, "lt_0.5"), (0.004999, "lt_0.5"), (0.005, "0.5_to_2"),
    (0.019999, "0.5_to_2"), (0.02, "ge_2"), (0.1, "ge_2"),
    (None, None), (-0.001, None), (math.nan, None), (math.inf, None),
    (-math.inf, None), ("missing", None),
])
def test_slope_class_boundary_semantics(slope, expected):
    assert geo.slope_class(slope) == expected


def test_crosswalk_covers_the_bundled_polygons():
    crosswalk = geo.ecoregion_crosswalk()
    features = json.loads(geo.ECOREGIONS_PATH.read_text(encoding="utf-8"))["features"]
    expected = {str(feature["properties"]["US_L3CODE"]) for feature in features}
    assert expected <= set(crosswalk["l3"])
    assert len(crosswalk["l3"]) == 85 and len(crosswalk["l2"]) == 20
    assert geo.strata_for("55", slope=0.005) == {
        "l3": "55", "l2": "8.2", "l1": "8", "l2_name": "CENTRAL USA PLAINS",
        "slope_class": "0.5_to_2"}
    assert geo.strata_for(45.0, slope=0.02)["l2"] == "8.3"
    unknown = geo.strata_for("999", slope=None)
    assert unknown["l3"] == "999" and unknown["l2"] is None and unknown["l1"] is None
    assert geo.strata_for(None, slope=None)["l3"] is None


def _generator():
    path = Path(__file__).resolve().parents[1] / "scripts/build_ecoregion_crosswalk.py"
    spec = importlib.util.spec_from_file_location("build_ecoregion_crosswalk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _crosswalk_inputs(tmp_path, rows):
    source = tmp_path / "crosswalk.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["l3", "l2", "l1", "l3_name", "l2_name"])
        writer.writeheader()
        writer.writerows(rows)
    polygons = tmp_path / "regions.geojson"
    polygons.write_text(json.dumps({"features": [
        {"properties": {"US_L3CODE": code}} for code in (45, 55)]}), encoding="utf-8")
    return source, polygons


def test_crosswalk_generator_is_deterministic_and_rejects_missing_codes(tmp_path):
    generator = _generator()
    rows = [
        {"l3": "45", "l2": "8.3", "l1": "8", "l3_name": "Piedmont", "l2_name": "SOUTHEASTERN USA PLAINS"},
        {"l3": "55", "l2": "8.2", "l1": "8", "l3_name": "Eastern Corn Belt Plains", "l2_name": "CENTRAL USA PLAINS"},
    ]
    source, polygons = _crosswalk_inputs(tmp_path, rows)
    output = tmp_path / "result.json"
    argv = ["--source", str(source), "--ecoregions", str(polygons), "--out", str(output)]
    assert generator.main(argv) == 0
    first = output.read_bytes()
    _crosswalk_inputs(tmp_path, list(reversed(rows)))
    assert generator.main(argv) == 0 and output.read_bytes() == first
    _crosswalk_inputs(tmp_path, rows[:1])
    with pytest.raises(ValueError, match="55"):
        generator.main(argv)
    assert output.read_bytes() == first


@pytest.mark.parametrize("identity", [
    {"l3_code": "55", "nars9": "TPL"},
    {"l3_code": None, "nars9": None},
])
def test_stored_identity_avoids_polygon_lookups_including_explicit_nulls(monkeypatch, identity):
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(args)
        raise AssertionError("stored region identity must not trigger a polygon lookup")

    monkeypatch.setattr(geo, "level3_at", unexpected)
    monkeypatch.setattr(geo, "nars9_at", unexpected)
    record = _record(schema_version=2, **identity)
    report = client.score_record(record, cross_section=False)
    assert calls == []
    assert report["strata"]["l3"] == identity["l3_code"]
    assert report["strata"]["nars9"] == identity["nars9"]
    assert report["strata"]["slope_class"] == "0.5_to_2"
    assert report["precomputed"]["schema"] == SCHEMA_VERSION == 2


def test_schema_one_identity_absence_survives_roundtrip_and_falls_back_once(monkeypatch):
    calls = []
    monkeypatch.setattr(geo, "level3_at", lambda *args: calls.append("l3") or {"code": "55"})
    monkeypatch.setattr(geo, "nars9_at", lambda *args: calls.append("nars9") or {"code": "TPL"})
    old_record = _record(schema_version=1)
    old_record.pop("l3_code")
    old_record.pop("nars9")
    record = records.from_row(records.to_row(old_record))
    assert "l3_code" not in record and "nars9" not in record
    report = client.score_record(record, cross_section=False)
    assert calls == ["l3", "nars9"]
    assert report["strata"]["l2"] == "8.2" and report["strata"]["nars9"] == "TPL"


def test_only_the_missing_identity_uses_geographic_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(geo, "level3_at", lambda *args: calls.append("l3") or {"code": "55"})
    monkeypatch.setattr(geo, "nars9_at", lambda *args: calls.append("nars9") or {"code": "TPL"})
    old_record = _record(schema_version=1, l3_code=None)
    old_record.pop("nars9")
    context = records.build_context(old_record)
    assert calls == ["nars9"]
    assert context.extras["strata"]["l3"] is None
    assert context.extras["strata"]["nars9"] == "TPL"


@pytest.mark.parametrize("criteria_set", ["regional", "legacy"], indirect=True)
def test_live_and_preloaded_reports_share_strata_and_preserve_them(monkeypatch, criteria_set):
    _stub(monkeypatch)
    context = _ctx()
    live = asyncio.run(assessment.assess(context))
    record = _record(schema_version=2, l3_code=live["strata"]["l3"], nars9=live["strata"]["nars9"])
    stored = client.score_record(record, cross_section=False)
    assert stored["strata"] == live["strata"] == context.extras["strata"]
    assert stored["strata"]["l3"] == "55" and stored["strata"]["l2"] == "8.2"
    assert assessment.rescore(stored, {})["strata"] == stored["strata"]
    assert assessment.recompute_watershed_rows(stored, records.build_context(record))["strata"] == stored["strata"]


def test_nutrients_use_stored_nars_region_and_retain_its_name(monkeypatch):
    context = _ctx()
    context.extras["strata"] = {"nars9": "CPL"}
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *args: {"value": 0.1})
    calls = []
    monkeypatch.setattr(geo, "nars9_at", lambda *args: calls.append(args) or {"code": "TPL"})
    result = physicochemistry.nutrients(context)
    assert calls == [] and result.value["region"] == "CPL"
    assert result.detail["region"]["name"] == geo.nars9_name("CPL")
    assert result.detail["region"]["name"]


def test_method_version_hashes_the_crosswalk_and_geo_module(tmp_path, monkeypatch):
    from easi import national

    assert "geo.py" in national._METHOD_SOURCES
    assert "ecoregion-crosswalk.json" in national._METHOD_DATA
    source = config.DATA_DIR
    for name in (config.screening_methods_filename(), *national._METHOD_DATA):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    national.method_version.cache_clear()
    try:
        before = national.method_version()
        path = tmp_path / "ecoregion-crosswalk.json"
        path.write_bytes(path.read_bytes() + b"\n")
        national.method_version.cache_clear()
        assert national.method_version() != before
    finally:
        national.method_version.cache_clear()
