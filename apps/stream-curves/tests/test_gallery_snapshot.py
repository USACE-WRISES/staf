"""The catalog-only library snapshot the apps payload ships.

An installed StreamCurves Desktop reads the gallery from the `library` release and downloads
the version it opens, so the payload's ``library\\`` shrinks to ``catalog.json`` and each
assessment's records (manifest, status, validation, artifacts): no version folder. The gallery
lists such a snapshot with every version marked download-only and refuses to build a pack from
it with a plain message; a full library behaves as before. The payload script strips exactly
the version folders (a plain ``v*`` would also drop ``validation.json``), and the payload check
refuses a staged version folder.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from streamcurves import gallery
from streamcurves import library as lib

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parents[1]
REAL_LIBRARY = APP_DIR.parent / "library"
DESKTOP_SCRIPTS = REPO_ROOT / "desktop" / "scripts"
#: What the snapshot keeps per assessment (the catalog rides beside the assessments folder).
RECORDS = ("manifest.json", "status.json", "validation.json", "artifacts.json")

needs_checkout = pytest.mark.skipif(not DESKTOP_SCRIPTS.is_dir(),
                                    reason="not a STAF checkout (no desktop/scripts)")


@pytest.fixture(autouse=True)
def _own_roots(tmp_path, monkeypatch):
    """A per-test data root (the gallery cache) and the real library unless a test says."""
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("STREAMCURVES_LIBRARY_BASE_URL", raising=False)
    monkeypatch.delenv("STREAMCURVES_GALLERY_SOURCE", raising=False)
    monkeypatch.delenv("STAF_LIBRARY_ROOT", raising=False)


def catalog_only_copy(dest: Path) -> Path:
    """The real library as the payload ships it: the catalog and each assessment's records."""
    (dest / "assessments").mkdir(parents=True)
    shutil.copyfile(REAL_LIBRARY / "catalog.json", dest / "catalog.json")
    for adir in sorted(p for p in (REAL_LIBRARY / "assessments").iterdir() if p.is_dir()):
        (dest / "assessments" / adir.name).mkdir()
        for name in RECORDS:
            if (adir / name).is_file():
                shutil.copyfile(adir / name, dest / "assessments" / adir.name / name)
    return dest


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    root = catalog_only_copy(tmp_path / "library")
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


# --------------------------------------------------------------------------- #
# The gallery over a catalog-only snapshot
# --------------------------------------------------------------------------- #
def test_a_catalog_only_snapshot_lists_every_assessment_as_download_only(snapshot):
    entries = gallery.entries_from_library()
    listed = {e["assessmentId"] for e in json.loads(
        (REAL_LIBRARY / "catalog.json").read_text(encoding="utf-8"))["assessments"]}
    assert {e.id for e in entries} == listed and len(entries) >= 16
    assert {e.type for e in entries} == {"deep", "easi"}, "the typed entry lists too"
    for e in entries:
        assert e.download_only, e.id
        assert e.latest_version >= 1 and e.version() is not None
        for v in e.versions:
            assert v.download_only and v.assets == {}, (e.id, v.version)
            assert v.metrics is None and v.functions_covered is None, "no bundle to count"
            assert v.content_digest, "the manifest still records the digest"
            assert v.status in lib.VERSION_STATUSES and v.validation in lib.VALIDATION_STATES
    easi = next(e for e in entries if e.type == "easi")
    assert easi.version().method_version, "the manifest row carries the method version"
    assert gallery.snapshot_entries() == entries, "offline, the snapshot lists them all"
    assert gallery.load_catalog() == (entries, "snapshot")


def test_a_catalog_only_snapshot_opens_nothing(snapshot):
    n = 0
    for e in gallery.entries_from_library():
        for v in e.versions:
            with pytest.raises(gallery.GalleryError, match="not on this computer"):
                gallery.pack_bytes(e, v)
            n += 1
    assert n >= 20
    # the catalogs a builder would write never list a download-only version's assets
    doc = gallery.catalog_doc(gallery.entries_from_library(), source_commit="abc")
    assert all(v["assets"] == {} for a in doc["assessments"] for v in a["versions"])


def test_a_version_whose_folder_is_gone_lists_beside_the_others(snapshot):
    """Records are per assessment, so one missing folder marks one version only."""
    vdir = snapshot / "assessments" / "mi-sqt-adapted" / "v2"
    shutil.copytree(REAL_LIBRARY / "assessments" / "mi-sqt-adapted" / "v2", vdir)
    e = next(x for x in gallery.entries_from_library() if x.id == "mi-sqt-adapted")
    assert not e.download_only
    assert not e.version(2).download_only and e.version(1).download_only
    assert e.version(2).metrics and e.version(2).functions_covered
    assert gallery.pack_bytes(e, e.version(2)).startswith(b"PK")
    with pytest.raises(gallery.GalleryError, match="not on this computer"):
        gallery.pack_bytes(e, e.version(1))


def test_a_full_library_is_not_download_only():
    entries = gallery.entries_from_library()                    # the real apps/library
    assert entries
    assert not any(v.download_only for e in entries for v in e.versions)
    assert not any(e.download_only for e in entries)
    e = next(x for x in entries if x.id == "mi-sqt-adapted")
    assert gallery.pack_bytes(e, e.version()).startswith(b"PK")
    assert gallery.version_files_present(e.id, e.latest_version)
    assert not gallery.version_files_present(e.id, 99)


def test_a_published_catalog_never_reads_as_download_only():
    """Entries an installed copy parses from the release carry their packs."""
    e = next(x for x in gallery.entries_from_library() if x.id == "mi-sqt-adapted")
    doc = gallery.catalog_doc([e], source_commit="abc", schema=gallery.CATALOG_SCHEMA_V2)
    (back,) = gallery.parse_catalog(json.dumps(doc))
    assert not back.download_only and not any(v.download_only for v in back.versions)


# --------------------------------------------------------------------------- #
# The payload script and its check
# --------------------------------------------------------------------------- #
def _pathspecs() -> list[str]:
    """The git pathspecs build-apps-payload.ps1 archives, as written."""
    text = (DESKTOP_SCRIPTS / "build-apps-payload.ps1").read_text(encoding="utf-8")
    block = re.search(r"\$pathspecs = @\((.*?)\n\)", text, re.S)
    assert block, "the pathspec block moved"
    return re.findall(r"'([^']+)'", block.group(1))


@needs_checkout
def test_the_payload_script_excludes_version_folders_and_keeps_the_records():
    text = (DESKTOP_SCRIPTS / "build-apps-payload.ps1").read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text), "desktop/scripts/*.ps1 stay pure ASCII"
    specs = _pathspecs()
    assert "apps/library" in specs and ":(exclude)apps/library/assessments/*/v[0-9]*" in specs
    assert not any(s.endswith("/v*") for s in specs), "v* would drop validation.json"
    assert "'library\\catalog.json'" in text, "the catalog stays required"
    assert "check_payload_records.py" in text


@needs_checkout
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_git_archives_the_catalog_and_records_but_no_version_folder(tmp_path):
    """What git actually stages under the script's library pathspecs (HEAD of this checkout)."""
    specs = [s for s in _pathspecs() if "apps/library" in s]
    tar = tmp_path / "probe.tar"
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", "archive", "--format=tar",
                           "-o", str(tar), "HEAD", "--", *specs],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    with tarfile.open(tar) as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    assert "apps/library/catalog.json" in names
    assert not any(re.search(r"/assessments/[^/]+/v\d+/", n) for n in names), \
        [n for n in names if re.search(r"/v\d+/", n)][:3]
    per_assessment = {n.rsplit("/", 1)[-1] for n in names if "/assessments/" in n}
    assert per_assessment == set(RECORDS), per_assessment
    assert "apps/library/assessments/eastern-corn-belt-plains/validation.json" in names


def _payload_records():
    spec = importlib.util.spec_from_file_location(
        "check_payload_records_snapshot", DESKTOP_SCRIPTS / "check_payload_records.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_checkout
def test_the_payload_check_refuses_a_shipped_version_folder(tmp_path):
    mod = _payload_records()
    stage = tmp_path / "stage"
    data = stage / "stream-curves" / "data"
    data.mkdir(parents=True)
    (data / "nrsa_provenance.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    catalog_only_copy(stage / "library")
    assert mod.check(stage) == []

    vdir = stage / "library" / "assessments" / "mi-sqt-adapted" / "v2"
    vdir.mkdir()
    (vdir / "assessment.deep.json").write_text("{}", encoding="utf-8")
    problems = mod.check(stage)
    assert any("mi-sqt-adapted/v2" in p and "version folder" in p for p in problems), problems
    shutil.rmtree(vdir)

    (stage / "library" / "assessments" / "mi-sqt-adapted" / "manifest.json").unlink()
    assert any("mi-sqt-adapted/manifest.json: missing" in p for p in mod.check(stage))
    (stage / "library" / "catalog.json").unlink()
    assert any(p.startswith("library/catalog.json") for p in mod.check(stage))
