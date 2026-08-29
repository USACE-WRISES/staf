"""Tests for scripts/bake_library_into_deep.py (v2: authoritative, all eligible versions).

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


def _bundle(aid, version, name):
    return {
        "schemaVersion": 1,
        "tier": "detailed",
        "assessmentId": aid,
        "assessmentName": name,
        "sourceCitation": "Library test",
        "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
        "library": {"libraryId": aid, "version": version, "updatedAt": "2026-07-07T00:00:00Z"},
        "metricsByFunction": [
            {"functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
             "discipline": "Hydrology",
             "metrics": [{"metricId": "m1", "metricName": "M1",
                          "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}]}
        ],
    }


def _write_library(root, aid, name, versions, statuses=None):
    """Write version bundles + manifest + status.json for one assessment.

    ``versions``: list of ints. ``statuses``: {version: status}; defaults to preliminary.
    """
    statuses = statuses or {}
    for v in versions:
        vdir = root / "assessments" / aid / f"v{v}"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "assessment.deep.json").write_text(json.dumps(_bundle(aid, v, name)),
                                                    encoding="utf-8")
    (root / "assessments" / aid / "manifest.json").write_text(
        json.dumps({"schemaVersion": 2, "assessmentId": aid, "assessmentName": name,
                    "latestVersion": max(versions),
                    "versions": [{"version": v} for v in versions]}),
        encoding="utf-8")
    history = [{"version": v, "status": statuses.get(v, "preliminary"),
                "actor": "t", "timestamp": "2026-07-07T00:00:00Z"} for v in versions]
    (root / "assessments" / aid / "status.json").write_text(
        json.dumps({"schemaVersion": 2, "assessmentId": aid, "history": history}),
        encoding="utf-8")


def _write_catalog(root, entries):
    (root / "catalog.json").write_text(
        json.dumps({"schemaVersion": 2, "generatedAt": None, "assessments": entries}),
        encoding="utf-8")


def _catalog_entry(aid, name, latest, default, cert=0, prelim=0):
    return {"assessmentId": aid, "assessmentName": name,
            "region": {"kind": "ecoregion", "code": "55", "name": "ECBP"},
            "latestVersion": latest, "defaultVersion": default,
            "latestCertified": cert, "latestPreliminary": prelim,
            "latestUpdatedAt": "2026-07-07T00:00:00Z"}


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def test_bake_writes_v2_registry_and_versioned_bundles(tmp_path, libroot):
    out = tmp_path / "data"
    _write_library(libroot, "eastern-corn-belt-plains", "ECBP Adapted", [1])
    _write_catalog(libroot, [_catalog_entry("eastern-corn-belt-plains", "ECBP Adapted",
                                            latest=1, default=1, prelim=1)])
    bake = _load_bake_module()
    result = bake.bake(out=out)

    assert result["libraryCount"] == 1
    assert result["records"] == 1
    assert result["assessments"] == ["eastern-corn-belt-plains"]

    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    assert doc["schemaVersion"] == 2
    assert "eastern-corn-belt-plains" in doc["libraryCatalog"]
    rec = doc["assessments"][0]
    assert rec["assessmentRef"] == "eastern-corn-belt-plains@v1"
    assert rec["lifecycle"] == "preliminary"
    # per-version bundle + default bundle both written
    assert (out / "bundles" / "eastern-corn-belt-plains@v1.deep.json").is_file()
    assert (out / "bundles" / "eastern-corn-belt-plains.deep.json").is_file()


def test_bake_bakes_all_eligible_versions_certified_default(tmp_path, libroot):
    out = tmp_path / "data"
    # v1 certified, v2 preliminary -> both baked; default is the certified v1.
    _write_library(libroot, "ecbp", "ECBP", [1, 2], statuses={1: "certified", 2: "preliminary"})
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=2, default=1,
                                            cert=1, prelim=2)])
    bake = _load_bake_module()
    result = bake.bake(out=out)
    assert result["records"] == 2

    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    refs = {r["assessmentRef"] for r in doc["assessments"]}
    assert refs == {"ecbp@v1", "ecbp@v2"}
    assert doc["libraryCatalog"]["ecbp"]["defaultVersion"] == 1
    # the default bundle mirrors the certified v1
    default_bundle = json.loads((out / "bundles" / "ecbp.deep.json").read_text("utf-8"))
    assert default_bundle["version"] == 1


def test_bake_excludes_retired_versions(tmp_path, libroot):
    out = tmp_path / "data"
    _write_library(libroot, "ecbp", "ECBP", [1, 2], statuses={1: "retired", 2: "preliminary"})
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=2, default=2, prelim=2)])
    bake = _load_bake_module()
    bake.bake(out=out)
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    refs = {r["assessmentRef"] for r in doc["assessments"]}
    assert refs == {"ecbp@v2"}  # retired v1 is not eligible


def test_bake_excludes_draft_versions(tmp_path, libroot):
    """A draft is automation output no human reviewed: never DEEP-eligible."""
    out = tmp_path / "data"
    _write_library(libroot, "ecbp", "ECBP", [1, 2], statuses={1: "draft", 2: "preliminary"})
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=2, default=2, prelim=2)])
    bake = _load_bake_module()
    bake.bake(out=out)
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    refs = {r["assessmentRef"] for r in doc["assessments"]}
    assert refs == {"ecbp@v2"}
    assert not (out / "bundles" / "ecbp@v1.deep.json").exists()


def test_an_all_draft_assessment_is_absent_from_the_bake(tmp_path, libroot):
    out = tmp_path / "data"
    _write_library(libroot, "ecbp", "ECBP", [1], statuses={1: "draft"})
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=1, default=1)])
    bake = _load_bake_module()
    result = bake.bake(out=out)
    assert result["records"] == 0
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    assert doc["assessments"] == []
    assert not (out / "bundles" / "ecbp.deep.json").exists()


def test_bake_is_idempotent(tmp_path, libroot):
    out = tmp_path / "data"
    _write_library(libroot, "ecbp", "ECBP", [1])
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=1, default=1, prelim=1)])
    bake = _load_bake_module()
    r1 = bake.bake(out=out)
    first = (out / "deep-assessments.json").read_text("utf-8")
    r2 = bake.bake(out=out)
    second = (out / "deep-assessments.json").read_text("utf-8")
    assert first == second  # byte-identical on re-run
    assert r1 == r2


def test_bake_drops_stale_bundle_files(tmp_path, libroot):
    out = tmp_path / "data"
    (out / "bundles").mkdir(parents=True)
    (out / "bundles" / "old-removed.deep.json").write_text("{}", encoding="utf-8")
    _write_library(libroot, "ecbp", "ECBP", [1])
    _write_catalog(libroot, [_catalog_entry("ecbp", "ECBP", latest=1, default=1, prelim=1)])
    bake = _load_bake_module()
    bake.bake(out=out)
    assert not (out / "bundles" / "old-removed.deep.json").is_file()
    assert (out / "bundles" / "ecbp@v1.deep.json").is_file()


def test_bake_empty_library_is_empty_registry(tmp_path, libroot):
    out = tmp_path / "data"
    _write_catalog(libroot, [])  # nothing published
    bake = _load_bake_module()
    result = bake.bake(out=out)
    assert result["libraryCount"] == 0
    doc = json.loads((out / "deep-assessments.json").read_text("utf-8"))
    assert doc["assessments"] == []
    assert doc["schemaVersion"] == 2
