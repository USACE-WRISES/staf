"""Vendor the STAF site engine into EASI.

EASI runs the engine in-process for streams outside the StreamCat lookup
network (apps never import ``libs/`` at runtime; each Posit deployment must be
self-contained). This copies ``libs/site_engine/site_engine`` (including the
``_extracted`` modules and data) into ``apps/easi/easi/_vendor/site_engine``.

Consequence worth knowing: StreamCurves vendors the whole ``easi`` package
with ``apps/stream-curves/scripts/vendor_easi_engine.py``, so the nested
``easi/_vendor/site_engine`` rides along into ``streamcurves/_vendor/easi/``
beside StreamCurves' own ``streamcurves/_vendor/site_engine``. Both copies
come from the same source and its drift gates keep them at one version.

``VENDOR_INFO.json`` records the engine version and per-file hash manifests;
``tests/test_site_engine_vendor.py`` uses it as a drift gate (fails when the
vendored copy diverges from the source, when the source is present locally).

Run from anywhere:  python apps/easi/scripts/vendor_site_engine.py
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
SRC_PKG = REPO / "libs" / "site_engine" / "site_engine"
DEST = REPO / "apps" / "easi" / "easi" / "_vendor" / "site_engine"

_SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def _engine_version() -> str:
    text = (SRC_PKG / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'ENGINE_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "unknown"


def _hash_tree(root: Path, suffixes: tuple[str, ...] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(part in _SKIP_DIRS for part in p.parts):
            continue
        if suffixes is not None and p.suffix not in suffixes:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(SRC_PKG, DEST,
                    ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    vendor_root = DEST.parent
    (vendor_root / "__init__.py").touch()
    info = {
        "vendored_from": "libs/site_engine/site_engine",
        "engine_version": _engine_version(),
        "manifest": _hash_tree(SRC_PKG, (".py",)),
        "data_manifest": {k: v for k, v in _hash_tree(SRC_PKG, None).items()
                          if not k.endswith(".py")},
    }
    (DEST / "VENDOR_INFO.json").write_text(
        json.dumps(info, indent=1, sort_keys=True), encoding="utf-8")
    print(f"vendored site_engine {info['engine_version']} "
          f"({len(info['manifest'])} py + {len(info['data_manifest'])} data) "
          f"-> {DEST.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
