"""EASI National Builder: the offline pipeline that precomputes the EASI
screening for every NHDPlus V2 reach, chunk by chunk, with checkpoints,
pause/resume, and progressive publishing to the rolling GitHub prerelease.

Local operator tool. It imports the EASI package straight from ``apps/easi``
(never vendored, never deployed), so the scoring code is the app's own.
"""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

TOOL_ROOT = Path(__file__).resolve().parents[1]          # tools/easi-national
REPO_ROOT = TOOL_ROOT.parents[1]                         # the monorepo root
EASI_APP = REPO_ROOT / "apps" / "easi"


def bootstrap_easi() -> None:
    """Put ``apps/easi`` on ``sys.path`` so ``import easi`` resolves."""
    path = str(EASI_APP)
    if path not in sys.path:
        sys.path.insert(0, path)


bootstrap_easi()
