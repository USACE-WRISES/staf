"""USGS 3DEP elevation read straight from USGS's own tile files (2026-10).

USGS publishes the 3DEP 1 m project tiles and the 1/9 arc-second (about 3 m) quads in its public
bucket ``prd-tnm`` on Amazon S3 (The National Map's download area); STAF stores nothing there.
This module reads the window a reach's buffer needs from those files by HTTP range requests
(rasterio ``/vsicurl/``), with catalogs of every tile's footprint that the EASI national builder
writes (``tools/easi-national/builder/dem.py``, which this ports) and the STAF data bundle ships
(``tables/dem1m_tiles.parquet``, ``tables/dem19_quads.parquet``).

The rule is ``threedep._best_available_dem``'s: 1 m from the newest lidar project covering the
buffer when at least half of it comes back finite, else the 3 m quads under the same test, else
the caller's 10 m seamless DEM. Reading the files instead of asking the 3DEP dynamic service
keeps the 1 m answer from timing out to 10 m; the values are the native tiles', which differ from
the service's resampled mosaic by a few centimetres.

Off until the catalogs are found: ``set_catalog_folder``, else ``STAF_DEM_CATALOG``, else the
STAF data bundle's ``tables/`` when ``STAF_DATA_SOURCE`` is ``bundle`` or ``auto``: the folder
``STAF_DATA_BUNDLE`` names, or else the cache the bundle is delivered into (``STAF_DATA_CACHE``,
default the temp folder's ``staf_data_bundle``, where the site engine unpacks the release's core
on first use; a look before it arrives is not remembered). Never raises from ``best_tile_dem``:
any failure returns None and the caller's 10 m path runs.
"""
from __future__ import annotations

import math
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

#: a raster that comes back mostly null falls back to the next tier
FINITE_MIN = 0.5
QUAD_RES_M = 3
CATALOG_1M = "dem1m_tiles.parquet"
CATALOG_19 = "dem19_quads.parquet"
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.vrt,.img,.ige,.rrd",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_CACHEMAX": 512,
    "VSI_CACHE": "TRUE",
}

_lock = threading.Lock()
_state: dict = {"folder": None, "set": False}
_catalogs: dict = {}


def set_catalog_folder(folder) -> None:
    """Use the catalogs in ``folder`` (None: off), whatever the environment says."""
    with _lock:
        _state["folder"] = None if folder is None else Path(folder)
        _state["set"] = True
        _catalogs.clear()


def use_environment() -> None:
    """Forget ``set_catalog_folder``: the environment decides again."""
    with _lock:
        _state["folder"] = None
        _state["set"] = False
        _catalogs.clear()


def catalog_folder() -> Optional[Path]:
    if _state["set"]:
        return _state["folder"]
    env = os.environ.get("STAF_DEM_CATALOG")
    if env:
        return Path(env)
    source = (os.environ.get("STAF_DATA_SOURCE") or "").strip().lower()
    if source not in ("bundle", "auto"):
        return None
    bundle = os.environ.get("STAF_DATA_BUNDLE")
    if bundle:
        return Path(bundle) / "tables"
    # the delivered bundle (the site engine's ``delivery.default_cache``)
    cache = os.environ.get("STAF_DATA_CACHE") or Path(tempfile.gettempdir()) / "staf_data_bundle"
    return Path(cache) / "tables"


class Catalog:
    """The 1 m tile catalog in memory: footprint queries in EPSG:4326."""

    def __init__(self, table):
        import numpy as np
        cols = dict((c, table.column(c).to_pylist()) for c in table.column_names)
        self.project = np.array(cols["project"], dtype=object)
        self.name = np.array(cols["name"], dtype=object)
        self.url = np.array(cols["url"], dtype=object)
        self.last_modified = np.array(cols["last_modified"], dtype=object)
        self.epsg = np.asarray(cols["epsg"], dtype="int64")
        self.year = np.asarray(cols["year"], dtype="int64")
        for name in ("minx", "miny", "maxx", "maxy", "west", "south", "east", "north"):
            setattr(self, name, np.asarray(cols[name], dtype="float64"))

    def tiles_for(self, bbox4326, project: Optional[str] = None):
        import numpy as np
        west, south, east, north = [float(v) for v in bbox4326]
        hit = (self.east >= west) & (self.west <= east) & (self.north >= south) & (self.south <= north)
        if project is not None:
            hit &= self.project == project
        return np.flatnonzero(hit)

    def projects_for(self, bbox4326) -> list[str]:
        """Projects touching the box, newest first (year, then last modified)."""
        best: dict = {}
        for i in self.tiles_for(bbox4326):
            project = str(self.project[i])
            key = (int(self.year[i]), str(self.last_modified[i]))
            if project not in best or key > best[project]:
                best[project] = key
        return [p for p, _k in sorted(best.items(), key=lambda item: item[1], reverse=True)]


class Catalog19:
    """The quarter-degree quads of the 1/9 arc-second product."""

    def __init__(self, table):
        import numpy as np
        self.name = np.array(table.column("name").to_pylist(), dtype=object)
        self.url = np.array(table.column("url").to_pylist(), dtype=object)
        self.year = np.asarray(table.column("year").to_pylist(), dtype="int64")
        for name in ("west", "south", "east", "north"):
            setattr(self, name, np.asarray(table.column(name).to_pylist(), dtype="float64"))

    def quads_for(self, bbox4326):
        import numpy as np
        west, south, east, north = [float(v) for v in bbox4326]
        hit = (self.east >= west) & (self.west <= east) & (self.north >= south) & (self.south <= north)
        return np.flatnonzero(hit)


def catalogs() -> tuple:
    """``(Catalog or None, Catalog19 or None)`` from the catalog folder, read once."""
    folder = catalog_folder()
    if folder is None:
        return None, None
    with _lock:
        key = str(folder)
        if key not in _catalogs:
            import pyarrow.parquet as pq
            one = folder / CATALOG_1M
            nine = folder / CATALOG_19
            got = (Catalog(pq.read_table(one)) if one.exists() else None,
                   Catalog19(pq.read_table(nine)) if nine.exists() else None)
            if got == (None, None):
                return got                 # not there yet: a delivered core may still arrive
            _catalogs[key] = got
        return _catalogs[key]


# ------------------------------------------------------------------ window reads
_OPEN: "OrderedDict[str, object]" = OrderedDict()
_OPEN_LOCK = threading.Lock()
OPEN_MAX = 48


def open_tile(url: str):
    """A rasterio dataset for the tile, cached per process (the header and block index are the
    expensive part of every range read)."""
    import rasterio
    with _OPEN_LOCK:
        ds = _OPEN.get(url)
        if ds is not None:
            _OPEN.move_to_end(url)
            return ds
    with rasterio.Env(**GDAL_ENV):
        ds = rasterio.open("/vsicurl/" + url if url.startswith("http") else url)
    with _OPEN_LOCK:
        _OPEN[url] = ds
        while len(_OPEN) > OPEN_MAX:
            _key, old = _OPEN.popitem(last=False)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
    return ds


def merge_windows(urls: list, bounds):
    """``(array float32 with NaN, transform, crs)`` of the rasters over ``bounds`` (their CRS).
    The rasters of one product share one grid, so the mosaic is a paste: the first raster with
    data wins a pixel."""
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.windows import Window
    datasets = [open_tile(url) for url in urls]
    ref = datasets[0].transform
    a, e = ref.a, ref.e
    minx, miny, maxx, maxy = [float(v) for v in bounds]
    col0 = math.floor((minx - ref.c) / a)
    col1 = math.ceil((maxx - ref.c) / a)
    row0 = math.floor((maxy - ref.f) / e)
    row1 = math.ceil((miny - ref.f) / e)
    width, height = max(col1 - col0, 1), max(row1 - row0, 1)
    out = np.full((height, width), np.nan, dtype="float32")
    transform = Affine(a, 0.0, ref.c + col0 * a, 0.0, e, ref.f + row0 * e)
    with rasterio.Env(**GDAL_ENV):
        for ds in datasets:
            dc = int(round((ds.transform.c - transform.c) / a))
            dr = int(round((ds.transform.f - transform.f) / e))
            c0, c1 = max(0, dc), min(width, dc + ds.width)
            r0, r1 = max(0, dr), min(height, dr + ds.height)
            if c1 <= c0 or r1 <= r0:
                continue
            window = Window(col_off=c0 - dc, row_off=r0 - dr, width=c1 - c0, height=r1 - r0)
            data = np.asarray(ds.read(1, window=window), dtype="float32")
            mask = ~np.isfinite(data) | (data <= -1e30)      # every 3DEP nodata is a float32 minimum
            if ds.nodata is not None:
                mask |= data == np.float32(ds.nodata)
            region = out[r0:r1, c0:c1]
            take = np.isnan(region) & ~mask
            region[take] = data[take]
    return out, transform, datasets[0].crs


def as_dataarray(array, transform, crs):
    import numpy as np
    import rioxarray  # noqa: F401 - registers the rio accessor
    import xarray as xr
    height, width = array.shape
    xs = transform.c + (np.arange(width) + 0.5) * transform.a
    ys = transform.f + (np.arange(height) + 0.5) * transform.e
    da = xr.DataArray(array.astype("float32"), coords={"y": ys, "x": xs}, dims=("y", "x"), name="elevation")
    da = da.rio.write_crs(crs).rio.write_transform(transform)
    return da.rio.write_nodata(np.nan, encoded=False)


def _project_polygon(buf4326, epsg: int):
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return shp_transform(transformer.transform, buf4326)


def _finite(da) -> float:
    import numpy as np
    return float(np.isfinite(np.asarray(da.values, dtype=float)).mean()) if da.size else 0.0


def _zones(catalog: Catalog, idx, buf4326) -> list:
    """The project's tiles touching the buffer, grouped by CRS: ``[(epsg, buffer in that CRS,
    [tile index, ...]), ...]`` in catalog order. A project crossing a UTM zone line keeps each
    zone's tiles on that zone's grid, so each group is merged on its own."""
    out = []
    for epsg in dict.fromkeys(int(catalog.epsg[i]) for i in idx):
        poly = _project_polygon(buf4326, epsg)
        touching = [i for i in idx if int(catalog.epsg[i]) == epsg
                    and catalog.maxx[i] >= poly.bounds[0] and catalog.minx[i] <= poly.bounds[2]
                    and catalog.maxy[i] >= poly.bounds[1] and catalog.miny[i] <= poly.bounds[3]]
        if touching:
            out.append((epsg, poly, touching))
    return out


def _zone_dem(catalog: Catalog, poly, touching):
    """One zone's tiles merged over the buffer and clipped to it, in the zone's CRS."""
    array, transform, crs = merge_windows([str(catalog.url[i]) for i in touching], poly.bounds)
    return as_dataarray(array, transform, crs).rio.clip([poly], crs=crs, drop=True, all_touched=True)


def _combine_zones(das: list, buf4326):
    """One EPSG:5070 DataArray from each zone's clipped DEM: every zone reprojected onto the first
    zone's EPSG:5070 grid (its resolution and alignment, extended to cover them all), the first
    zone with data winning a cell, then clipped to the buffer."""
    import numpy as np
    from affine import Affine
    first = das[0].rio.reproject(5070)
    t0 = first.rio.transform()
    rx, ry = t0.a, t0.e                                      # ry < 0
    boxes = [da.rio.transform_bounds("EPSG:5070") for da in das]
    minx, miny = min(b[0] for b in boxes), min(b[1] for b in boxes)
    maxx, maxy = max(b[2] for b in boxes), max(b[3] for b in boxes)
    x0 = t0.c + math.floor((minx - t0.c) / rx) * rx         # the grid line at or left of the union
    y0 = t0.f - math.floor((t0.f - maxy) / -ry) * -ry       # the grid line at or above it
    width = max(int(math.ceil((maxx - x0) / rx)), 1)
    height = max(int(math.ceil((y0 - miny) / -ry)), 1)
    transform = Affine(rx, 0.0, x0, 0.0, ry, y0)
    out = None
    for da in das:
        grid = da.rio.reproject(5070, transform=transform, shape=(height, width))
        values = np.asarray(grid.values, dtype="float32")
        if out is None:
            out = values
        else:
            take = ~np.isfinite(out) & np.isfinite(values)
            out[take] = values[take]
    combined = as_dataarray(out, transform, "EPSG:5070")
    poly = _project_polygon(buf4326, 5070)
    return combined.rio.clip([poly], crs="EPSG:5070", drop=True, all_touched=True)


def onemetre_dem(catalog: Catalog, buf4326) -> Optional[tuple]:
    """``(DataArray clipped to the buffer, provenance)`` from the newest project that answers at
    least ``FINITE_MIN`` finite, else None. The DataArray is in the project's CRS, or in
    EPSG:5070 when the buffer reaches tiles of the project in more than one UTM zone (each zone
    merged on its own grid, then combined; provenance lists the zones)."""
    bounds = buf4326.bounds
    for project in catalog.projects_for(bounds):
        idx = catalog.tiles_for(bounds, project=project)
        if not len(idx):
            continue
        zones = _zones(catalog, idx, buf4326)
        if not zones:
            continue
        if len(zones) == 1:
            _epsg, poly, touching = zones[0]
            da = _zone_dem(catalog, poly, touching)
        else:
            da = _combine_zones([_zone_dem(catalog, poly, touching) for _e, poly, touching in zones], buf4326)
        finite = _finite(da)
        if finite < FINITE_MIN:
            continue
        provenance = {"source": "1m", "project": project,
                      "tiles": [str(catalog.name[i]) for _e, _p, touching in zones for i in touching],
                      "finite": round(finite, 3)}
        if len(zones) > 1:
            provenance["zones"] = [epsg for epsg, _p, _t in zones]
        return da, provenance
    return None


def threemetre_dem(catalog19: Catalog19, buf4326) -> Optional[tuple]:
    """``(DataArray clipped to the buffer, provenance)`` from the 1/9 arc-second quads, else None."""
    bounds = buf4326.bounds
    idx = catalog19.quads_for(bounds)
    if not len(idx):
        return None
    array, transform, crs = merge_windows([str(catalog19.url[i]) for i in idx], bounds)
    da = as_dataarray(array, transform, crs).rio.clip([buf4326], crs="EPSG:4326", drop=True, all_touched=True)
    finite = _finite(da)
    if finite < FINITE_MIN:
        return None
    return da, {"source": "19", "quads": [str(catalog19.name[i]) for i in idx], "finite": round(finite, 3)}


def best_tile_dem(buf4326) -> Optional[tuple]:
    """``(DataArray in EPSG:5070, resolution_m, provenance)`` from the 1 m tiles, else the 3 m
    quads; None when the catalogs are absent, nothing covers the buffer, or a read fails (the
    caller's 10 m path then runs, as it does when the dynamic service fails)."""
    try:
        one, nine = catalogs()
    except Exception:  # noqa: BLE001 - an unreadable catalog means no tiles
        return None
    for catalog, fn, res in ((one, onemetre_dem, 1), (nine, threemetre_dem, QUAD_RES_M)):
        if catalog is None:
            continue
        try:
            hit = fn(catalog, buf4326)
        except Exception:  # noqa: BLE001 - a broken read falls through to the next tier
            continue
        if hit is not None:
            da, provenance = hit
            if da.rio.crs is not None and da.rio.crs.to_epsg() == 5070:    # zones already combined
                return da, res, provenance
            return da.rio.reproject(5070), res, provenance
    return None
