"""HR slim builder: turns the USGS NHDPlus HR VPU packages into the compact
flowline and catchment files the STAF data app serves.

Local operator tool, never deployed. It streams each package straight out of its
zip on the USGS S3 bucket with GDAL (nothing is unzipped or stored), and imports
the file format (``hrslim``) from ``apps/hr-data`` so writer and reader share one
definition.
"""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

TOOL_ROOT = Path(__file__).resolve().parents[1]          # tools/hr-slim
REPO_ROOT = TOOL_ROOT.parents[1]                         # the monorepo root
HR_DATA_APP = REPO_ROOT / "apps" / "hr-data"


def bootstrap_hrslim() -> None:
    """Put ``apps/hr-data`` on ``sys.path`` so ``import hrslim`` resolves."""
    path = str(HR_DATA_APP)
    if path not in sys.path:
        sys.path.insert(0, path)


bootstrap_hrslim()
