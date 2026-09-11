"""Publish ``staging/`` to the rolling GitHub prerelease with the gh CLI.

Only assets whose sha256 differs from the last published one are uploaded
(``--clobber`` replaces in place); ``manifest.json`` goes last so a reader
never sees a manifest pointing at an asset that is not there yet. The tag is
created as a prerelease when missing and is never turned into a normal
release (the desktop updater resolves ``releases/latest`` to installers).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from . import config
from .paths import DataRoot
from .state import Progress, now_iso

LOG = "publish_log.jsonl"


def gh(*args: str, check: bool = True, timeout: float = 1800.0) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check,
                          timeout=timeout)


def gh_ready() -> tuple[bool, str]:
    try:
        out = gh("auth", "status", check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gh not available: {exc}"
    text = (out.stdout + out.stderr)
    return ("Logged in" in text), text.strip().splitlines()[0][:120] if text.strip() else "gh auth status: no output"


def release_exists(tag: str = config.DATASET_TAG) -> Optional[dict]:
    out = gh("release", "view", tag, "--repo", config.GITHUB_REPO, "--json",
             "tagName,isPrerelease,assets,url", check=False, timeout=120)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def ensure_release(progress: Progress, tag: str = config.DATASET_TAG) -> dict:
    info = release_exists(tag)
    if info is not None:
        if not info.get("isPrerelease"):
            raise RuntimeError(f"{tag} is not a prerelease; refusing to publish to it")
        return info
    progress.say(f"creating the {tag} prerelease")
    gh("release", "create", tag, "--repo", config.GITHUB_REPO, "--prerelease",
       "--title", "EASI national screening dataset (rolling)",
       "--notes", ("Rolling precomputed EASI screening dataset. Assets are replaced in "
                   "place as coverage grows; manifest.json describes the current state. "
                   "Always a prerelease: the desktop installers are the normal releases."),
       timeout=300)
    return release_exists(tag) or {}


def published_shas(root: DataRoot) -> dict[str, str]:
    """Asset name -> sha256 of the last successful upload, from the ledger."""
    path = root.state / LOG
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("event") == "uploaded":
                out[entry["asset"]] = entry["sha256"]
    return out


def _log(root: DataRoot, **entry) -> None:
    with open(root.state / LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now_iso(), **entry}) + "\n")


def plan(root: DataRoot) -> list[dict]:
    """Which staging assets would upload (changed since last publish)."""
    manifest_path = root.staging / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wanted: dict[str, str] = {}
    for block in (manifest.get("assets") or {}).values():
        wanted[block["asset"]] = block["sha256"]
    for unit in (manifest.get("units") or {}).values():
        wanted[unit["evidence"]["asset"]] = unit["evidence"]["sha256"]
    for block in (manifest.get("scores") or {}).values():
        wanted[block["asset"]] = block["sha256"]
    for block in (manifest.get("tiles") or {}).values():
        wanted[block["asset"]] = block["sha256"]
    done = published_shas(root)
    items = []
    for name, sha in sorted(wanted.items()):
        path = root.staging / name
        if not path.exists():
            continue
        items.append({"asset": name, "sha256": sha, "bytes": path.stat().st_size,
                      "changed": done.get(name) != sha})
    items.append({"asset": "manifest.json", "sha256": _sha(manifest_path),
                  "bytes": manifest_path.stat().st_size, "changed": True})
    return items


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_publish(root: DataRoot, progress: Progress, *, dry_run: bool = False,
                tag: str = config.DATASET_TAG) -> dict:
    items = plan(root)
    changed = [i for i in items if i["changed"] and i["asset"] != "manifest.json"]
    total_mb = sum(i["bytes"] for i in changed) / 1e6
    progress.begin("publish", "publish", total=len(changed) + 1,
                   message=f"{len(changed)} changed assets, {total_mb:.1f} MB")
    if dry_run:
        progress.say("dry run: " + ", ".join(i["asset"] for i in changed) + ", manifest.json")
        return {"dry_run": True, "items": items}
    ok, note = gh_ready()
    if not ok:
        raise RuntimeError(f"gh is not authenticated: {note}")
    ensure_release(progress, tag)
    for n, item in enumerate(changed):
        path = root.staging / item["asset"]
        started = time.monotonic()
        gh("release", "upload", tag, str(path), "--repo", config.GITHUB_REPO, "--clobber")
        _log(root, event="uploaded", asset=item["asset"], sha256=item["sha256"],
             bytes=item["bytes"], seconds=round(time.monotonic() - started, 1))
        progress.tick(done=n + 1, message=f"uploaded {item['asset']} ({item['bytes'] / 1e6:.1f} MB)")
    manifest_path = root.staging / "manifest.json"
    gh("release", "upload", tag, str(manifest_path), "--repo", config.GITHUB_REPO, "--clobber")
    _log(root, event="uploaded", asset="manifest.json", sha256=_sha(manifest_path),
         bytes=manifest_path.stat().st_size)
    progress.tick(done=len(changed) + 1, message="uploaded manifest.json")
    verified = verify_manifest(root, tag)
    _log(root, event="published", assets=len(changed) + 1, verified=verified)
    progress.say(f"published {len(changed) + 1} assets to {tag}; manifest verified: {verified}")
    return {"dry_run": False, "items": items, "verified": verified}


def verify_manifest(root: DataRoot, tag: str = config.DATASET_TAG) -> bool:
    """Download the published manifest and compare it with staging (retries a
    little: the asset takes a moment to become downloadable)."""
    url = f"https://github.com/{config.GITHUB_REPO}/releases/download/{tag}/manifest.json"
    local = _sha(root.staging / "manifest.json")
    for _ in range(6):
        try:
            response = requests.get(url, timeout=60)
            if response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == local:
                return True
        except requests.RequestException:
            pass
        time.sleep(10)
    return False
