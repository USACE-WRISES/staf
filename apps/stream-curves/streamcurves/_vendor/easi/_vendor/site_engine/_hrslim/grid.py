"""The elevation grids NHDPlus catchments were cut from.

NHDPlus catchments are raster polygons: every vertex of an NHDPlus HR catchment
sits on the 10 m grid of EPSG:5070 with corners at 5 m past each 10 m line (all
lower-48 packages checked so far), Alaska's on a 5 m grid of EPSG:3338, and the
NHDPlus V2 catchments on the 30 m grid of EPSG:5070 at 15 m. Edges run along the
grid lines. Stored as whole grid cells, a catchment loses nothing.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

_lock = threading.Lock()
_transformers: dict = {}


def _transformer(src, dst):
    from pyproj import Transformer
    with _lock:
        key = (src, dst)
        if key not in _transformers:
            _transformers[key] = Transformer.from_crs(src, dst, always_xy=True)
        return _transformers[key]


@dataclass(frozen=True)
class Grid:
    """A square grid: cell ``(ix, iy)`` has its corner at ``(ix * cell + offset,
    iy * cell + offset)`` in ``crs`` (metres)."""

    crs: int
    cell: float
    offset: float

    def to_dict(self) -> dict:
        return {"crs": self.crs, "cell": self.cell, "offset": self.offset}

    @classmethod
    def from_dict(cls, d: dict) -> "Grid":
        return cls(int(d["crs"]), float(d["cell"]), float(d["offset"]))

    @property
    def cell_area_m2(self) -> float:
        return self.cell * self.cell

    def project(self, lon, lat) -> tuple[np.ndarray, np.ndarray]:
        x, y = _transformer(4269, self.crs).transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(x, float), np.asarray(y, float)

    def unproject(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        lon, lat = _transformer(self.crs, 4269).transform(np.asarray(x, float), np.asarray(y, float))
        return np.asarray(lon, float), np.asarray(lat, float)

    def to_cells(self, lon, lat) -> tuple[np.ndarray, np.ndarray, float]:
        """Whole cells and the largest distance (m) a vertex had to move."""
        x, y = self.project(lon, lat)
        fx, fy = (x - self.offset) / self.cell, (y - self.offset) / self.cell
        ix, iy = np.round(fx), np.round(fy)
        moved = 0.0
        if ix.size:
            moved = float(max(np.abs(fx - ix).max(), np.abs(fy - iy).max()) * self.cell)
        return ix.astype(np.int64), iy.astype(np.int64), moved

    def cells_to_xy(self, ix, iy) -> tuple[np.ndarray, np.ndarray]:
        return (np.asarray(ix, float) * self.cell + self.offset,
                np.asarray(iy, float) * self.cell + self.offset)

    def to_lonlat(self, ix, iy) -> tuple[np.ndarray, np.ndarray]:
        return self.unproject(*self.cells_to_xy(ix, iy))


#: Grids found in the USGS packages, tried in order.
CANDIDATES = (Grid(5070, 10.0, 5.0), Grid(3338, 5.0, 0.0), Grid(5070, 30.0, 15.0))
#: A grid fits when every sampled vertex is within this distance of a node.
FIT_TOLERANCE_M = 0.01


def detect(lon, lat, *, sample: int = 200_000, seed: int = 0) -> Optional[Grid]:
    """The coarsest candidate grid every sampled vertex sits on, or None (a 30 m
    grid also fits a 10 m test, so the coarsest fit is the source grid)."""
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    if lon.size == 0:
        return None
    if lon.size > sample:
        pick = np.random.default_rng(seed).choice(lon.size, size=sample, replace=False)
        lon, lat = lon[pick], lat[pick]
    fits = [g for g in CANDIDATES if g.to_cells(lon, lat)[2] <= FIT_TOLERANCE_M]
    return max(fits, key=lambda g: g.cell) if fits else None
