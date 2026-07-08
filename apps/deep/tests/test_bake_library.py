"""Tests for scripts/bake_library_into_deep.py.

Everything runs against a tmp ``out`` and a monkeypatched STAF_LIBRARY_ROOT, so the real
apps/deep/data and apps/library are never touched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

DEEP_ROOT = Path(__file__).resolve().parents[1]


def _load_bake_module():
    path = DEEP_ROOT / "scripts" / "bake_library_into_deep.py"
    spec = importlib.util.spec_from_file_location("bake_library_into_deep", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_library_version(root, aid, version, name):
    vdir = root / "assessments" / aid / f"v{version}"
    vdir.mkdir(parents=True)
    bundle = {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": aid,
        "assessmentName": name,
        "sourceCitation": "Library test",
        "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
        "library": {"libraryId": aid, "version": version, "updatedAt": "2026-07-07T00:00:00Z"},
        "metricsByFunction": [
            {
                "functionId": "catchment-hydrology",
                "functionName": "Catchment hydrology",
                "discipline": "Hydrology",
                "metrics": [
                    {"metricId": "m1", "metricName": "M1",
                     "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}
                ],
            }
        ],
    }
    (vdir / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")


def _write_catalog(root, entries):
    (root / "catalog.json").write_text(
        json.dumps({"schemaVersion": 1, "generatedAt": None, "assessments": entries}),
        encoding="utf-8",
    )


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def _seed_registry(out, assessments):
    out.mkdir(parents=True, exist_ok=True)
    (out / "deep-assessments.json").write_text(
        json.dumps({"schemaVersion": 1, "tier": "detailed", "assessments": assessments}),
        encoding="utf-8",
    )


def test_bake_appends_library_and_writes_bundle(tmp_path, libroot):
    out = tmp_path / "data"
    _seed_registry(out, [{"assessmentId": "ak-sqt-adapted", "assessmentName": "AK",
                          "metricsByFunction": []}])
    _write_library_version(libroot, "eastern-corn-belt-plains", 2, "ECBP Adapted")
    _write_catalog(libroot, [
        {"assessmentId": "eastern-corn-belt-plains", "assessmentName": "ECBP Adapted",
         "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
         "latestVersion": 2, "latestUpdatedAt": "2026-07-07T00:00:00Z"}])

    bake = _load_bake_module()
    result = bake.bake(out=out)
    assert result["libraryCount"] == 1
    assert result["added"] == ["eastern-corn-belt-plains"]

    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    ids = [a["assessmentId"] for a in doc["assessments"]]
    assert ids == ["ak-sqt-adapted", "eastern-corn-belt-plains"]
    ecbp = next(a for a in doc["assessments"] if a["assessmentId"] == "eastern-corn-belt-plains")
    assert ecbp["library"]["version"] == 2
    assert (out / "bundles" / "eastern-corn-belt-plains.deep.json").is_file()


def test_bake_is_idempotent_and_updates_existing(tmp_path, libroot):
    out = tmp_path / "data"
    _seed_registry(out, [])
    _write_library_version(libroot, "eastern-corn-belt-plains", 1, "ECBP")
    _write_catalog(libroot, [
        {"assessmentId": "eastern-corn-belt-plains", "assessmentName": "ECBP",
         "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
         "latestVersion": 1, "latestUpdatedAt": "2026-07-07T00:00:00Z"}])

    bake = _load_bake_module()
    bake.bake(out=out)
    r2 = bake.bake(out=out)
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    assert len(doc["assessments"]) == 1  # not duplicated on re-run
    assert r2["updated"] == ["eastern-corn-belt-plains"]


def test_bake_no_library_leaves_registry_unchanged(tmp_path, libroot):
    out = tmp_path / "data"
    _seed_registry(out, [{"assessmentId": "ak-sqt-adapted", "assessmentName": "AK",
                          "metricsByFunction": []}])
    _write_catalog(libroot, [])  # nothing published
    bake = _load_bake_module()
    result = bake.bake(out=out)
    assert result["libraryCount"] == 0
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    assert [a["assessmentId"] for a in doc["assessments"]] == ["ak-sqt-adapted"]
