"""The code fingerprint the manifest, stage_complete.json and the evidence package share
is the one the batch script has always computed."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from streamcurves import code_identity as ci

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "scripts" / "run_region_batch.py"


def _batch_module():
    spec = importlib.util.spec_from_file_location("run_region_batch_for_code_identity", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_fingerprint_equals_the_batch_scripts_on_this_tree():
    assert ci.fingerprint() == _batch_module().code_fingerprint()
    assert ci.fingerprint(APP) == ci.fingerprint()
    assert len(ci.fingerprint()) == 64 and ":" not in ci.fingerprint()


def test_the_fingerprint_reads_bytes_by_relative_path_and_skips_caches(tmp_path):
    root = tmp_path / "app"
    (root / "streamcurves").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "streamcurves" / "a.py").write_bytes(b"x = 1\n")
    (root / "scripts" / "run.py").write_bytes(b"print(1)\n")
    (root / "scripts" / "notes.txt").write_bytes(b"not code\n")     # scripts: *.py only
    first = ci.fingerprint(root)
    (root / "streamcurves" / "__pycache__").mkdir()
    (root / "streamcurves" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00")
    (root / "scripts" / "notes.txt").write_bytes(b"changed\n")
    assert ci.fingerprint(root) == first, "caches and non-py scripts never count"
    (root / "streamcurves" / "a.py").write_bytes(b"x = 2\n")
    assert ci.fingerprint(root) != first
    # the same bytes under another name are another tree
    moved = tmp_path / "app2"
    (moved / "streamcurves").mkdir(parents=True)
    (moved / "scripts").mkdir()
    (moved / "streamcurves" / "b.py").write_bytes(b"x = 1\n")
    (moved / "scripts" / "run.py").write_bytes(b"print(1)\n")
    assert ci.fingerprint(moved) != first


def test_git_helpers_answer_or_say_they_cannot(tmp_path):
    head = ci.git_head()
    assert head is None or (len(head) == 40 and all(c in "0123456789abcdef" for c in head))
    assert ci.git_dirty() in (True, False, None)
    # a folder that is no checkout: None, never an exception
    assert ci.git_head(tmp_path) is None
    assert ci.git_dirty(tmp_path) is None
    ident = ci.identity()
    assert ident["fingerprint"] == ci.fingerprint() and ident["scope"]
