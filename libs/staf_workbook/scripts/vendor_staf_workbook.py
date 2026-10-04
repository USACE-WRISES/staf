"""Vendor ``libs/staf_workbook`` into EASI, SFARI and DEEP.

Copies the package to ``apps/<app>/<pkg>/_vendor/staf_workbook`` and the assets to
``apps/<app>/www/staf/``, and writes ``VENDOR_INFO.json`` (version and per-file hashes) beside the
package. Each app's ``tests/test_staf_workbook_vendor.py`` is the drift gate. Apps never import
``libs/`` at runtime: each deployment must be self-contained.

StreamCurves vendors the whole ``easi`` package (``apps/stream-curves/scripts/vendor_easi_engine.py``),
so re-run that after this when EASI's copy changed.

Run from anywhere:  python libs/staf_workbook/scripts/vendor_staf_workbook.py
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
LIB = HERE.parents[1]
REPO = HERE.parents[3]
SRC_PKG = LIB / "staf_workbook"
SRC_ASSETS = LIB / "assets"
APPS = (("easi", "easi"), ("sfari", "sfari"), ("deep", "deep"))
_SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def lib_version() -> str:
    m = re.search(r'LIB_VERSION\s*=\s*"([^"]+)"', (SRC_PKG / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "unknown"


def hash_tree(root: Path, py: bool | None) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(part in _SKIP_DIRS for part in p.parts) or p.name == "VENDOR_INFO.json":
            continue
        if py is not None and py != (p.suffix == ".py"):
            continue
        out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def vendor(app: str, pkg: str) -> str:
    dest = REPO / "apps" / app / pkg / "_vendor" / "staf_workbook"
    assets = REPO / "apps" / app / "www" / "staf"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC_PKG, dest, ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    (dest.parent / "__init__.py").touch()
    if assets.exists():
        shutil.rmtree(assets)
    shutil.copytree(SRC_ASSETS, assets, ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    info = {"vendored_from": "libs/staf_workbook", "lib_version": lib_version(),
            "manifest": hash_tree(SRC_PKG, True), "data_manifest": hash_tree(SRC_PKG, False),
            "assets_manifest": hash_tree(SRC_ASSETS, None)}
    (dest / "VENDOR_INFO.json").write_text(json.dumps(info, indent=1, sort_keys=True), encoding="utf-8",
                                           newline="\n")
    return (f"vendored staf_workbook {info['lib_version']} ({len(info['manifest'])} py, "
            f"{len(info['data_manifest'])} data, {len(info['assets_manifest'])} assets) -> "
            f"{dest.relative_to(REPO)} + {assets.relative_to(REPO)}")


def main() -> int:
    for app, pkg in APPS:
        print(vendor(app, pkg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
