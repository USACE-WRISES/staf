"""An assembled _tools/ (scripts/assemble_tools.py, run before a deploy) is exactly the tools as
the repo has them now: every file the assembler would copy, with the same bytes. Skipped when
nothing has been assembled. A failure means the tools changed since: re-run the assembler (or
delete _tools/ while developing)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "_tools"


@pytest.mark.skipif(not (OUT / "MANIFEST.json").exists(), reason="nothing assembled")
def test_the_assembled_tools_match_the_repo():
    spec = importlib.util.spec_from_file_location("assemble_tools", HERE / "scripts" / "assemble_tools.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    manifest = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
    for tool in tools.TOOLS:
        recorded = manifest["tools"][tool]["files"]
        expected = tools.selection(tool, tools.tracked(tool))
        assert sorted(recorded) == sorted(expected), f"{tool}: re-run scripts/assemble_tools.py"
        for rel in expected:
            now = hashlib.sha256((HERE.parent / tool / rel).read_bytes()).hexdigest()
            copied = hashlib.sha256((OUT / tool / rel).read_bytes()).hexdigest()
            assert now == copied == recorded[rel], f"{tool}/{rel}: re-run scripts/assemble_tools.py"
