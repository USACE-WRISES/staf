"""Evidence packages: identity from the data, a deterministic archive, verification."""
from __future__ import annotations

import json

import pyarrow as pa

from builder import evidence_export as ee


def _package(tmp_path, *, producer_time="2026-09-23T00:00:00Z"):
    pkg = ee.Package(tmp_path, "test-pkg")
    pkg.parquet(pa.table({"comid": [1, 2, 3], "v": [0.5, 1.5, None]}), "values.parquet")
    pkg.json({"a": 1}, "notes.json")
    return pkg.finish(title="t", roles=["development"], reproducibility="refittable",
                      producer={"created": producer_time})


def test_the_identity_is_the_data_and_the_description_not_who_exported_it(tmp_path):
    first = _package(tmp_path / "a")
    again = _package(tmp_path / "b", producer_time="2026-09-24T12:00:00Z")
    assert first["dataDigest"] == again["dataDigest"]
    assert first["packageDigest"] == again["packageDigest"]
    assert first["zip"] == again["zip"]
    got = ee.verify(tmp_path / "a" / "test-pkg")
    assert got["damaged"] == [] and got["dataDigestOk"] and got["packageDigest"] == first["packageDigest"]


def test_the_archive_is_deterministic_and_a_damaged_file_is_found(tmp_path):
    first = _package(tmp_path / "a")
    blob = (tmp_path / "a" / first["zip"]).read_bytes()
    second = _package(tmp_path / "a")
    assert (tmp_path / "a" / second["zip"]).read_bytes() == blob
    doc = json.loads((tmp_path / "a" / "test-pkg" / "evidence.json").read_text(encoding="utf-8"))
    assert doc["files"]["data/values.parquet"]["rows"] == 3
    (tmp_path / "a" / "test-pkg" / "data" / "notes.json").write_text("{}", encoding="utf-8")
    assert ee.verify(tmp_path / "a" / "test-pkg")["damaged"] == ["data/notes.json"]


def test_two_exports_at_other_times_are_the_same_bytes(tmp_path):
    first = _package(tmp_path / "a")
    again = _package(tmp_path / "b", producer_time="2026-09-24T12:00:00Z")
    assert (tmp_path / "a" / first["zip"]).read_bytes() == (tmp_path / "b" / again["zip"]).read_bytes()
    assert first["exportedAt"] != again["exportedAt"]
    doc = json.loads((tmp_path / "a" / "test-pkg" / "evidence.json").read_text(encoding="utf-8"))
    assert "producer" not in doc or "created" not in doc["producer"]


def test_a_dependency_must_resolve_in_the_export(tmp_path):
    pkg = ee.Package(tmp_path, "needs-other")
    pkg.json({"a": 1}, "notes.json")
    pkg.finish(title="t", roles=["development"], reproducibility="reviewable",
               dependsOn=[{"packageId": "test-pkg", "dataDigest": "sha256:" + "0" * 64}])
    assert ee.check_dependencies(tmp_path) == ["needs-other needs test-pkg sha256:000000000000"]
    real = _package(tmp_path)
    pkg = ee.Package(tmp_path, "needs-other")
    pkg.json({"a": 1}, "notes.json")
    pkg.finish(title="t", roles=["development"], reproducibility="reviewable",
               dependsOn=[{"packageId": "test-pkg", "dataDigest": real["dataDigest"]}])
    assert ee.check_dependencies(tmp_path) == []
