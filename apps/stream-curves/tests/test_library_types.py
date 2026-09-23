"""A typed library: EASI method versions beside DEEP assessments, and nothing DEEP-shaped changes.

An EASI version is stored as the method package EASI loads (checked the way EASI checks it),
never as a DEEP bundle. DEEP entries, the schema-1 feed ``library.json`` and every DEEP asset
stay exactly what they were; ``library-v2.json`` lists every type. The readers StreamCurves
Desktop 1.0.0 shipped (frozen copies in ``tests/legacy``) read the schema-1 feed and never see an
EASI method, and refuse an EASI pack with their own "update the app" message.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from streamcurves import carry_forward as cf
from streamcurves import gallery, library as lib, project_file as pf
from streamcurves.easi_method import edit, io as eio, register as reg

APP = Path(__file__).resolve().parents[1]
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"
REAL_LIBRARY = APP.parent / "library"
RELEASE_METHOD = "b2e3033116e3"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return mod


@pytest.fixture
def project():
    return eio.import_from_easi(VENDORED_DATA, imported_by="test", cases={"cases": []})


@pytest.fixture
def empty_library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


@pytest.fixture
def typed_library(tmp_path, monkeypatch, project):
    """A copy of the real library with the unchanged EASI method published beside it."""
    root = tmp_path / "library"
    shutil.copytree(REAL_LIBRARY, root)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    before = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    eio.publish(project, author="tester", revision_notes="the unchanged method")
    return root, before


def _release():
    return _load("library_release_under_test", APP / "scripts" / "library_release.py")


def test_an_easi_version_is_stored_as_the_package_easi_loads(project, empty_library):
    assert eio.publish(project, author="tester") == 1
    man = lib.read_manifest("easi-screening")
    assert man["assessmentType"] == "easi" and man["versions"][0]["methodVersion"] == RELEASE_METHOD
    cat = lib.read_catalog()["assessments"][0]
    assert cat["assessmentType"] == "easi" and cat["methodVersion"] == RELEASE_METHOD
    assert cat["contentDigest"] == project.package_digest
    assert cat["region"]["kind"] == "national"
    got = lib.easi_version_files("easi-screening", 1)
    assert got["files"] == project.files and got["project"]["register"] == project.register
    assert lib.easi_package_bytes("easi-screening", 1) == eio.export_zip(project, status="draft")[0]
    with pytest.raises(ValueError, match="not a DEEP assessment"):
        lib.load_version_bundle("easi-screening", 1)


def test_a_revision_is_the_next_version_and_an_unchanged_one_is_refused(project, empty_library):
    eio.publish(project, author="tester")
    with pytest.raises(ValueError, match="already holds exactly this method"):
        eio.publish(eio.fork(project, by="tester"), author="tester")
    reopened = eio.open_version("easi-screening", 1)
    assert reopened.files == project.files
    assert reopened.meta["lineage"]["origin"] == {**reopened.meta["lineage"]["origin"],
                                                  "kind": "library", "version": 1}
    draft = edit.set_band_edge(eio.fork(reopened, by="tester"), "road-density-inflow-pressure",
                               None, 0, 1.5, by="tester", reason="experimental test edit")
    draft = reg.confirm_selection(draft, "reach-inflow", by="tester", reason="reviewed",
                                  at="2026-09-23T00:00:00Z")
    assert eio.publish(draft, author="tester") == 2
    entry = lib.read_catalog()["assessments"][0]
    assert entry["latestVersion"] == 2 and entry["methodVersion"] != RELEASE_METHOD


def test_deep_entries_stay_exactly_as_they_were(typed_library):
    root, before = typed_library
    after = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    deep_after = [e for e in after["assessments"] if lib.entry_type(e) == "deep"]
    assert deep_after == before["assessments"]
    assert not any("assessmentType" in e for e in deep_after)
    easi = [e for e in after["assessments"] if lib.entry_type(e) == "easi"]
    assert [e["assessmentId"] for e in easi] == ["easi-screening"]


def test_the_deep_walkers_never_see_the_easi_method(typed_library):
    root, _ = typed_library
    man = json.loads((root / "assessments" / "easi-screening" / "manifest.json").read_text())
    man["region"] = {"kind": "ecoregion", "code": "58", "name": "mislabelled"}
    (root / "assessments" / "easi-screening" / "manifest.json").write_text(json.dumps(man))
    found = cf.find_published("58", root=root)
    assert found is not None and found[0] == "northeastern-highlands"


def _normalized(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("generatedAt", "source")}


def test_library_json_is_unchanged_by_an_easi_method_and_v2_lists_it(typed_library, tmp_path,
                                                                     monkeypatch):
    rel = _release()
    root, _ = typed_library
    typed_out = tmp_path / "typed"
    rel.build(typed_out)
    plain = tmp_path / "plain-library"
    shutil.copytree(REAL_LIBRARY, plain)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(plain))
    plain_out = tmp_path / "plain"
    rel.build(plain_out)
    v1_typed = json.loads((typed_out / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    v1_plain = json.loads((plain_out / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    assert _normalized(v1_typed) == _normalized(v1_plain)
    for name in rel.catalog_names(v1_plain):
        assert (typed_out / name).read_bytes() == (plain_out / name).read_bytes()
    v2 = json.loads((typed_out / gallery.CATALOG_NAME_V2).read_text(encoding="utf-8"))
    types = {a["id"]: a["type"] for a in v2["assessments"]}
    assert types["easi-screening"] == "easi" and types["northeastern-highlands"] == "deep"
    easi_v1 = next(a for a in v2["assessments"] if a["id"] == "easi-screening")["versions"][0]
    easi_assets = easi_v1["assets"]
    assert set(easi_assets) == {"pack", "method"} and "-p2-" in easi_assets["pack"]["name"]
    assert easi_v1["methodVersion"] == RELEASE_METHOD
    assert not any("methodVersion" in v for a in v2["assessments"] if a["type"] == "deep"
                   for v in a["versions"])
    keep = rel.all_names(typed_out)
    assert easi_assets["method"]["name"] in keep and easi_assets["pack"]["name"] in keep
    # the new reader lists the EASI method in its own group; DEEP never runs it
    entries = gallery.parse_catalog((typed_out / gallery.CATALOG_NAME_V2).read_text(encoding="utf-8"))
    easi = next(e for e in entries if e.id == "easi-screening")
    assert easi.type == "easi" and easi.group == "EASI screening methods"
    assert easi.versions[0].method_version == RELEASE_METHOD
    assert not any(v.in_deep for v in easi.versions)


def test_streamcurves_1_0_0_reads_the_feed_it_knows_and_never_an_easi_method(typed_library, tmp_path):
    rel = _release()
    out = tmp_path / "out"
    rel.build(out)
    legacy = _load("streamcurves.legacy_gallery_1_0_0", APP / "tests" / "legacy" / "gallery_1_0_0.py")
    entries = legacy.parse_catalog((out / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    ids = {e.id for e in entries}
    assert "easi-screening" not in ids and "northeastern-highlands" in ids
    with pytest.raises(ValueError, match="newer than this app reads"):
        legacy.parse_catalog((out / gallery.CATALOG_NAME_V2).read_text(encoding="utf-8"))
    legacy_pf = _load("streamcurves.legacy_project_file_1_0_0",
                      APP / "tests" / "legacy" / "project_file_1_0_0.py")
    v2 = json.loads((out / gallery.CATALOG_NAME_V2).read_text(encoding="utf-8"))
    pack_name = next(a for a in v2["assessments"] if a["id"] == "easi-screening")["versions"][0]["assets"]["pack"]["name"]
    with pytest.raises(legacy_pf.ProjectFileError, match="Update the app"):
        legacy_pf.read_project((out / pack_name).read_bytes())
    proj, back = eio.read_project((out / pack_name).read_bytes())
    assert proj.format_version == 2 and back.method_version() == RELEASE_METHOD


def test_a_deep_pack_stays_format_1(typed_library):
    entries = gallery.entries_from_library()
    deep = next(e for e in entries if e.id == "northeastern-highlands")
    blob = gallery.pack_bytes(deep, deep.versions[0])
    proj = pf.read_project(blob)
    assert proj.format_version == 1 and proj.assessment_type == "deep" and not proj.parts
    assert "-p1-" in gallery.pack_name(deep, deep.versions[0], "0" * 64)
