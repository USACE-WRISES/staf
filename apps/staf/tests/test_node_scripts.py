"""Runs the Node test of the shell's own script (skipped where Node is not installed).

It shares the fake DOM in libs/staf_workbook/tests/fakedom.cjs, so it runs in the repo only.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FAKEDOM = HERE.parents[2] / "libs" / "staf_workbook" / "tests" / "fakedom.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not FAKEDOM.exists(), reason="needs the repo's libs/staf_workbook tests")
def test_the_shell_script():
    run = subprocess.run(["node", "--test", str(HERE / "test_shell_js.cjs")], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
