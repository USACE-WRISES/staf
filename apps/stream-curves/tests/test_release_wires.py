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
def test_the_two_manifest_writers_run_one_after_the_other():
    for name in ("streamcurves-shell.yml", "streamcurves-payload.yml"):
        concurrency = _workflow(name)["concurrency"]
        assert concurrency["group"] == "streamcurves-current", name
        assert concurrency["cancel-in-progress"] is False, name


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
    assert files == lr.catalog_names(doc) | {gallery.CATALOG_NAME}


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
