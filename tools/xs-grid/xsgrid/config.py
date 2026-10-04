"""Paths and constants for the cross-section grid (2026-10-03).

A section every 100 ft (30.48 m) on every NHDPlus HR stream, river, canal and ditch flowline of the
lower 48, sampled from USGS's 3DEP tile files and scored with EASI's cross-section code (through the
site engine's byte-synced copy). Plan: ``notes/2026-10-01_HR_Mirror/xs_section_grid_plan.md``.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
#: the STAF data bundle the sections are placed on (its 2 m lines, the apps' lines)
BUNDLE = Path(os.environ.get("XSGRID_BUNDLE") or r"D:\Data\nhdplus-hr\slim2\data")
#: the 3DEP tile catalogs the bundle ships (``tables/dem1m_tiles.parquet``, ``dem19_quads.parquet``)
CATALOGS = Path(os.environ.get("XSGRID_CATALOGS") or r"D:\Data\nhdplus-hr\slim2\tables")
#: the archive (every sampled transect, float32) and the metrics: the dedicated drive
ARCHIVE = Path(os.environ.get("XSGRID_ARCHIVE") or r"F:\staf-xs")
#: working files (section tables, logs, the metrics' second copy)
WORK = Path(os.environ.get("XSGRID_WORK") or r"D:\Data\xs-grid")

#: placement: sections evenly spaced on each flowline, as close to 100 ft apart as a whole number
#: allows, the first and last half a spacing from the ends (never on a confluence)
SPACING_M = 30.48
#: the app's direction chord: the section is perpendicular to the line from the section point to
#: the point 5 m downstream (``threedep.reach_geomorphology``)
CHORD_M = 5.0
#: the app's half-width: eight regional bankfull widths, 250 to 800 m
WIDE_FACTOR, WIDE_MIN_M, WIDE_MAX_M = 8.0, 250.0, 800.0
#: the app's sampling: a step of min(10 m, DEM resolution), at most 2001 points
STEP_CAP_M, MAX_POINTS = 10.0, 2001
#: the app's rule: a DEM that answers less than half the transect falls back to the next source
FINITE_MIN = 0.5
#: flowline types gridded: StreamRiver (460xx) and CanalDitch (336xx); artificial paths
#: (lake and wide-river centerlines), connectors and pipelines are not
FCODE_RANGES = ((46000, 46100), (33600, 33700))
#: batch cell in EPSG:5070 (one part file per region and cell)
CELL_M = 10_000
#: the 10 m fallback: USGS's 1/3 arc-second seamless DEM (same bucket)
SEAMLESS_URL = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
QUAD_RES_M = 3
SEAMLESS_RES_M = 10

#: what a stored transect means; any change here is a new sampling version
SAMPLING_VERSION = "xsgrid-1"
SAMPLING_RULE = ("straight transect in EPSG:5070 through the section point, perpendicular to the "
                 "5 m chord downstream; samples at linspace(-wide, wide, n_pts); each sample point "
                 "transformed to the tile's own CRS and interpolated bilinearly between the four "
                 "nearest cell centres of the native tile grid (NaN when any is missing), then "
                 "rounded to float32, the tiles' own precision")
#: archive file format (codec + schema)
FORMAT = 1
#: zstd level of the archive's transect column (measured 2026-10-03, ``xs_archive_codecs.py``)
ZSTD_LEVEL = int(os.environ.get("XSGRID_ZSTD") or 9)
#: one in this many sections also runs the reference thinning and must match the fast one
VERIFY_EVERY = 200


def gdal_env() -> dict:
    """The tile reader's GDAL settings with a larger block cache (a cell reads up to ~0.5 GB)."""
    env = dict(GDAL_ENV_BASE)
    env["GDAL_CACHEMAX"] = int(os.environ.get("XSGRID_GDAL_CACHE_MB") or 1024)
    return env


GDAL_ENV_BASE = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.vrt,.img,.ige,.rrd",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "8",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_HTTP_TIMEOUT": "120",
    "VSI_CACHE": "TRUE",
}
