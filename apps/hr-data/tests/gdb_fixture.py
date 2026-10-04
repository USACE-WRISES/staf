"""A tiny USGS-style package for the direct-access tests: VPU 9901 from
``fixture`` as a zipped File Geodatabase with the USGS layer and field names
(``NHDFlowline``, ``NHDPlusFlowlineVAA``, ``NHDPlusCatchment``, CamelCase fields).

It holds reaches 1, 2, 3, 4 and 6; reach 5 lives in VPU 9902, so a walk inside
this one package stops at reach 4. ``SIDE_DITCH`` is an off-network line next to
reach 3 that the nearest-stream search must skip.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pyogrio.raw
import shapely

import fixture

NAME = "NHDPLUS_H_9901_HU4_GDB"
X0, Y0, D = fixture.X0, fixture.Y0, fixture.D
REACHES = [1, 2, 3, 4, 6]
SIDE_DITCH_ID = 99019999999999
SIDE_DITCH = shapely.LineString([(X0 + 1.65 * D, Y0 + 0.1 * D), (X0 + 1.65 * D, Y0 + 0.9 * D)])
#: A point 26 m from the side ditch and about 100 m from reach 3's end.
NEAR_REACH_3 = (X0 + 1.62 * D, Y0 + 0.5 * D)
#: Published catchment area stored for every reach (any value; the tests read it back).
AREA_SQKM = 0.95


def build(root: Path) -> dict:
    """Write the package under ``root``; returns its ``direct`` package record."""
    root.mkdir(parents=True, exist_ok=True)
    gdb = root / (NAME + ".gdb")
    r = fixture.REACHES
    ids = np.array([fixture.ID[n] for n in REACHES], dtype="float64")
    n = len(REACHES)
    pyogrio.raw.write(
        str(gdb), shapely.to_wkb([fixture._geom(k) for k in REACHES] + [SIDE_DITCH]),
        [np.append(ids, float(SIDE_DITCH_ID)),
         np.array([r[k][4] for k in REACHES] + ["Side Ditch"], dtype=object),
         np.array([f"0206000500{k:04d}" for k in REACHES] + ["02060005009999"], dtype=object),
         np.array([46006] * n + [33600], dtype=np.int32),
         np.full(n + 1, 0.9),
         np.array([1] * n + [0], dtype=np.int32)],
        ["NHDPlusID", "GNIS_Name", "ReachCode", "FCode", "LengthKM", "InNetwork"],
        layer="NHDFlowline", driver="OpenFileGDB", geometry_type="MultiLineString",
        promote_to_multi=True, crs="EPSG:4269")
    pyogrio.raw.write(
        str(gdb), None,
        [ids, np.array([r[k][1] for k in REACHES], dtype="float64"),
         np.array([r[k][2] for k in REACHES], dtype="float64"),
         np.array([r[k][5] for k in REACHES], dtype="float64"),
         np.ones(n, dtype=np.int32)],
        ["NHDPlusID", "HydroSeq", "DnHydroSeq", "TotDASqKm", "StreamOrde"],
        layer="NHDPlusFlowlineVAA", driver="OpenFileGDB", geometry_type=None)
    pyogrio.raw.write(
        str(gdb), shapely.to_wkb([fixture._square(*r[k][0]) for k in REACHES]),
        [ids, np.full(n, AREA_SQKM)], ["NHDPlusID", "AreaSqKm"],
        layer="NHDPlusCatchment", driver="OpenFileGDB", geometry_type="MultiPolygon",
        promote_to_multi=True, crs="EPSG:4269")
    zpath = root / (NAME + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gdb.rglob("*")):
            z.write(f, f.relative_to(root).as_posix())
    return {"vpu": "9901", "name": zpath.name, "url": str(zpath), "bytes": zpath.stat().st_size}
