"""Runs the Node tests of the shared browser assets (skipped where Node is not installed)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_assets():
    run = subprocess.run(["node", "--test", str(HERE / "test_assets.cjs")], capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
