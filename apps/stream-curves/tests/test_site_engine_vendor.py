"""Drift gate for the vendored site engine (clone of the EASI-vendor pattern):
the vendored copy must match its recorded manifest everywhere, and must match
the ``libs/site_engine`` source wherever the source tree is present."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[1] / "streamcurves" / "_vendor" / "site_engine"
_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "libs" / "site_engine" / "site_engine"
_SKIP = {"__pycache__", ".pytest_cache"}


def _hash_tree(root: Path, py_only: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(part in _SKIP for part in p.parts):
            continue
        if p.name == "VENDOR_INFO.json":
            continue
        if py_only != (p.suffix == ".py"):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _info() -> dict:
    return json.loads((_VENDOR / "VENDOR_INFO.json").read_text(encoding="utf-8"))


def test_vendor_info_present():
    info = _info()
    assert info["manifest"] and info["data_manifest"]
    assert info["engine_version"] != "unknown"


def test_vendored_copy_matches_manifest():
    # Runs everywhere, including deployments where libs/ is absent: catches
    # hand-edits to the vendored tree.
    info = _info()
    assert _hash_tree(_VENDOR, True) == info["manifest"]
    assert _hash_tree(_VENDOR, False) == info["data_manifest"]


@pytest.mark.skipif(not _SRC.is_dir(), reason="engine source not present")
def test_vendor_in_sync_with_source():
    info = _info()
    assert _hash_tree(_SRC, True) == info["manifest"], \
        "re-run scripts/vendor_site_engine.py"
    assert _hash_tree(_SRC, False) == info["data_manifest"], \
        "re-run scripts/vendor_site_engine.py"


def test_nested_engine_copy_matches_the_direct_vendor():
    # The vendored EASI package carries its own copy of the site engine
    # (easi/_vendor/site_engine). StreamCurves therefore ships two copies, and
    # they must come from the same engine version and the same files.
    nested = _VENDOR.parent / "easi" / "_vendor" / "site_engine" / "VENDOR_INFO.json"
    assert nested.exists(), \
        "re-run scripts/vendor_easi_engine.py (EASI vendors the site engine now)"
    direct = _info()
    other = json.loads(nested.read_text(encoding="utf-8"))
    assert other["engine_version"] == direct["engine_version"]
    assert other["manifest"] == direct["manifest"]
    assert other["data_manifest"] == direct["data_manifest"]
