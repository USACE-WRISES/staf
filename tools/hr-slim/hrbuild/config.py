"""Builder configuration: the data root, the USGS source, and the recipe."""
from __future__ import annotations

import os
from pathlib import Path

ENV_ROOT = "HR_SLIM_ROOT"
DEFAULT_ROOT = Path(r"D:\Data\nhdplus-hr\slim")

#: The per-VPU packages the USGS NHDPlus HR MapServer serves (verified on VPUs
#: 0204, 0601 and 1506; National Release 2 differs in region 06).
S3_BASE = "https://prd-tnm.s3.amazonaws.com/"
S3_PREFIX = "StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/"

#: Recipe. Lines are Douglas-Peucker simplified (endpoints kept); catchments are
#: coverage-simplified (neighbours keep sharing their edges) with the VPU's outer
#: edge left as is; both are rounded to 1e-5 degree after simplification.
LINE_TOLERANCE_M = float(os.environ.get("HR_SLIM_LINE_TOL_M") or 5.0)
CATCHMENT_TOLERANCES_M = tuple(float(t) for t in
                               (os.environ.get("HR_SLIM_CAT_TOLS_M") or "10,20").split(","))
DEFAULT_CATCHMENT_TOLERANCE_M = 20.0
#: Extra line tolerances measured for the size report but not written.
LINE_TOLERANCES_MEASURED = (0.0, 2.0)
#: QA boxes per VPU (original full-precision geometry kept for the viewer).
QA_BOXES = 3
QA_BOX_DEG = 0.03

WORKERS = int(os.environ.get("HR_SLIM_WORKERS") or 4)

#: Version 2 (``hrslim.fmt2``): exact catchments, 2 m lines, a separate data root,
#: and the USGS zips downloaded once into a shared folder.
ENV_ROOT_V2 = "HR_SLIM2_ROOT"
DEFAULT_ROOT_V2 = Path(r"D:\Data\nhdplus-hr\slim2")
ZIPS_DIR = Path(os.environ.get("HR_SLIM_ZIPS") or r"D:\Data\nhdplus-hr\zips")
V2_LINE_TOLERANCE_M = float(os.environ.get("HR_SLIM2_LINE_TOL_M") or 2.0)
#: Metres at each flowline end kept with every vertex, where confluences are (0: simplify the
#: whole line). Owner decision 2026-10-01: 25 m (wrong-flowline snaps 0.5% to 0.05%, lines +6%).
V2_LINE_KEEP_ENDS_M = float(os.environ.get("HR_SLIM2_KEEP_ENDS_M") or 25.0)
V2_WORKERS = int(os.environ.get("HR_SLIM2_WORKERS") or 3)
#: The STAF data bundle's release assets (``release pack``; ``release publish`` uploads them).
RELEASE_DIR = Path(os.environ.get("HR_SLIM_RELEASE") or r"D:\Data\nhdplus-hr\release")

#: National counts on the USGS service (2026-10-01), for size extrapolation.
NATIONAL_FLOWLINES = 24_958_332
NATIONAL_CATCHMENTS = 25_541_804

#: GDAL settings for streaming zips over HTTP range requests.
GDAL_ENV = {
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".zip",
    "GDAL_HTTP_MAX_RETRY": "6",
    "GDAL_HTTP_RETRY_DELAY": "3",
    "GDAL_HTTP_TIMEOUT": "180",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "500000000",
}


def data_root() -> Path:
    """The data root: ``HR_SLIM_ROOT`` or ``D:\\Data\\nhdplus-hr\\slim``."""
    return Path(os.environ.get(ENV_ROOT) or DEFAULT_ROOT)


def data_root_v2() -> Path:
    """The version 2 data root: ``HR_SLIM2_ROOT`` or ``D:\\Data\\nhdplus-hr\\slim2``."""
    return Path(os.environ.get(ENV_ROOT_V2) or DEFAULT_ROOT_V2)


def apply_gdal_env() -> None:
    for key, value in GDAL_ENV.items():
        os.environ.setdefault(key, value)
