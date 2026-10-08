"""The release feed's evidence references.

A DEEP version whose folder carries ``evidence.json`` (the reference to its evidence package)
is listed with it as ``evidence`` in its ``library-v2.json`` record only; ``library.json``, the
frozen schema-1 feed, does not change by a byte apart from its build stamp. A reference a
client could not act on fails the build, and a catalog-only snapshot is refused as a release
source. Runs on a copy of the smallest DEEP assessment, never on ``apps/library``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

from streamcurves import gallery

APP_DIR = Path(__file__).resolve().parents[1]
REAL_LIBRARY = APP_DIR.parent / "library"
AID = "mi-sqt-adapted"          # two preliminary versions; v2 has a calculator, v1 none
EVIDENCE = {
    "packageId": "deep-dev-l3-99", "version": "mi-sqt-adapted-v2",
    "packageDigest": "sha256:" + "a" * 64, "dataDigest": "sha256:" + "b" * 64,
    "title": "Michigan development data", "roles": ["development"],
    "reproducibility": "refittable", "bytes": 12345,
    "archive": {"name": "deep-dev-l3-99-mi-sqt-adapted-v2-aaaaaaaa.evidence.zip",
                "sha256": "c" * 64, "bytes": 12345},
}


def _release():
    spec = importlib.util.spec_from_file_location("library_release_evidence_under_test",
                                                  APP_DIR / "scripts" / "library_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def one_assessment(tmp_path, monkeypatch):
    """A library holding only the smallest DEEP assessment, copied from the real one."""
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    shutil.copytree(REAL_LIBRARY / "assessments" / AID, root / "assessments" / AID)
    cat = json.loads((REAL_LIBRARY / "catalog.json").read_text(encoding="utf-8"))
    cat["assessments"] = [e for e in cat["assessments"] if e["assessmentId"] == AID]
    (root / "catalog.json").write_text(json.dumps(cat, indent=2), encoding="utf-8")
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data"))
    return root


def _unstamped(text: str) -> str:
    return re.sub(r'"generatedAt": "[^"]*"', '"generatedAt": ""', text)


def _v2_versions(folder: Path) -> dict[int, dict]:
    doc = json.loads((folder / gallery.CATALOG_NAME_V2).read_text(encoding="utf-8"))
    (entry,) = doc["assessments"]
    return {v["version"]: v for v in entry["versions"]}


def test_evidence_rides_in_the_typed_feed_only(one_assessment, tmp_path):
    lr = _release()
    lr.build(tmp_path / "a", commit="abc123")
    assert not any("evidence" in v for v in _v2_versions(tmp_path / "a").values())

    (one_assessment / "assessments" / AID / "v2" / lr.EVIDENCE_FILE).write_text(
        json.dumps(EVIDENCE), encoding="utf-8")
    lr.build(tmp_path / "b", commit="abc123")

    plain_a = (tmp_path / "a" / gallery.CATALOG_NAME).read_text(encoding="utf-8")
    plain_b = (tmp_path / "b" / gallery.CATALOG_NAME).read_text(encoding="utf-8")
    assert _unstamped(plain_a) == _unstamped(plain_b), "library.json never changes shape"
    assert "evidence" not in plain_b
    by_version = _v2_versions(tmp_path / "b")
    assert by_version[2]["evidence"] == EVIDENCE
    assert "evidence" not in by_version[1]
    assert {p.name for p in (tmp_path / "a").iterdir()} == \
        {p.name for p in (tmp_path / "b").iterdir()}, "a reference is never an asset"
    assert lr.all_names(tmp_path / "b") == lr.all_names(tmp_path / "a")

    typed = gallery.parse_catalog((tmp_path / "b" / gallery.CATALOG_NAME_V2)
                                  .read_text(encoding="utf-8"))
    assert typed[0].version(2).evidence == EVIDENCE and typed[0].version(1).evidence is None
    plain = gallery.parse_catalog(plain_b)
    assert plain[0].version(2).evidence is None, "the schema-1 reader never sees it"
    # and a typed catalog round-trips it
    again = gallery.catalog_doc(typed, source_commit="abc123", schema=gallery.CATALOG_SCHEMA_V2)
    assert next(v for v in again["assessments"][0]["versions"]
                if v["version"] == 2)["evidence"] == EVIDENCE


@pytest.mark.parametrize("bad, lacks", [
    ({"packageId": "deep-dev-l3-99"}, "version, packageDigest, dataDigest, archive"),
    ({**EVIDENCE, "archive": {"name": "x.evidence.zip"}}, "archive.sha256, archive.bytes"),
    ({**EVIDENCE, "archive": "x.evidence.zip"}, "archive (not an object)"),
    ([EVIDENCE], "not a JSON object"),
])
def test_a_reference_a_client_cannot_act_on_fails_the_build(one_assessment, tmp_path, bad,
                                                            lacks):
    lr = _release()
    (one_assessment / "assessments" / AID / "v1" / lr.EVIDENCE_FILE).write_text(
        json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit, match=re.escape(lacks)):
        lr.build(tmp_path / "out", commit="abc123")


def test_an_unreadable_reference_fails_the_build(one_assessment, tmp_path):
    lr = _release()
    (one_assessment / "assessments" / AID / "v1" / lr.EVIDENCE_FILE).write_text(
        "{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot be read"):
        lr.build(tmp_path / "out", commit="abc123")


def test_a_catalog_only_snapshot_is_refused_as_a_release_source(one_assessment, tmp_path):
    shutil.rmtree(one_assessment / "assessments" / AID / "v1")
    shutil.rmtree(one_assessment / "assessments" / AID / "v2")
    with pytest.raises(SystemExit, match="catalog-only snapshot"):
        _release().build(tmp_path / "out", commit="abc123")
    assert not (tmp_path / "out" / gallery.CATALOG_NAME).exists(), "nothing half-built"


def test_a_hidden_version_changes_both_catalogs_by_one_key_and_mints_no_asset(one_assessment,
                                                                               tmp_path):
    """Show in DEEP off (owner, 2026-10-08): both feeds list the version with
    ``visibleInDeep: false`` (DEEP reads library.json, so the schema-1 feed carries it too),
    nothing else in either catalog moves, and no pack or bundle is built again."""
    from streamcurves import library as lib
    lr = _release()
    lr.build(tmp_path / "a", commit="abc123")
    lib.set_version_visibility(AID, 1, False, "GM")
    lr.build(tmp_path / "b", commit="abc123")
    for name in (gallery.CATALOG_NAME, gallery.CATALOG_NAME_V2):
        before = json.loads(_unstamped((tmp_path / "a" / name).read_text(encoding="utf-8")))
        after = json.loads(_unstamped((tmp_path / "b" / name).read_text(encoding="utf-8")))
        rows = dict((v["version"], v) for v in after["assessments"][0]["versions"])
        assert rows[1].pop("visibleInDeep") is False and "visibleInDeep" not in rows[2]
        assert after == before, name
    assert lr.all_names(tmp_path / "b") == lr.all_names(tmp_path / "a")
    entry = gallery.parse_catalog((tmp_path / "b" / gallery.CATALOG_NAME).read_text(encoding="utf-8"))[0]
    assert not entry.version(1).in_deep and entry.version(2).in_deep
