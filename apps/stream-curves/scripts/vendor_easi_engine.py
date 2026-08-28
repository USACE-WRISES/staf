"""Vendor the EASI batch engine into StreamCurves.

StreamCurves runs the EASI reference-condition screening in-process (no HTTP: the
Connect Cloud bot gate 403s server-to-server). This copies the ``easi`` package
and its bundled ``data/`` from ``apps/easi`` into
``apps/stream-curves/streamcurves/_vendor/easi`` so StreamCurves ships a
self-contained copy. The in-package ``data/`` is picked up by the engine's
``EASI_DATA_DIR -> in-package -> sibling`` resolution chain.

A ``VENDOR_INFO.json`` records the engine API version and a per-file hash manifest;
``tests/test_easi_screening.py`` uses it as a drift gate (fails when the vendored
copy diverges from the source, when the source is present locally).

Run from anywhere:  python apps/stream-curves/scripts/vendor_easi_engine.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
# repo root = .../staf ; script at apps/stream-curves/scripts/vendor_easi_engine.py
REPO = HERE.parents[3]
SRC_PKG = REPO / "apps" / "easi" / "easi"
SRC_DATA = REPO / "apps" / "easi" / "data"
DEST = REPO / "apps" / "stream-curves" / "streamcurves" / "_vendor" / "easi"

_SKIP_DIRS = {"__pycache__", ".pytest_cache"}
_SKIP_DATA_DIRS = {"source"}          # build-time-only sources, not needed at runtime


def _copy_tree(src: Path, dest: Path, skip_dirs: set[str]) -> None:
    for item in sorted(src.iterdir()):
        if item.name in skip_dirs:
            continue
        target = dest / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(item, target, skip_dirs)
        elif item.suffix != ".pyc":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _hash_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


def _hash_data_manifest(root: Path, skip_dirs: set[str]) -> dict[str, str]:
    # Data files (screening-methods.json etc.) drive scoring just like code, so
    # they get their own drift manifest; the *.py manifest cannot see them.
    manifest: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix == ".pyc":
            continue
        rel = p.relative_to(root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        manifest[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


def _engine_api_version() -> int:
    text = (SRC_PKG / "batch" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("ENGINE_API_VERSION"):
            return int(line.split("=")[1].strip())
    raise RuntimeError("ENGINE_API_VERSION not found in easi/batch/__init__.py")


def main() -> int:
    if not SRC_PKG.is_dir():
        print(f"source engine not found: {SRC_PKG}", file=sys.stderr)
        return 1
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    _copy_tree(SRC_PKG, DEST, _SKIP_DIRS)
    _copy_tree(SRC_DATA, DEST / "data", _SKIP_DIRS | _SKIP_DATA_DIRS)

    info = {
        "vendored_from": "apps/easi/easi",
        "engine_api_version": _engine_api_version(),
        "manifest": _hash_manifest(SRC_PKG),
        "data_manifest": _hash_data_manifest(SRC_DATA, _SKIP_DIRS | _SKIP_DATA_DIRS),
    }
    (DEST / "VENDOR_INFO.json").write_text(
        json.dumps(info, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    # Make the vendor tree a package (kept out of the drift manifest).
    (DEST.parent / "__init__.py").touch()
    n = len(info["manifest"])
    print(f"vendored {n} engine modules + data -> {DEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
