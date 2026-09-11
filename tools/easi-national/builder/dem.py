"""USGS 3DEP elevation for the cross-section archive.

Three parts: the catalog of every 1 m project tile USGS stages on S3 (built
once from the bucket listings), windowed reads of those tiles by HTTP range
requests for a reach's buffer, and the 10 m seamless fallback through the
very py3dep call the live app makes. The selection rule is the app's own
(``threedep._best_available_dem``): 1 m when a project covers the buffer and
at least half of it comes back finite, else 10 m.

Nothing is stored here beyond the catalog: a reach's window is read, sampled
by ``stages/xs_sample.py`` and dropped; the archive keeps the samples and the
provenance (project, tiles, vintages) so the same window can be read again.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, http
from .paths import DataRoot, atomic_write_text
from .state import Control, Ledger, Progress

S3 = "https://prd-tnm.s3.amazonaws.com"
ONE_M_PREFIX = "StagedProducts/Elevation/1m/Projects/"
#: the 1/9 arc-second (about 3 m) product: one HFA raster per quarter-degree
#: quad, NAD83 geographic, its north-west corner in the name
NINTH_PREFIX = "StagedProducts/Elevation/19/TILES/"
QUAD_DEG = 0.25
QUAD_RES_DEG = 1.0 / 32400.0
QUAD_COLLAR_PX = 6
QUAD_RES_M = 3
SEAMLESS_VRT_URL = f"{S3}/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
CELL_M = 10_000
#: the app's rule: a 1 m raster that comes back mostly null falls back to 10 m
FINITE_MIN = 0.5
#: tiles whose name does not carry the 10 km cell: their headers are read one
#: by one, up to this many per project (beyond it the project is skipped)
MAX_HEADER_READS = 400
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.vrt,.img,.ige,.rrd",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_CACHEMAX": 512,                    # MB; rasterio wants an int here
    "VSI_CACHE": "TRUE",
}
_TILE_RE = re.compile(r"x(\d+)y(\d+)", re.IGNORECASE)
_QUAD_RE = re.compile(r"ned19_([ns])(\d{2})x(\d{2})_([ew])(\d{3})x(\d{2})", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
CATALOG_COLUMNS = ("project", "name", "url", "epsg", "res_m", "collar_m", "width", "height",
                   "block", "nodata", "minx", "miny", "maxx", "maxy", "west", "south", "east",
                   "north", "bytes", "last_modified", "year")


# ---------------------------------------------------------------- listings
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def s3_list(prefix: str, *, delimiter: Optional[str] = None, session=None) -> tuple[list[dict], list[str]]:
    """Every key (with size and last-modified) and every common prefix under
    ``prefix`` in the public 3DEP bucket (ListObjectsV2, paged)."""
    session = session or http.session()
    keys: list[dict] = []
    prefixes: list[str] = []
    token: Optional[str] = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        response = session.get(S3 + "/", params=params, timeout=60)
        response.raise_for_status()
        token = None
        for element in ET.fromstring(response.content):
            kind = _local(element.tag)
            if kind == "Contents":
                fields = {_local(child.tag): child.text for child in element}
                keys.append({"key": fields.get("Key") or "", "bytes": int(fields.get("Size") or 0),
                             "last_modified": fields.get("LastModified") or ""})
            elif kind == "CommonPrefixes":
                prefixes.extend(child.text for child in element if _local(child.tag) == "Prefix" and child.text)
            elif kind == "NextContinuationToken":
                token = element.text
        if not token:
            break
    return keys, prefixes


def list_projects(session=None) -> list[str]:
    _keys, prefixes = s3_list(ONE_M_PREFIX, delimiter="/", session=session)
    out = []
    for prefix in prefixes:
        name = prefix[len(ONE_M_PREFIX):].strip("/")
        if name:
            out.append(name)
    return sorted(out)


def parse_tile_name(name: str) -> Optional[tuple[int, int]]:
    """``USGS_1m_x27y430_<project>.tif`` -> (27, 430), the 10 km cell."""
    match = _TILE_RE.search(name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def project_year(project: str, last_modified: str = "") -> int:
    years = [int(y) for y in _YEAR_RE.findall(project)]
    if years:
        return max(years)
    try:
        return int(last_modified[:4])
    except ValueError:
        return 0


# ----------------------------------------------------------------- headers
def probe_tile(url: str) -> dict:
    """The header of one tile through a range request: CRS, size, block, nodata."""
    import rasterio
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open("/vsicurl/" + url if url.startswith("http") else url) as ds:
            epsg = ds.crs.to_epsg() if ds.crs else None
            return {"epsg": int(epsg) if epsg else None, "width": int(ds.width), "height": int(ds.height),
                    "res_m": float(ds.res[0]), "left": float(ds.bounds.left),
                    "bottom": float(ds.bounds.bottom), "right": float(ds.bounds.right),
                    "top": float(ds.bounds.top), "nodata": None if ds.nodata is None else float(ds.nodata),
                    "block": int(ds.block_shapes[0][0]) if ds.block_shapes else 0}


def cell_bounds(cell: tuple[int, int], collar_m: float) -> tuple[float, float, float, float]:
    """Projected bounds of a 10 km cell tile including its collar."""
    x, y = cell
    return (x * CELL_M - collar_m, (y - 1) * CELL_M - collar_m, (x + 1) * CELL_M + collar_m, y * CELL_M + collar_m)


def _to_4326(epsg: int, minx: float, miny: float, maxx: float, maxy: float) -> tuple[float, float, float, float]:
    from pyproj import Transformer
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    xs, ys = transformer.transform([minx, maxx, maxx, minx], [miny, miny, maxy, maxy])
    return (min(xs), min(ys), max(xs), max(ys))


def _name_rule_holds(header: dict, cell, collar: float) -> bool:
    if cell is None or collar < 0:
        return False
    expected = cell_bounds(cell, collar)
    actual = (header["left"], header["bottom"], header["right"], header["top"])
    return all(abs(a - b) <= 1.0 for a, b in zip(expected, actual))


def project_rows(project: str, *, session=None, probe=probe_tile, header_workers: int = 6) -> tuple[list[dict], str]:
    """Catalog rows for one project: its tile keys from the listing, bounds
    from the tile names when the headers of the tiles at the extremes of the
    name grid agree on one UTM zone and confirm the name rule; every header
    read otherwise (a project that spans two zones repeats cell names, so no
    name rule can place its tiles)."""
    keys, _prefixes = s3_list(f"{ONE_M_PREFIX}{project}/TIFF/", session=session)
    tiffs = [k for k in keys if k["key"].lower().endswith(".tif")]
    if not tiffs:
        return [], "no tif"
    named = [(parse_tile_name(k["key"].rsplit("/", 1)[-1]), k) for k in tiffs]
    cells = [(c, k) for c, k in named if c is not None]
    extremes = []
    if cells:
        by_x = sorted(cells, key=lambda item: (item[0][0], item[0][1]))
        picks = {0, len(by_x) // 2, len(by_x) - 1}
        extremes = [by_x[i] for i in sorted(picks)]
    else:
        extremes = [(None, tiffs[0])]
    headers = {k["key"]: probe(f"{S3}/{k['key']}") for _c, k in extremes}
    if any(not h.get("epsg") for h in headers.values()):
        return [], "no EPSG"
    zones = {h["epsg"] for h in headers.values()}
    first = headers[extremes[0][1]["key"]]
    res = first["res_m"]
    collar = round((first["width"] - CELL_M / res) / 2.0 * res, 3)
    rule_ok = (len(zones) == 1 and len(cells) == len(tiffs)
               and all(_name_rule_holds(headers[k["key"]], c, collar) for c, k in extremes))
    note = "name rule" if rule_ok else ("headers (two zones)" if len(zones) > 1 else "headers")
    if not rule_ok:
        from concurrent.futures import ThreadPoolExecutor
        pending = [k for k in tiffs if k["key"] not in headers]
        with ThreadPoolExecutor(max_workers=max(1, header_workers)) as pool:
            for entry, header in zip(pending, pool.map(lambda k: probe(f"{S3}/{k['key']}"), pending)):
                headers[entry["key"]] = header
    rows: list[dict] = []
    for entry in tiffs:
        name = entry["key"].rsplit("/", 1)[-1]
        url = f"{S3}/{entry['key']}"
        if rule_ok:
            cell = parse_tile_name(name)
            minx, miny, maxx, maxy = cell_bounds(cell, collar)
            header = first
        else:
            header = headers[entry["key"]]
            if not header.get("epsg"):
                continue
            minx, miny, maxx, maxy = header["left"], header["bottom"], header["right"], header["top"]
        west, south, east, north = _to_4326(header["epsg"], minx, miny, maxx, maxy)
        rows.append({"project": project, "name": name, "url": url, "epsg": header["epsg"],
                     "res_m": header["res_m"], "collar_m": collar, "width": header["width"],
                     "height": header["height"], "block": header["block"], "nodata": header["nodata"],
                     "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
                     "west": west, "south": south, "east": east, "north": north,
                     "bytes": entry["bytes"], "last_modified": entry["last_modified"],
                     "year": project_year(project, entry["last_modified"])})
    return rows, note


def _catalog_schema():
    import pyarrow as pa
    return pa.schema([
        ("project", pa.string()), ("name", pa.string()), ("url", pa.string()), ("epsg", pa.int32()),
        ("res_m", pa.float64()), ("collar_m", pa.float64()), ("width", pa.int32()), ("height", pa.int32()),
        ("block", pa.int32()), ("nodata", pa.float64()), ("minx", pa.float64()), ("miny", pa.float64()),
        ("maxx", pa.float64()), ("maxy", pa.float64()), ("west", pa.float64()), ("south", pa.float64()),
        ("east", pa.float64()), ("north", pa.float64()), ("bytes", pa.int64()),
        ("last_modified", pa.string()), ("year", pa.int32())])


def seamless_last_modified(session=None) -> str:
    try:
        response = (session or http.session()).head(SEAMLESS_VRT_URL, timeout=60)
        return response.headers.get("Last-Modified", "")
    except Exception:  # noqa: BLE001 - provenance only
        return ""


def build_catalog(root: DataRoot, progress: Progress, control: Control, *, workers: int = 4,
                  session=None, projects: Optional[list[str]] = None, rows_for=project_rows) -> Path:
    """``national/dem/1m/tiles.parquet`` + ``catalog.json`` from the bucket
    listings; resumable per project."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from .stages import common
    out_dir = root.dem1m
    out_dir.mkdir(parents=True, exist_ok=True)
    projects = list(projects if projects is not None else list_projects(session))
    ledger = Ledger(root, "dem1m-catalog")
    parts = out_dir / "parts"
    parts.mkdir(exist_ok=True)
    pending = [p for p in projects if p not in ledger]
    done = len(projects) - len(pending)
    progress.begin("national", "dem1m_index", total=len(projects),
                   message=f"3DEP 1 m catalog: {len(projects)} projects, {len(pending)} to list, {workers} at a time")
    progress.tick(done=done)
    notes: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(rows_for, project, session=session): project for project in pending}
        for future in as_completed(futures):
            control.check()
            project = futures[future]
            rows, note = future.result()
            if rows:
                pq.write_table(pa.Table.from_pylist(rows, schema=_catalog_schema()),
                               parts / f"{project}.parquet", compression="zstd")
            ledger.add(project, n=len(rows), note=note)
            if not rows:
                notes[project] = note
            done += 1
            progress.tick(done=done, message=f"3DEP 1 m catalog: {done} of {len(projects)} projects ({project}: "
                                             f"{len(rows)} tiles, {note})")
    tables = [pq.read_table(path) for path in sorted(parts.glob("*.parquet"))]
    table = pa.concat_tables(tables) if tables else pa.Table.from_pylist([], schema=_catalog_schema())
    table = table.sort_by([("project", "ascending"), ("name", "ascending")])
    common.write_parquet(table, root.dem1m_catalog)
    atomic_write_text(root.dem1m_meta, json.dumps({
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "projects": len(projects), "tiles": table.num_rows,
        "seamless_last_modified": seamless_last_modified(session),
        "skipped": notes}, indent=1, sort_keys=True))
    progress.say(f"3DEP 1 m catalog: {table.num_rows:,} tiles in {len(projects)} projects"
                 + (f", {len(notes)} projects skipped" if notes else ""))
    common.drop_parts(parts)
    ledger.clear()
    return root.dem1m_catalog


# ------------------------------------------------- the 1/9 arc-second quads
def parse_quad_name(name: str) -> Optional[tuple[float, float]]:
    """``ned19_n38x00_w075x25_va_...`` -> (top latitude 38.00, left longitude -75.25)."""
    match = _QUAD_RE.search(name)
    if not match:
        return None
    lat = int(match.group(2)) + int(match.group(3)) / 100.0
    lon = int(match.group(5)) + int(match.group(6)) / 100.0
    if match.group(1).lower() == "s":
        lat = -lat
    if match.group(4).lower() == "w":
        lon = -lon
    return lat, lon


def quad_bounds(name: str) -> Optional[tuple[float, float, float, float]]:
    """``(west, south, east, north)`` of a quad including its collar."""
    parsed = parse_quad_name(name)
    if parsed is None:
        return None
    top, left = parsed
    collar = QUAD_COLLAR_PX * QUAD_RES_DEG
    return (left - collar, top - QUAD_DEG - collar, left + QUAD_DEG + collar, top + collar)


def list_quads(session=None) -> list[str]:
    _keys, prefixes = s3_list(NINTH_PREFIX, delimiter="/", session=session)
    out = []
    for prefix in prefixes:
        name = prefix[len(NINTH_PREFIX):].strip("/")
        if name:
            out.append(name)
    return sorted(out)


def _quad_schema():
    import pyarrow as pa
    return pa.schema([("name", pa.string()), ("url", pa.string()), ("west", pa.float64()),
                      ("south", pa.float64()), ("east", pa.float64()), ("north", pa.float64()),
                      ("year", pa.int32())])


def build_catalog19(root: DataRoot, progress: Progress, control: Control, *, session=None,
                    quads: Optional[list[str]] = None) -> Path:
    """``national/dem/19/quads.parquet`` from the bucket listing alone."""
    import pyarrow as pa
    from .stages import common
    root.dem19.mkdir(parents=True, exist_ok=True)
    names = list(quads if quads is not None else list_quads(session))
    progress.begin("national", "dem19_index", total=1, message=f"3DEP 1/9 arc-second catalog: {len(names)} quads")
    control.check()
    rows = []
    for name in names:
        bounds = quad_bounds(name)
        if bounds is None:
            continue
        rows.append({"name": name, "url": f"{S3}/{NINTH_PREFIX}{name}/{name}.img", "west": bounds[0],
                     "south": bounds[1], "east": bounds[2], "north": bounds[3],
                     "year": project_year(name)})
    table = pa.Table.from_pylist(rows, schema=_quad_schema()).sort_by("name")
    common.write_parquet(table, root.dem19_catalog)
    atomic_write_text(root.dem19_meta, json.dumps({
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "quads": table.num_rows, "unparsed": len(names) - table.num_rows}, indent=1, sort_keys=True))
    progress.tick(done=1)
    progress.say(f"3DEP 1/9 arc-second catalog: {table.num_rows:,} quads")
    return root.dem19_catalog


_CATALOGS19: dict[str, "Catalog19"] = {}


class Catalog19:
    """The quarter-degree quads of the 1/9 arc-second product."""

    def __init__(self, table):
        import numpy as np
        self.n = table.num_rows
        self.name = np.array(table.column("name").to_pylist(), dtype=object)
        self.url = np.array(table.column("url").to_pylist(), dtype=object)
        self.year = np.asarray(table.column("year").to_pylist(), dtype="int64")
        for name in ("west", "south", "east", "north"):
            setattr(self, name, np.asarray(table.column(name).to_pylist(), dtype="float64"))

    @classmethod
    def load(cls, root: DataRoot) -> Optional["Catalog19"]:
        key = str(root.dem19_catalog)
        with _CATALOG_LOCK:
            hit = _CATALOGS19.get(key)
        if hit is not None:
            return hit
        if not root.dem19_catalog.exists():
            return None
        import pyarrow.parquet as pq
        catalog = cls(pq.read_table(root.dem19_catalog))
        with _CATALOG_LOCK:
            _CATALOGS19[key] = catalog
        return catalog

    def quads_for(self, bbox4326):
        import numpy as np
        west, south, east, north = [float(v) for v in bbox4326]
        hit = (self.east >= west) & (self.west <= east) & (self.north >= south) & (self.south <= north)
        return np.flatnonzero(hit)


# ----------------------------------------------------------------- catalog
_CATALOGS: dict[str, "Catalog"] = {}
_CATALOG_LOCK = threading.Lock()


class Catalog:
    """The 1 m tile catalog in memory: bbox queries in EPSG:4326."""

    def __init__(self, table):
        import numpy as np
        self.n = table.num_rows
        cols = {c: table.column(c) for c in table.column_names}
        self.project = np.array(cols["project"].to_pylist(), dtype=object)
        self.name = np.array(cols["name"].to_pylist(), dtype=object)
        self.url = np.array(cols["url"].to_pylist(), dtype=object)
        self.last_modified = np.array(cols["last_modified"].to_pylist(), dtype=object)
        self.epsg = np.asarray(cols["epsg"].to_pylist(), dtype="int64")
        self.year = np.asarray(cols["year"].to_pylist(), dtype="int64")
        for name in ("res_m", "collar_m", "minx", "miny", "maxx", "maxy", "west", "south", "east", "north"):
            setattr(self, name, np.asarray(cols[name].to_pylist(), dtype="float64"))
        self.width = np.asarray(cols["width"].to_pylist(), dtype="int64")
        self.height = np.asarray(cols["height"].to_pylist(), dtype="int64")
        self.block = np.asarray(cols["block"].to_pylist(), dtype="int64")
        self.nodata = np.asarray([float("nan") if v is None else v for v in cols["nodata"].to_pylist()],
                                 dtype="float64")
        self.bytes = np.asarray(cols["bytes"].to_pylist(), dtype="int64")
        px = np.maximum(self.width * self.height, 1)
        self.bytes_per_px = self.bytes / px

    @classmethod
    def load(cls, root: DataRoot) -> Optional["Catalog"]:
        key = str(root.dem1m_catalog)
        with _CATALOG_LOCK:
            hit = _CATALOGS.get(key)
        if hit is not None:
            return hit
        if not root.dem1m_catalog.exists():
            return None
        import pyarrow.parquet as pq
        catalog = cls(pq.read_table(root.dem1m_catalog))
        with _CATALOG_LOCK:
            _CATALOGS[key] = catalog
        return catalog

    def tiles_for(self, bbox4326, project: Optional[str] = None):
        import numpy as np
        west, south, east, north = [float(v) for v in bbox4326]
        hit = (self.east >= west) & (self.west <= east) & (self.north >= south) & (self.south <= north)
        if project is not None:
            hit &= self.project == project
        return np.flatnonzero(hit)

    def projects_for(self, bbox4326) -> list[str]:
        """Projects touching the box, newest first (year, then last-modified)."""
        idx = self.tiles_for(bbox4326)
        best: dict[str, tuple[int, str]] = {}
        for i in idx:
            project = str(self.project[i])
            key = (int(self.year[i]), str(self.last_modified[i]))
            if project not in best or key > best[project]:
                best[project] = key
        return [p for p, _k in sorted(best.items(), key=lambda item: item[1], reverse=True)]


# ------------------------------------------------------------ window reads
_OPEN: "OrderedDict[str, object]" = OrderedDict()
_OPEN_LOCK = threading.Lock()
OPEN_MAX = 48


def open_tile(url: str):
    """A rasterio dataset for the tile, cached per process (the header and
    block index are the expensive part of every range read)."""
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


def close_tiles() -> None:
    with _OPEN_LOCK:
        for ds in _OPEN.values():
            try:
                ds.close()
            except Exception:  # noqa: BLE001
                pass
        _OPEN.clear()


#: bytes on the wire against the block estimate below, measured on three
#: Rivanna reaches (2.3 to 3.0 MB received for estimates of 3.05 to 4.09 MB)
WIRE_FACTOR = 0.75


def window_bytes(catalog: Catalog, idx, bounds) -> int:
    """Estimated bytes a window read pulls: the tile blocks it touches, at
    the tile's compressed bytes per pixel, scaled to the measured wire cost."""
    minx, miny, maxx, maxy = bounds
    total = 0.0
    for i in idx:
        x0, x1 = max(minx, catalog.minx[i]), min(maxx, catalog.maxx[i])
        y0, y1 = max(miny, catalog.miny[i]), min(maxy, catalog.maxy[i])
        if x1 <= x0 or y1 <= y0:
            continue
        res = catalog.res_m[i] or 1.0
        block = int(catalog.block[i]) or 256
        px0 = (x0 - catalog.minx[i]) / res
        px1 = (x1 - catalog.minx[i]) / res
        py0 = (catalog.maxy[i] - y1) / res
        py1 = (catalog.maxy[i] - y0) / res
        nbx = math.ceil(px1 / block) - math.floor(px0 / block)
        nby = math.ceil(py1 / block) - math.floor(py0 / block)
        total += max(nbx, 1) * max(nby, 1) * block * block * catalog.bytes_per_px[i]
    return int(total * WIRE_FACTOR)


def merge_windows(urls: list[str], bounds):
    """``(array2d float32 with NaN, transform, crs)`` of the rasters over
    ``bounds`` (their own CRS), by range requests. The rasters of one product
    share one grid (the collars keep every origin on it), so the mosaic is a
    paste: the first raster with data wins a pixel. (rasterio's ``merge``
    zeroes the output for these files, so it is not used.)"""
    import math
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


def read_window(catalog: Catalog, idx, bounds):
    """The project tiles ``idx`` over ``bounds`` (project CRS)."""
    return merge_windows([str(catalog.url[i]) for i in idx], bounds)


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


def onemetre_dem(catalog: Catalog, buf4326, *, accounting=None) -> Optional[tuple]:
    """``(DataArray in the project CRS masked to the buffer, provenance)`` from
    the newest project that answers at least ``FINITE_MIN`` finite, else None."""
    import numpy as np
    bounds = buf4326.bounds
    for project in catalog.projects_for(bounds):
        idx = catalog.tiles_for(bounds, project=project)
        if not len(idx):
            continue
        epsg = int(catalog.epsg[idx[0]])
        poly = _project_polygon(buf4326, epsg)
        touching = [i for i in idx if catalog.maxx[i] >= poly.bounds[0] and catalog.minx[i] <= poly.bounds[2]
                    and catalog.maxy[i] >= poly.bounds[1] and catalog.miny[i] <= poly.bounds[3]]
        if not touching:
            continue
        if accounting is not None:
            accounting(window_bytes(catalog, touching, poly.bounds))
        array, transform, crs = read_window(catalog, touching, poly.bounds)
        da = as_dataarray(array, transform, crs)
        da = da.rio.clip([poly], crs=crs, drop=True, all_touched=True)
        finite = float(np.isfinite(np.asarray(da.values, dtype=float)).mean()) if da.size else 0.0
        if finite < FINITE_MIN:
            continue
        return da, {"source": "1m", "project": project, "epsg": epsg,
                    "tiles": [str(catalog.name[i]) for i in touching],
                    "last_modified": max(str(catalog.last_modified[i]) for i in touching),
                    "finite": round(finite, 3)}
    return None


def threemetre_dem(catalog19: Catalog19, buf4326, *, accounting=None) -> Optional[tuple]:
    """``(DataArray in NAD83 geographic masked to the buffer, provenance)``
    from the 1/9 arc-second quads, None when no quad covers the buffer or
    the data comes back mostly null."""
    import numpy as np
    bounds = buf4326.bounds
    idx = catalog19.quads_for(bounds)
    if not len(idx):
        return None
    if accounting is not None:
        # uncompressed HFA: four bytes a pixel, read in 64-pixel blocks
        px = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / (QUAD_RES_DEG ** 2)
        accounting(int(max(px * 1.6, 64 * 64) * 4))
    array, transform, crs = merge_windows([str(catalog19.url[i]) for i in idx], bounds)
    da = as_dataarray(array, transform, crs)
    da = da.rio.clip([buf4326], crs="EPSG:4326", drop=True, all_touched=True)
    finite = float(np.isfinite(np.asarray(da.values, dtype=float)).mean()) if da.size else 0.0
    if finite < FINITE_MIN:
        return None
    return da, {"source": "19", "quads": [str(catalog19.name[i]) for i in idx],
                "year": int(catalog19.year[idx].max()), "finite": round(finite, 3)}


_SEAMLESS_MODIFIED: dict[str, str] = {}


def tenmetre_dem(buf4326, *, accounting=None):
    """The live app's 10 m call: py3dep over the seamless 1/3 arc-second VRT."""
    import py3dep
    if accounting is not None:
        # a 10 m window: the seamless tiles are 512-pixel blocks of about 1 byte per pixel
        minx, miny, maxx, maxy = buf4326.bounds
        px = (maxx - minx) * (maxy - miny) / (9.2592600e-05 ** 2)
        accounting(int(max(px, 512 * 512) * 1.0))
    dem = py3dep.get_dem(buf4326, resolution=10)
    if "vintage" not in _SEAMLESS_MODIFIED:
        _SEAMLESS_MODIFIED["vintage"] = seamless_last_modified()
    return dem, {"source": "seamless13", "last_modified": _SEAMLESS_MODIFIED["vintage"]}


def best_available_dem(buf4326, *, catalog: Optional[Catalog] = None,
                       catalog19: Optional[Catalog19] = None, accounting=None):
    """``(DataArray in EPSG:5070, resolution_m, provenance)``: 1 m lidar
    where a project covers the buffer, else the 1/9 arc-second (3 m) quads,
    else the 10 m seamless. A failed read at one tier falls through to the
    next, noted in the provenance, never silently."""
    notes = []
    for label, fn, res in (("1m", lambda: onemetre_dem(catalog, buf4326, accounting=accounting), 1),
                           ("19", lambda: threemetre_dem(catalog19, buf4326, accounting=accounting), QUAD_RES_M)):
        if (label == "1m" and catalog is None) or (label == "19" and catalog19 is None):
            notes.append(f"{label}: no catalog")
            continue
        try:
            hit = fn()
        except Exception as exc:  # noqa: BLE001 - a broken read falls back like a failed WMS fetch
            notes.append(f"{label}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if hit is not None:
            da, provenance = hit
            if notes:
                provenance["note"] = "; ".join(notes)
            return da.rio.reproject(5070), res, provenance
    dem, provenance = tenmetre_dem(buf4326, accounting=accounting)
    if notes:
        provenance["note"] = "; ".join(notes)
    return dem.rio.reproject(5070), 10, provenance


# ----------------------------------------------------- the threedep seam
_INSTALLED: dict[str, object] = {}


def install(root: DataRoot) -> None:
    """Make ``easi.datasources.threedep.reach_geomorphology`` read elevation
    through this module (QA and equality tests); ``uninstall`` restores it."""
    from easi.datasources import threedep
    catalog = Catalog.load(root)
    catalog19 = Catalog19.load(root)
    if "original" not in _INSTALLED:
        _INSTALLED["original"] = threedep._best_available_dem

    def _dem(buf4326):
        dem, res, _provenance = best_available_dem(buf4326, catalog=catalog, catalog19=catalog19)
        return dem, res
    threedep._best_available_dem = _dem


def uninstall() -> None:
    from easi.datasources import threedep
    original = _INSTALLED.pop("original", None)
    if original is not None:
        threedep._best_available_dem = original
