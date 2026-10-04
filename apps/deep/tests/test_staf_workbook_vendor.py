"""Drift gate for the vendored STAF workbook toolkit (``libs/staf_workbook``): the vendored package
and the ``www/staf`` assets must match the recorded manifest everywhere, and the source wherever the
source tree is present. Re-run ``libs/staf_workbook/scripts/vendor_staf_workbook.py`` to fix."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1]
_PKG = _APP / _APP.name / "_vendor" / "staf_workbook"
_ASSETS = _APP / "www" / "staf"
_LIB = _APP.parents[1] / "libs" / "staf_workbook"
_SKIP = {"__pycache__", ".pytest_cache"}


def _hash_tree(root: Path, py) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_dir() or any(part in _SKIP for part in p.parts) or p.name == "VENDOR_INFO.json":
            continue
        if py is not None and py != (p.suffix == ".py"):
            continue
        out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _info() -> dict:
    return json.loads((_PKG / "VENDOR_INFO.json").read_text(encoding="utf-8"))


def test_vendored_copy_matches_its_manifest():
    info = _info()
    assert info["lib_version"] != "unknown"
    assert _hash_tree(_PKG, True) == info["manifest"]
    assert _hash_tree(_PKG, False) == info["data_manifest"]
    assert _hash_tree(_ASSETS, None) == info["assets_manifest"]


@pytest.mark.skipif(not _LIB.is_dir(), reason="libs/staf_workbook not present")
def test_vendored_copy_matches_the_source():
    info = _info()
    assert _hash_tree(_LIB / "staf_workbook", True) == info["manifest"], "re-run vendor_staf_workbook.py"
    assert _hash_tree(_LIB / "staf_workbook", False) == info["data_manifest"], "re-run vendor_staf_workbook.py"
    assert _hash_tree(_LIB / "assets", None) == info["assets_manifest"], "re-run vendor_staf_workbook.py"
