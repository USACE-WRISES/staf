"""Slim NHDPlus HR: compact flowline and catchment files and their reader.

The files hold the USGS NHDPlus HR network flowlines (the 15 fields the STAF site
engine reads) and their catchments. Version 1 (``hrslim.fmt``) rounds coordinates
to about 1 m and simplifies catchments; version 2 (``hrslim.fmt2``) stores
catchments exactly as shared borders on the elevation grid they were cut from.
``tools/hr-slim`` writes them; the data app (``apps/hr-data``) and, later, the site
engine read them. ``Dataset(folder)`` opens either version.
"""
from __future__ import annotations

import json
from pathlib import Path

from .fmt import FORMAT_VERSION, SCALE
from .reader import Dataset as Dataset1, catchment_features, line_features
from .reader2 import Dataset2

__version__ = "0.2.0"
__all__ = ["Dataset", "Dataset1", "Dataset2", "FORMAT_VERSION", "SCALE", "catchment_features",
           "line_features", "open_dataset"]


def open_dataset(root, **kwargs):
    """The reader for the folder's format (``manifest.json`` ``format`` 1 or 2)."""
    manifest = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") == 2:
        return Dataset2(root, **kwargs)
    return Dataset1(root, **kwargs)


Dataset = open_dataset
