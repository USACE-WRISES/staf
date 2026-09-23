"""The release wiring StreamCurves Desktop depends on (desktop/RELEASING.md), pinned.

* Only `streamcurves-v*` shell releases are normal releases; the payload, the rolling manifest
  and the `library` release are always prereleases.
* Every vpk command names the `streamcurves` channel: Velopack merges the feed of every release
  on a channel and never checks the package id.
* The library builder writes every asset its catalog names, byte for byte the same on a rerun,
  and uploads the catalog last.
* Every reader looks for the releases in this repository.

The workflows and the shell live outside the app folder, so those tests skip in a copy of the
app without a checkout around it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import types
from pathlib import Path

import pytest
import yaml

from streamcurves import gallery
from streamcurves.version import REPO

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DESKTOP = REPO_ROOT / "desktop"

needs_checkout = pytest.mark.skipif(not WORKFLOWS.is_dir() or not DESKTOP.is_dir(),
                                    reason="not a STAF checkout (no .github/workflows or desktop/)")


def _library_release():
    spec = importlib.util.spec_from_file_location("library_release",
                                                  APP_DIR / "scripts" / "library_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as True.
    return doc.get("on", doc.get(True)) or {}


def _commands(doc: dict, program: str) -> list[str]:
    """Every `<program> ...` command in the workflow's run steps, with PowerShell backtick
    continuations joined."""
    out = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = re.sub(r"`[ \t]*\r?\n[ \t]*", " ", str(step.get("run") or ""))
            for line in run.splitlines():
                line = line.strip()
                if line.startswith(program + " "):
                    out.append(line)
    return out


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #
@needs_checkout
def test_the_retired_desktop_workflows_are_gone():
    names = {p.name for p in WORKFLOWS.glob("*.yml")}
    assert not names & {"desktop-shell.yml", "desktop-payload.yml", "desktop-offline-bundle.yml"}
    assert {"streamcurves-shell.yml", "streamcurves-payload.yml", "library-release.yml"} <= names


@needs_checkout
def test_no_workflow_fires_on_a_bare_version_tag():
    for p in WORKFLOWS.glob("*.yml"):
        push = _triggers(yaml.safe_load(p.read_text(encoding="utf-8"))).get("push") or {}
        for tag in push.get("tags") or []:
            assert tag.startswith("streamcurves-v"), (p.name, tag)


@needs_checkout
def test_every_vpk_command_names_the_streamcurves_channel():
    vpk = _commands(_workflow("streamcurves-shell.yml"), "vpk")
    assert sorted(c.split()[1] for c in vpk) == ["download", "pack", "upload"]
    for command in vpk:
        assert "--channel streamcurves" in command, command
    pack = next(c for c in vpk if c.startswith("vpk pack"))
    assert "--packId StreamCurvesDesktop" in pack and "--mainExe StreamCurvesDesktop.exe" in pack
    upload = next(c for c in vpk if c.startswith("vpk upload"))
    assert "--pre" not in upload.split(), "the shell release is a normal release"


@needs_checkout
def test_the_shell_release_names_its_assets_after_the_channel():
    text = (WORKFLOWS / "streamcurves-shell.yml").read_text(encoding="utf-8")
    assert "StreamCurvesDesktop-streamcurves-Setup.exe" in text
    assert "StreamCurvesDesktop-streamcurves-Portable.zip" in text


@needs_checkout
def test_every_release_the_payload_workflow_creates_is_a_prerelease():
    creates = _commands(_workflow("streamcurves-payload.yml"), "gh release create")
    assert len(creates) == 2, creates           # the payload, and streamcurves-current once
    for command in creates:
        assert "--prerelease" in command.split(), command


@needs_checkout
def test_the_tag_workflows_never_cancel_each_other():
    """GitHub keeps ONE pending run per concurrency group, so a group shared by the shell and the
    payload let one tag's runs cancel each other (streamcurves-v1.0.0 lost its payload that way)."""
    groups = [_workflow(n)["concurrency"] for n in ("streamcurves-shell.yml",
                                                     "streamcurves-payload.yml")]
    assert groups[0]["group"] != groups[1]["group"]
    assert all(g["cancel-in-progress"] is False for g in groups)


@needs_checkout
def test_a_duplicate_tag_event_does_nothing():
    """GitHub can deliver one tag push twice: a first job finds the release already published and
    the main job does not run."""
    for name, main, output in (("streamcurves-shell.yml", "shell", "published"),
                               ("streamcurves-payload.yml", "payload", "duplicate")):
        jobs = _workflow(name)["jobs"]
        assert "check" in jobs and output in jobs["check"]["outputs"], name
        assert jobs[main]["needs"] == "check", name
        assert f"needs.check.outputs.{output}" in jobs[main]["if"], name


@needs_checkout
def test_the_library_workflow_builds_then_uploads_on_library_pushes():
    doc = _workflow("library-release.yml")
    push = _triggers(doc)["push"]
    assert push["branches"] == ["main"] and "apps/library/**" in push["paths"]
    assert doc["permissions"]["contents"] == "write"
    runs = [str(s.get("run") or "") for s in doc["jobs"]["publish"]["steps"]]
    build = next(i for i, r in enumerate(runs) if "library_release.py build" in r)
    upload = next(i for i, r in enumerate(runs) if "library_release.py upload" in r)
    assert build < upload


@needs_checkout
def test_every_reader_looks_in_this_repository():
    assert gallery.PUBLIC_BASE_URL == f"https://github.com/{REPO}/releases/download/library/"
    deep_remote = (REPO_ROOT / "apps" / "deep" / "deep" / "remote_library.py").read_text(
        encoding="utf-8")
    assert f'DEFAULT_URL = "{gallery.PUBLIC_BASE_URL}"' in deep_remote
    program = (DESKTOP / "src" / "StreamCurves.Desktop" / "Program.cs").read_text(encoding="utf-8")
    assert (f"https://github.com/{REPO}/releases/download/streamcurves-current/"
            "latest-desktop.json") in program
    updater = (DESKTOP / "src" / "StreamCurves.Desktop" / "ShellUpdater.cs").read_text(
        encoding="utf-8")
    assert "prerelease: false" in updater


# --------------------------------------------------------------------------- #
# The apps payload carries the checkout's bytes
# --------------------------------------------------------------------------- #
def _payload_script_lines() -> list[str]:
    text = (DESKTOP / "scripts" / "build-apps-payload.ps1").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


@needs_checkout
def test_the_payload_archive_honours_gitattributes():
    """A subtree archive (HEAD:apps) skips the root .gitattributes, so core.autocrlf would decide
    the line endings (CRLF on a Windows build machine) and the app would hash other bytes than
    the maintainer's checkout. The full tree, with the machine's setting switched off, does not."""
    lines = _payload_script_lines()
    archive = [ln for ln in lines if ln.startswith("git ") and " archive " in ln]
    assert len(archive) == 1, archive
    assert "core.autocrlf=false" in archive[0]
    assert " HEAD -- " in archive[0] and "HEAD:" not in archive[0]
    assert any(ln.startswith("tar -xf") and "--strip-components=1" in ln for ln in lines)
    assert any("check_payload_records.py" in ln for ln in lines)


def _payload_records():
    spec = importlib.util.spec_from_file_location(
        "check_payload_records", DESKTOP / "scripts" / "check_payload_records.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_checkout
def test_the_payload_check_refuses_bytes_that_differ_from_their_records(tmp_path):
    mod = _payload_records()
    data = tmp_path / "stream-curves" / "data"
    (data / "nrsa").mkdir(parents=True)
    body = b"a,b\r\n1,2\r\n"
    (data / "sites.csv").write_bytes(body)
    record = {"file": "sites.csv", "bytes": len(body),
              "sha256": "sha256:" + hashlib.sha256(body).hexdigest()}
    (data / "nrsa_provenance.json").write_text(json.dumps({"files": [record]}), encoding="utf-8")
    (data / "nrsa" / "manifest.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
    config = tmp_path / "stream-curves" / "config"
    config.mkdir()
    (config / "metric_map.yaml").write_bytes(b"a: 1\n")
    assert mod.check(tmp_path) == []

    (data / "sites.csv").write_bytes(body.replace(b"\r\n", b"\n"))     # an LF copy of a CRLF record
    assert any("sites.csv" in p for p in mod.check(tmp_path))
    (data / "sites.csv").write_bytes(body)
    (config / "metric_map.yaml").write_bytes(b"a: 1\r\n")               # the autocrlf leak
    assert any("metric_map.yaml" in p for p in mod.check(tmp_path))


# --------------------------------------------------------------------------- #
# The library builder
# --------------------------------------------------------------------------- #
_PATTERNS = {
    "pack": r"(?P<id>[a-z0-9-]+)-v(?P<v>\d+)-p1-(?P<sha>[0-9a-f]{8})\.streamcurves",
    "bundle": r"(?P<id>[a-z0-9-]+)-v(?P<v>\d+)-(?P<sha>[0-9a-f]{8})\.deep\.json",
    "calculator": r"(?P<id>[a-z0-9-]+)-v(?P<v>\d+)-calculator-(?P<sha>[0-9a-f]{8})\.xlsx",
}


def test_the_builder_writes_every_asset_its_catalog_names_the_same_way_twice(tmp_path,
                                                                            monkeypatch):
    lr = _library_release()
    entries = [e for e in gallery.entries_from_library() if e.id == "eastern-corn-belt-plains"]
    assert entries
    monkeypatch.setattr(lr.gallery, "entries_from_library", lambda: entries)
    doc = lr.build(tmp_path / "a", commit="abc123")
    lr.build(tmp_path / "b", commit="abc123")

    assert doc["schema"] == gallery.CATALOG_SCHEMA and doc["source"]["commit"] == "abc123"
    on_disk = json.loads((tmp_path / "a" / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    assert on_disk == doc
    (entry,) = doc["assessments"]
    assert [v["version"] for v in entry["versions"]] == sorted(
        (v.version for v in entries[0].versions), reverse=True)     # newest first
    for v in entry["versions"]:
        assert {"pack", "bundle"} <= set(v["assets"])
        for kind, asset in v["assets"].items():
            m = re.fullmatch(_PATTERNS[kind], asset["name"])
            assert m, asset["name"]
            assert (m["id"], int(m["v"])) == (entry["id"], v["version"])
            data = (tmp_path / "a" / asset["name"]).read_bytes()
            assert len(data) == asset["size"]
            assert hashlib.sha256(data).hexdigest() == asset["sha256"]
            assert asset["sha256"].startswith(m["sha"])
            assert data == (tmp_path / "b" / asset["name"]).read_bytes(), "not deterministic"
    files = {p.name for p in (tmp_path / "a").iterdir()}
    # the schema-1 feed, the typed feed, and every asset either names
    assert files == lr.all_names(tmp_path / "a") | {gallery.CATALOG_NAME, gallery.CATALOG_NAME_V2}
    assert lr.catalog_names(doc) <= files


def test_upload_creates_a_prerelease_and_sends_the_catalog_last(tmp_path, monkeypatch):
    lr = _library_release()
    folder = tmp_path / "lib"
    folder.mkdir()
    names = [f"demo-v{i}-p1-0000000{i}.streamcurves" for i in range(1, 4)]
    for n in names:
        (folder / n).write_bytes(b"x")
    doc = {"schema": 1, "assessments": [{"id": "demo", "versions": [
        {"version": i + 1, "assets": {"pack": {"name": n, "size": 1, "sha256": "0" * 64}}}
        for i, n in enumerate(names)]}]}
    (folder / gallery.CATALOG_NAME).write_text(json.dumps(doc), encoding="utf-8")

    calls = []
    monkeypatch.setattr(lr, "_gh", lambda *a, check=True: calls.append(a) or types.SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(lr, "release_assets", lambda repo, tag: None)   # no release yet
    lr.upload(folder, "owner/repo", "library", commit="abc123")

    create, *uploads = calls
    assert create[:3] == ("release", "create", "library") and "--prerelease" in create
    assert all(c[:3] == ("release", "upload", "library") for c in uploads)
    *assets, catalog = uploads
    sent = [Path(a).name for c in assets for a in c[3:c.index("--repo")]]
    assert sorted(sent) == sorted(names)
    assert Path(catalog[3]).name == gallery.CATALOG_NAME and "--clobber" in catalog


def test_upload_refuses_a_published_name_whose_bytes_differ(tmp_path, monkeypatch):
    lr = _library_release()
    folder = tmp_path / "lib"
    folder.mkdir()
    (folder / "demo-v1-p1-00000001.streamcurves").write_bytes(b"xx")
    doc = {"schema": 1, "assessments": [{"id": "demo", "versions": [{"version": 1, "assets": {
        "pack": {"name": "demo-v1-p1-00000001.streamcurves", "size": 2, "sha256": "0" * 64}}}]}]}
    (folder / gallery.CATALOG_NAME).write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(lr, "release_assets",
                        lambda repo, tag: {"demo-v1-p1-00000001.streamcurves": 5})
    monkeypatch.setattr(lr, "_gh", lambda *a, check=True: pytest.fail("nothing may upload"))
    with pytest.raises(SystemExit, match="differ"):
        lr.upload(folder, "owner/repo", "library")
