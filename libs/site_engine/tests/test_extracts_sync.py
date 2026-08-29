"""The _extracted copies must match a fresh run of the sync transform against
the canonical EASI sources (skipped where the source tree is absent, e.g. in a
vendored deployment)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parents[1]
_REPO = _ENGINE.parents[1]
_EASI = _REPO / "apps" / "easi"
_DEST = _ENGINE / "site_engine" / "_extracted"


def _sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_engine_extracts", _ENGINE / "scripts" / "sync_engine_extracts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extracted_tree_exists():
    info = json.loads((_DEST / "EXTRACTS_INFO.json").read_text(encoding="utf-8"))
    for name in info["modules"]:
        assert (_DEST / name).exists()
    for name in info["data"]:
        assert (_DEST / "data" / name).exists()


@pytest.mark.skipif(not _EASI.is_dir(), reason="EASI source not present")
def test_extracts_in_sync_with_source():
    sync = _sync_module()
    for name, src in sync.MODULES.items():
        expected = sync.transform(name, src.read_text(encoding="utf-8"))
        actual = (_DEST / name).read_text(encoding="utf-8")
        assert actual == expected, f"{name} drifted; re-run sync_engine_extracts.py"
    for name, src in sync.DATA_FILES.items():
        assert (_DEST / "data" / name).read_bytes() == src.read_bytes(), \
            f"{name} drifted; re-run sync_engine_extracts.py"
