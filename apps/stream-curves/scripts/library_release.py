"""Publish the assessment library as the rolling GitHub prerelease `library`.

apps/library in the repository is the one source of truth; this script turns it into what
every installed StreamCurves (the gallery) and DEEP (its remote library) read:

    library.json                               the catalog: every assessment, every version,
                                               status, validation and the assets below
    <id>-v<N>-p<schema>-<sha8>.streamcurves    the version's pack (a project file a user
                                               downloads and opens as their own copy)
    <id>-v<N>-<sha8>.deep.json                 the version's DEEP bundle
    <id>-v<N>-calculator-<sha8>.xlsx           the version's Excel calculator, when it has one

Asset names carry a content hash, so a changed file is a new asset and a published name never
changes meaning; upload sends only names the release does not have, then library.json LAST,
so a client never sees a catalog that names an asset still in flight. A validation change
touches only library.json; a status change also rebuilds that version's pack, whose origin
block names the status (the old pack stays on the release until `prune --yes`).

    python apps/stream-curves/scripts/library_release.py build --out build/library [--commit SHA]
    python apps/stream-curves/scripts/library_release.py check --dir build/library
    python apps/stream-curves/scripts/library_release.py upload --dir build/library [--dry-run]
    python apps/stream-curves/scripts/library_release.py prune --dir build/library [--yes]

upload and check use the gh CLI (GH_TOKEN in CI); the release is created as a PRERELEASE when
it is missing, so releases/latest stays the StreamCurves installer. prune lists (and with --yes
deletes) release assets the current catalog no longer names.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from streamcurves import gallery  # noqa: E402
from streamcurves import library as lib  # noqa: E402
from streamcurves import project_file as pf  # noqa: E402
from streamcurves.version import REPO  # noqa: E402

TAG = gallery.RELEASE_TAG
TITLE = "STAF assessment library"
NOTES = ("The published STAF detailed assessments, rebuilt from apps/library by "
         "apps/stream-curves/scripts/library_release.py. StreamCurves Desktop lists them in "
         "its Assessment library; DEEP runs the preliminary and final versions. library.json "
         "is the catalog; every other asset is immutable.")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_bytes(p: Path) -> bytes:
    """A text file's bytes with newlines normalized (a CRLF checkout builds the same asset)."""
    return p.read_text(encoding="utf-8").encode("utf-8")


def build(out: Path, *, commit: str | None = None) -> dict:
    """Write library.json and every asset into `out`; return the catalog."""
    out.mkdir(parents=True, exist_ok=True)
    rebuilt = []
    for e in gallery.entries_from_library():
        versions = []
        for v in e.versions:
            assets = {}
            pack = gallery.pack_bytes(e, v)
            sha = _sha(pack)
            name = pf.pack_asset_name(e.id, v.version, sha)
            (out / name).write_bytes(pack)
            assets["pack"] = gallery.Asset(name=name, size=len(pack), sha256=sha)
            bundle_path = lib.version_dir(e.id, v.version) / lib.BUNDLE_FILE
            if bundle_path.is_file():
                data = _text_bytes(bundle_path)
                sha = _sha(data)
                name = f"{e.id}-v{v.version}-{sha[:8]}.deep.json"
                (out / name).write_bytes(data)
                assets["bundle"] = gallery.Asset(name=name, size=len(data), sha256=sha)
            calc = lib.calculator_path(e.id, v.version)
            if calc.is_file():
                data = calc.read_bytes()
                sha = _sha(data)
                name = f"{e.id}-v{v.version}-calculator-{sha[:8]}.xlsx"
                (out / name).write_bytes(data)
                assets["calculator"] = gallery.Asset(name=name, size=len(data), sha256=sha)
            versions.append(dataclasses.replace(v, assets=assets))
        rebuilt.append(dataclasses.replace(e, versions=tuple(versions)))
    doc = gallery.catalog_doc(rebuilt, source_commit=commit)
    (out / gallery.CATALOG_NAME).write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                                            encoding="utf-8")
    n_assets = sum(len(v.assets) for e in rebuilt for v in e.versions)
    print(f"built {len(rebuilt)} assessments, "
          f"{sum(len(e.versions) for e in rebuilt)} versions, {n_assets} assets into {out}")
    return doc


def catalog_names(doc: dict) -> set[str]:
    names = set()
    for a in doc.get("assessments") or []:
        for v in a.get("versions") or []:
            for asset in (v.get("assets") or {}).values():
                if asset and asset.get("name"):
                    names.add(asset["name"])
    return names


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip() or proc.stdout}")
    return proc


def release_assets(repo: str, tag: str) -> dict[str, int] | None:
    """{name: size} of the release's assets, or None when the release does not exist."""
    proc = _gh("release", "view", tag, "--repo", repo, "--json", "assets", check=False)
    if proc.returncode != 0:
        if "release not found" in (proc.stderr + proc.stdout).lower():
            return None
        raise SystemExit(f"gh release view failed: {proc.stderr.strip()}")
    return {a["name"]: int(a.get("size") or 0) for a in json.loads(proc.stdout).get("assets") or []}


def plan(folder: Path, repo: str, tag: str) -> tuple[list[str], list[str], dict | None]:
    """(names to upload, names already there with a different size, existing assets)."""
    doc = json.loads((folder / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    existing = release_assets(repo, tag)
    have = existing or {}
    missing, clashes = [], []
    for name in sorted(catalog_names(doc)):
        size = (folder / name).stat().st_size
        if name not in have:
            missing.append(name)
        elif have[name] != size:
            clashes.append(name)
    return missing, clashes, existing


def upload(folder: Path, repo: str, tag: str, *, dry_run: bool = False,
           commit: str | None = None) -> None:
    missing, clashes, existing = plan(folder, repo, tag)
    if clashes:
        raise SystemExit("These published assets differ from the build under the same name "
                         f"(names carry a content hash, so this should not happen): {clashes}")
    print(f"{len(missing)} asset(s) to upload; library.json last")
    if dry_run:
        for name in missing:
            print("  would upload", name)
        return
    if existing is None:
        args = ["release", "create", tag, "--repo", repo, "--prerelease", "--title", TITLE,
                "--notes", NOTES]
        if commit:
            args += ["--target", commit]
        _gh(*args)
        print(f"created the prerelease {tag}")
    for i in range(0, len(missing), 20):
        batch = [str(folder / n) for n in missing[i:i + 20]]
        _gh("release", "upload", tag, *batch, "--repo", repo)
        print(f"  uploaded {min(i + 20, len(missing))} of {len(missing)}")
    _gh("release", "upload", tag, str(folder / gallery.CATALOG_NAME), "--repo", repo,
        "--clobber")
    print("uploaded library.json")


def prune(folder: Path, repo: str, tag: str, *, yes: bool = False) -> None:
    doc = json.loads((folder / gallery.CATALOG_NAME).read_text(encoding="utf-8"))
    keep = catalog_names(doc) | {gallery.CATALOG_NAME}
    have = release_assets(repo, tag) or {}
    stale = sorted(n for n in have if n not in keep)
    for name in stale:
        if yes:
            _gh("release", "delete-asset", tag, name, "--repo", repo, "--yes")
            print("deleted", name)
        else:
            print("superseded:", name)
    if stale and not yes:
        print("run again with --yes to delete them")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--commit")
    for name in ("check", "upload", "prune"):
        p = sub.add_parser(name)
        p.add_argument("--dir", type=Path, required=True)
        p.add_argument("--repo", default=REPO)
        p.add_argument("--tag", default=TAG)
        if name == "upload":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--commit")
        if name == "prune":
            p.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.out, commit=a.commit)
    elif a.cmd == "check":
        missing, clashes, existing = plan(a.dir, a.repo, a.tag)
        print("release exists" if existing is not None else "release does not exist yet")
        print(f"{len(missing)} to upload, {len(clashes)} clashing")
        for n in missing:
            print("  +", n)
        for n in clashes:
            print("  !", n)
    elif a.cmd == "upload":
        upload(a.dir, a.repo, a.tag, dry_run=a.dry_run, commit=a.commit)
    else:
        prune(a.dir, a.repo, a.tag, yes=a.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
