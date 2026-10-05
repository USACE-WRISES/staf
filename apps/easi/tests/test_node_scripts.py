"""Runs the Node tests of EASI's browser scripts (skipped where Node is not installed).

test_worksheet.cjs shares the fake DOM in libs/staf_workbook/tests/fakedom.cjs, so it runs in
the repo only; the others carry their own stand-ins.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FAKEDOM = HERE.parents[2] / "libs" / "staf_workbook" / "tests" / "fakedom.cjs"
SUITES = ["test_worksheet.cjs", "test_legend_bridge.cjs", "test_report_ready.cjs", "test_viewer.cjs",
          "test_viewer_compatibility.cjs"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not FAKEDOM.exists(), reason="needs the repo's libs/staf_workbook tests")
@pytest.mark.parametrize("suite", SUITES)
def test_browser_scripts(suite):
    run = subprocess.run(["node", "--test", str(HERE / suite)], capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout + run.stderr
