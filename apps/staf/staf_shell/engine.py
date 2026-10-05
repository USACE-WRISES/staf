"""One STAF site engine for every tool of the STAF app.

EASI, SFARI and DEEP each vendor the same engine (libs/site_engine) inside their own package, and
the copies are byte-identical (each app's drift gate). Three copies in one process would mean three
data bundle states racing on one cache folder (``assets.json``), three request limits of six (the
limit is meant per process) and three tile pools. So the first tool's copy is imported whole, and
the other tools' module names are pointed at it before their packages are imported: every tool then
runs on one engine, and no tool's code changes (EASI's frozen method files keep importing their own
``._vendor.site_engine``, which is the copy everyone shares).
"""
from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from pathlib import Path

ENGINE = "_vendor.site_engine"


def identity(pkg_dir: Path):
    """What makes two vendored copies one engine: its version and its file manifests."""
    try:
        info = json.loads((pkg_dir / "_vendor" / "site_engine" / "VENDOR_INFO.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info.get("engine_version"), info.get("manifest"), info.get("data_manifest")


def same(root: Path, key: str, canonical: str) -> bool:
    mine, theirs = identity(root / key / key), identity(root / canonical / canonical)
    if mine is None or mine != theirs:
        print(f"[staf] {key} keeps its own site engine: its copy differs from {canonical}'s", file=sys.stderr)
        return False
    return True


def load(key: str) -> list[str]:
    """Import ``key``'s whole engine, so no later import loads one of its modules a second time
    under another tool's name. Returns the modules that failed to import (they fail for every tool)."""
    base = importlib.import_module(f"{key}.{ENGINE}")
    failed = []
    for info in pkgutil.walk_packages(base.__path__, base.__name__ + ".", onerror=failed.append):
        try:
            importlib.import_module(info.name)
        except Exception as exc:  # noqa: BLE001 - an optional dependency; the tools see the same error
            failed.append(f"{info.name}: {exc}")
    return failed


def alias(key: str, canonical: str) -> None:
    """Point every ``<key>._vendor.site_engine[.x]`` module name at ``canonical``'s module."""
    prefix = f"{canonical}.{ENGINE}"
    for name, module in list(sys.modules.items()):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules[f"{key}.{ENGINE}{name[len(prefix):]}"] = module


def attach(key: str, canonical: str) -> None:
    """Hang the shared engine on ``<key>._vendor`` as well, as a normal import would have."""
    vendor = importlib.import_module(f"{key}._vendor")
    vendor.site_engine = sys.modules[f"{canonical}.{ENGINE}"]


def shared_with(key: str, canonical: str) -> bool:
    return sys.modules.get(f"{key}.{ENGINE}") is sys.modules.get(f"{canonical}.{ENGINE}")
