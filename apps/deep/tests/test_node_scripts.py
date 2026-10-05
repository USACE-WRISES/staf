"""Runs the Node tests of DEEP's own browser scripts (skipped where Node is not installed).

They share the fake DOM in libs/staf_workbook/tests/fakedom.cjs, so they run in the repo only.
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
@pytest.mark.parametrize("suite", ["test_measure.cjs", "test_coverage.cjs"])
def test_browser_scripts(suite):
    run = subprocess.run(["node", "--test", str(HERE / suite)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
