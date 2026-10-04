"""The live library is read once and reused until one of its files changes (2026-10-03).

With ``apps/library`` beside DEEP a Basin step looked the library up 171 times and took 72 s.
These pin that a lookup reads each file once, that a publish, a status change or a rewritten
bundle is seen by the very next lookup, and that ``covering_refs`` reads the registry once.
Every change below also changes a file's size, so none of it depends on the file clock's
resolution.
"""
from __future__ import annotations

import json

import pytest

from deep import assessments, config, library

POLY = {"type": "Polygon", "coordinates": [[[-72, 43], [-70, 43], [-70, 45], [-72, 45], [-72, 43]]]}


def _bundle(aid, version, name):
    region = {"kind": "ecoregion", "code": "58", "name": "Test", "polygon": POLY}
    return {"schemaVersion": 1, "tier": "detailed", "assessmentId": aid, "assessmentName": name,
            "region": region, "library": {"version": version, "region": region},
            "metricsByFunction": [{"functionId": "catchment-hydrology", "functionName": "CH",
                                   "discipline": "Hydrology",
                                   "metrics": [{"metricId": "m1",
                                                "curve": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}}]}]}


def _publish(root, aid, version, status="preliminary", name=None):
    """What a StreamCurves publish leaves behind: the version's bundle, then the manifest,
    the status history and the catalog rewritten."""
    adir = root / "assessments" / aid
    (adir / f"v{version}").mkdir(parents=True, exist_ok=True)
    (adir / f"v{version}" / "assessment.deep.json").write_text(
        json.dumps(_bundle(aid, version, name or f"{aid} v{version}")), encoding="utf-8")
    mpath, spath = adir / "manifest.json", adir / "status.json"
    manifest = json.loads(mpath.read_text("utf-8")) if mpath.is_file() else {"assessmentId": aid, "versions": []}
    manifest["versions"] = sorted({int(v["version"]) for v in manifest["versions"]} | {version})
    manifest["versions"] = [{"version": v} for v in manifest["versions"]]
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    status_doc = json.loads(spath.read_text("utf-8")) if spath.is_file() else {"assessmentId": aid, "history": []}
    status_doc["history"].append({"version": version, "status": status})
    spath.write_text(json.dumps(status_doc), encoding="utf-8")
    ids = sorted(p.name for p in (root / "assessments").iterdir() if p.is_dir())
    (root / "catalog.json").write_text(json.dumps({"assessments": [
        {"assessmentId": i, "latestVersion": max(int(v["version"]) for v in json.loads(
            (root / "assessments" / i / "manifest.json").read_text("utf-8"))["versions"])}
        for i in ids]}), encoding="utf-8")


def _set_status(root, aid, version, status):
    spath = root / "assessments" / aid / "status.json"
    doc = json.loads(spath.read_text("utf-8"))
    doc["history"].append({"version": version, "status": status})
    spath.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    library.clear_cache()
    yield root
    library.clear_cache()


@pytest.fixture
def reads(monkeypatch):
    """The files library reads, in order."""
    seen = []
    real = library._read_json

    def counting(path):
        seen.append(path.name)
        return real(path)
    monkeypatch.setattr(library, "_read_json", counting)
    return seen


def _refs(bundles):
    return sorted(b["assessmentRef"] for b in bundles)


def test_a_second_lookup_reads_nothing_again(libroot, reads):
    _publish(libroot, "a-test", 1)
    _publish(libroot, "b-test", 1)
    first = library.all_eligible_bundles()
    assert _refs(first) == ["a-test@v1", "b-test@v1"] and len(reads) == 7   # catalog + 2 x 3
    second = library.all_eligible_bundles()
    assert second == first and len(reads) == 7


def test_a_publish_is_seen_by_the_next_lookup(libroot):
    _publish(libroot, "a-test", 1)
    assert _refs(library.all_eligible_bundles()) == ["a-test@v1"]
    _publish(libroot, "a-test", 2)
    _publish(libroot, "c-test", 1)
    assert _refs(library.all_eligible_bundles()) == ["a-test@v1", "a-test@v2", "c-test@v1"]


def test_a_status_change_is_seen_by_the_next_lookup(libroot):
    _publish(libroot, "a-test", 1)
    _publish(libroot, "a-test", 2)
    assert _refs(library.all_eligible_bundles()) == ["a-test@v1", "a-test@v2"]
    _set_status(libroot, "a-test", 1, "retired")
    assert _refs(library.all_eligible_bundles()) == ["a-test@v2"]
    _set_status(libroot, "a-test", 2, "certified")
    assert library.all_eligible_bundles()[0]["lifecycle"] == "certified"


def test_a_bundle_rewritten_in_place_is_seen(libroot):
    _publish(libroot, "a-test", 1, name="before")
    assert library.all_eligible_bundles()[0]["assessmentName"] == "before"
    path = libroot / "assessments" / "a-test" / "v1" / "assessment.deep.json"
    path.write_text(json.dumps(_bundle("a-test", 1, "after the rewrite")), encoding="utf-8")
    assert library.all_eligible_bundles()[0]["assessmentName"] == "after the rewrite"


def test_a_file_that_was_missing_is_seen_when_it_arrives(libroot):
    _publish(libroot, "a-test", 1)
    (libroot / "assessments" / "a-test" / "status.json").unlink()
    assert library.all_eligible_bundles()[0]["lifecycle"] == "preliminary"   # no history: preliminary
    doc = {"assessmentId": "a-test", "history": [{"version": 1, "status": "retired"}]}
    (libroot / "assessments" / "a-test" / "status.json").write_text(json.dumps(doc), encoding="utf-8")
    assert library.all_eligible_bundles() == []


def test_another_library_root_is_read(libroot, tmp_path, monkeypatch):
    _publish(libroot, "a-test", 1)
    assert _refs(library.all_eligible_bundles()) == ["a-test@v1"]
    other = tmp_path / "other"
    (other / "assessments").mkdir(parents=True)
    _publish(other, "z-test", 3)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(other))
    assert _refs(library.all_eligible_bundles()) == ["z-test@v3"]


def test_each_lookup_gets_its_own_list_and_top_level_dicts(libroot):
    _publish(libroot, "a-test", 1)
    first = library.all_eligible_bundles()
    first[0]["assessmentName"] = "edited by a caller"
    first.clear()
    again = library.all_eligible_bundles()
    assert len(again) == 1 and again[0]["assessmentName"] == "a-test v1"


def test_an_absent_library_stays_empty_without_reading(tmp_path, monkeypatch, reads):
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(tmp_path / "absent"))
    library.clear_cache()
    assert library.all_eligible_bundles() == [] and library.all_eligible_bundles() == []
    assert reads == []


def test_clear_cache_reads_again(libroot, reads):
    _publish(libroot, "a-test", 1)
    library.all_eligible_bundles()
    n = len(reads)
    library.clear_cache()
    library.all_eligible_bundles()
    assert len(reads) == 2 * n


def test_covering_refs_reads_the_registry_once(libroot, monkeypatch):
    _publish(libroot, "nh-test", 1)
    _publish(libroot, "nh-test", 2, status="certified")
    calls = []
    real = config._registry_records

    def counting():
        calls.append(1)
        return real()
    monkeypatch.setattr(config, "_registry_records", counting)
    covering = assessments.covering_refs(44.0, -71.0, require_polygon=True)
    entry = next(c for c in covering if c["assessmentId"] == "nh-test")
    assert entry["defaultRef"] == "nh-test@v2" and entry["refs"] == ["nh-test@v2", "nh-test@v1"]
    assert len(calls) == 1
