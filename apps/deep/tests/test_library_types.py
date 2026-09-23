"""A typed library: DEEP reads only DEEP assessments, in this code and in the DEEP of ea32716.

StreamCurves publishes EASI screening methods into the same library (``assessmentType:
"easi"``, a ``method.json`` payload, no ``assessment.deep.json``). DEEP skips them by type;
the reader the next deploy ships from ea32716 (a frozen copy in ``tests/legacy``) skips them
because they have no bundle, so neither ever offers an EASI method as a detailed assessment.
"""
from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest

from deep import library as dl

HERE = Path(__file__).resolve().parent


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def typed_library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    digest = "sha256:" + "a" * 64
    bundle = {"assessmentId": "test-region", "assessmentName": "Test region", "tier": "detailed",
              "schemaVersion": 1, "metricsByFunction": [], "contentDigest": digest}
    _write(root / "assessments/test-region/manifest.json", {
        "schemaVersion": 2, "assessmentId": "test-region", "assessmentName": "Test region",
        "region": {"kind": "ecoregion", "code": "99", "name": "Test"}, "latestVersion": 1,
        "versions": [{"version": 1, "contentDigest": digest}]})
    _write(root / "assessments/test-region/status.json", {
        "schemaVersion": 1, "assessmentId": "test-region",
        "history": [{"version": 1, "status": "preliminary"}]})
    _write(root / "assessments/test-region/v1/assessment.deep.json", bundle)
    _write(root / "assessments/easi-screening/manifest.json", {
        "schemaVersion": 2, "assessmentId": "easi-screening", "assessmentType": "easi",
        "assessmentName": "EASI screening method",
        "region": {"kind": "national", "code": "CONUS", "name": "Contiguous United States"},
        "latestVersion": 1, "versions": [{"version": 1, "contentDigest": "sha256:" + "b" * 64}]})
    _write(root / "assessments/easi-screening/status.json", {
        "schemaVersion": 1, "assessmentId": "easi-screening",
        "history": [{"version": 1, "status": "preliminary"}]})
    _write(root / "assessments/easi-screening/v1/method.json", {"schema": "staf-easi-method"})
    _write(root / "catalog.json", {"schemaVersion": 2, "assessments": [
        {"assessmentId": "easi-screening", "assessmentType": "easi", "latestVersion": 1,
         "defaultVersion": 1, "latestPreliminary": 1},
        {"assessmentId": "test-region", "latestVersion": 1, "defaultVersion": 1,
         "latestPreliminary": 1}]})
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def test_deep_reads_only_deep_assessments(typed_library, caplog):
    caplog.set_level(logging.WARNING, logger="deep")
    assert [b["assessmentId"] for b in dl.latest_bundles()] == ["test-region"]
    assert [b["assessmentRef"] for b in dl.all_eligible_bundles()] == ["test-region@v1"]
    assert set(dl.catalog_pointers()) == {"test-region"}
    # skipped by its type, never looked for as a missing bundle
    assert "easi-screening" not in caplog.text


def test_an_untyped_entry_is_a_deep_assessment():
    assert dl.is_deep({"assessmentId": "x"}) and dl.is_deep({"assessmentType": "deep"})
    assert not dl.is_deep({"assessmentType": "easi"})


def test_the_ea32716_reader_never_yields_an_easi_method(typed_library):
    spec = importlib.util.spec_from_file_location(
        "deep.legacy_library_ea32716", HERE / "legacy" / "library_ea32716.py")
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    assert [b["assessmentId"] for b in legacy.latest_bundles()] == ["test-region"]
    assert [b["assessmentRef"] for b in legacy.all_eligible_bundles()] == ["test-region@v1"]


def test_the_remote_feed_skips_a_typed_non_deep_entry():
    from deep import remote_library as rl
    doc = {"schema": 1, "assessments": [
        {"id": "easi-screening", "type": "easi", "versions": [
            {"version": 1, "status": "preliminary", "assets": {}}]},
        {"id": "test-region", "versions": [
            {"version": 1, "status": "draft", "assets": {}}]}]}
    cat = rl.parse_catalog(json.dumps(doc))
    assert {v.assessment_id for v in cat.versions} == {"test-region"}
