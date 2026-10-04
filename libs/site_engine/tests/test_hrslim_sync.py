"""The _hrslim copies must match the slim reader in apps/hr-data (skipped where that source tree
is absent, e.g. in a vendored deployment)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parents[1]
_REPO = _ENGINE.parents[1]
_SRC = _REPO / "apps" / "hr-data" / "hrslim"
_DEST = _ENGINE / "site_engine" / "_hrslim"


def _sync_module():
    spec = importlib.util.spec_from_file_location("sync_hrslim", _ENGINE / "scripts" / "sync_hrslim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hrslim_tree_exists():
    info = json.loads((_DEST / "HRSLIM_INFO.json").read_text(encoding="utf-8"))
    for name in info["modules"]:
        assert (_DEST / name).exists()
    assert (_DEST / "__init__.py").exists()


@pytest.mark.skipif(not _SRC.is_dir(), reason="slim reader source not present")
def test_hrslim_in_sync_with_source():
    sync = _sync_module()
    for name in sync.MODULES:
        assert (_DEST / name).read_bytes() == (_SRC / name).read_bytes(), \
            f"{name} drifted; re-run scripts/sync_hrslim.py"
    assert (_DEST / "__init__.py").read_text(encoding="utf-8") == sync.INIT
