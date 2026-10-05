"""Runs the Node tests of the shared browser assets (skipped where Node is not installed)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("suite", ["test_assets.cjs", "test_tools.cjs"])
def test_browser_assets(suite):
    run = subprocess.run(["node", "--test", str(HERE / suite)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
