"""Offline tests for the read grids (``xsgrid/origins.py``): a pull on a section's recorded grid
reproduces the build's group read bit for bit."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xsgrid import origins, sample  # noqa: E402
from xsgrid.engine import dem_tiles  # noqa: E402

EPSG = 26915


def _tile(path: Path, c: float, f: float, w: int, h: int, seed: int) -> str:
    """A 1 m float32 tile with a USGS-like origin a few micrometres off whole metres."""
    import rasterio
    from rasterio.transform import Affine
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    z = (300.0 + 0.031 * (c + xx) - 0.017 * (f - yy) + rng.normal(0, 0.05, (h, w))).astype(np.float32)
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
                       crs=f"EPSG:{EPSG}", transform=Affine(1.0, 0.0, c, 0.0, -1.0, f), nodata=-999999.0) as ds:
        ds.write(z, 1)
    return str(path)


class _Names(origins.Names):
    def __init__(self, paths: dict):                    # name -> path, no catalog
        self.one = dict((n, (p, EPSG)) for n, p in paths.items())
        self.nine = {}


def _to5070(u, v):
    from pyproj import Transformer
    x, y = Transformer.from_crs(EPSG, 5070, always_xy=True).transform(u, v)
    return float(x), float(y)


@pytest.fixture
def tiles(tmp_path):
    a = _tile(tmp_path / "A.tif", 500000.0000123, 4600060.0000456, 70, 60, 1)
    b = _tile(tmp_path / "B.tif", 500063.9999987, 4600060.0000321, 70, 60, 2)   # a 6 m collar over A
    return {"A.tif": a, "B.tif": b}


def test_a_pull_on_the_recorded_grid_is_the_group_read(tiles, tmp_path):
    names = _Names(tiles)
    grids = origins.TileGrids(tmp_path / "grids.json")
    # the section of interest crosses both tiles; another section of its group sits further west and
    # south, so the group's window starts elsewhere than the section's own would
    x0, y0 = _to5070(500066.3, 4600031.7)
    sec = sample.Section(i=0, x=x0, y=y0, nx=1.0, ny=0.0, wide=20.0)
    x1, y1 = _to5070(500012.6, 4600011.2)
    other = sample.Section(i=1, x=x1, y=y1, nx=0.0, ny=1.0, wide=9.0)
    n, n_other = sample.n_points(sec.wide, 1), sample.n_points(other.wide, 1)
    urls = [tiles["A.tif"], tiles["B.tif"]]
    bounds = origins._bounds([sec, other], [n, n_other], EPSG, origins.METRE_PAD)
    arr, transform, _crs = dem_tiles.merge_windows(urls, bounds)       # the build's group read
    u, v = (np.asarray(t) for t in sample.transformer(EPSG).transform(*sample.points5070(sec, n)))
    built = sample.bilinear(arr, transform, u, v).astype(np.float32)
    assert np.isfinite(built).all()
    corner = (EPSG,) + origins._corner(grids[urls[0]], bounds)
    pulled = origins.pull(sec, n, 1, "B.tif;A.tif", [corner], grids, names)
    assert np.array_equal(pulled.view(np.uint32), built.view(np.uint32))
    assert corner[1] == transform.c and corner[2] == transform.f


def test_the_build_read_and_the_sections_own_window_can_differ(tiles, tmp_path):
    """Why the grid is recorded: the same section read over its own window lands elsewhere on the
    micrometre-shifted tiles than in its group's window."""
    grids = origins.TileGrids(tmp_path / "grids.json")
    urls = [tiles["A.tif"], tiles["B.tif"]]
    x0, y0 = _to5070(500066.3, 4600031.7)
    sec = sample.Section(i=0, x=x0, y=y0, nx=1.0, ny=0.0, wide=20.0)
    n = sample.n_points(sec.wide, 1)
    own = origins._bounds([sec], [n], EPSG, origins.METRE_PAD)
    c_own = origins._corner(grids[urls[0]], own)
    x1, y1 = _to5070(500012.6, 4600011.2)
    other = sample.Section(i=1, x=x1, y=y1, nx=0.0, ny=1.0, wide=9.0)
    group = origins._bounds([sec, other], [n, sample.n_points(other.wide, 1)], EPSG, origins.METRE_PAD)
    c_group = origins._corner(grids[urls[0]], group)
    assert c_own[0] != c_group[0] or c_own[1] != c_group[1]


@pytest.mark.parametrize("epsg,pad", [(26915, origins.METRE_PAD), (26918, origins.METRE_PAD), (4269, origins.QUAD_PAD)])
def test_end_point_bounds_equal_every_point_bounds(epsg, pad):
    rng = np.random.default_rng(epsg)
    secs = []
    for i in range(300):
        angle = rng.uniform(0, 2 * np.pi) if i % 3 else rng.choice([0.0, np.pi / 2]) + rng.normal(0, 1e-7)
        secs.append(sample.Section(i=i, x=float(rng.uniform(-200_000, 1_900_000)), y=float(rng.uniform(1_500_000, 2_900_000)),
                                   nx=float(np.cos(angle)), ny=float(np.sin(angle)), wide=float(rng.choice([250.0, 319.76, 800.0]))))
    for group in (secs[:1], secs[:7], secs[100:300], secs):
        n = [sample.n_points(s.wide, 1) for s in group]
        assert origins._bounds(group, n, epsg, pad) == origins._bounds_all_points(group, n, epsg, pad)


def test_tile_grids_are_cached_and_merged_on_disk(tiles, tmp_path):
    path = tmp_path / "grids.json"
    one = origins.TileGrids(path)
    one.ensure([tiles["A.tif"]])
    two = origins.TileGrids(path)
    two.ensure([tiles["B.tif"]])
    again = origins.TileGrids(path)
    assert set(again.grids) == {tiles["A.tif"], tiles["B.tif"]}
    assert again.grids[tiles["A.tif"]][:4] == [500000.0000123, 4600060.0000456, 1.0, -1.0]


def test_paste_order_and_urls_follow_the_catalog_names():
    class N(origins.Names):
        def __init__(self):
            self.one = {"b.tif": ("https://x/b.tif", 26915), "a.tif": ("https://x/a.tif", 26914)}
            self.nine = {"q": "https://x/TILES/q/imgq_19.img"}
    assert N().urls(1, "b.tif;a.tif") == [("https://x/a.tif", 26914), ("https://x/b.tif", 26915)]
    assert N().urls(3, "q") == [("https://x/TILES/q/imgq_19.img", 4269)]
    assert N().urls(10, "USGS_13_n42w094.tif") == [
        ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n42w094/USGS_13_n42w094.tif", 4269)]
    assert N().urls(0, "") == []
