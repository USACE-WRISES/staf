"""Source data for the precomputed values (bundle v2, Phase 3).

Downloads, once, into ``<sources>/`` (``D:\\Data\\nhdplus-hr\\sources``,
``HR_SOURCES``) the public data the site engine and the apps read live today,
and records what was fetched (URL, bytes, date, vintage) in ``sources.json``:

- ``nlcd``: legacy NLCD 2021 land cover, 2021 impervious and 2001 impervious
  (the engine's vintages; the 2021 release left 2001 unchanged) as native 30 m
  EPSG:5070 tiles from MRLC's public WCS, only the tiles the regions touch;
- ``nid``: the USACE National Inventory of Dams national GeoPackage;
- ``tiger``: Census TIGER/Line 2025 roads for every county a region touches;
- ``wbd``: the USGS Watershed Boundary Dataset national geodatabase (current
  HUC12s; the apps query the live WBD today);
- ``nwi``: USFWS National Wetlands Inventory state geodatabases;
- ``gnatsgo``: USDA gNATSGO (July 2020) 10 m map-unit tiles on the catchments'
  own grid, read window by window from Microsoft Planetary Computer, and its
  component, horizon, map-unit and legend tables.

Regions are the version 2 pilot's (outlines from ``hrslim/vpu_index.geojson``).
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import requests
import shapely

from . import HR_DATA_APP, REPO_ROOT

SOURCES_DIR = Path(os.environ.get("HR_SOURCES") or r"D:\Data\nhdplus-hr\sources")
UA = {"User-Agent": "STAF hr-slim builder (bundle v2 precompute)"}
_lock = threading.Lock()

# --------------------------------------------------------------------------- #
# NLCD on MRLC's WCS: the native grid, EPSG:5070, 30 m, corner at 15 mod 30
# --------------------------------------------------------------------------- #
NLCD_WCS = "https://www.mrlc.gov/geoserver/mrlc_download/wcs"
NLCD_LAYERS = {
    "lc2021": "mrlc_download__NLCD_2021_Land_Cover_L48",
    "imp2021": "mrlc_download__NLCD_2021_Impervious_L48",
    "imp2001": "mrlc_download__NLCD_2001_Impervious_L48",
}
NLCD_X0, NLCD_Y0, NLCD_CELL = -2493045.0, 3310005.0, 30.0
#: the far corner of NLCD's lower-48 raster (EPSG:5070): no tile beyond it exists (the service answers 404)
NLCD_X1, NLCD_Y1 = 2342655.0, 177285.0
NLCD_TILE = 2048                                   # cells per tile side (61.44 km)

NID_URL = "https://nid.sec.usace.army.mil/api/nation/gpkg"
TIGER_YEAR = 2025
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER{year}/ROADS/tl_{year}_{geoid}_roads.zip"
TIGERWEB_COUNTIES = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                     "State_County/MapServer/1/query")
WBD_URL = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/National/GDB/"
           "WBD_National_GDB.zip")
NWI_URL = "https://documentst.ecosphere.fws.gov/wetlands/data/State-Downloads/{st}_geodatabase_wetlands.zip"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"   # or account/container
GNATSGO_TABLES = ("component", "chorizon", "mapunit", "legend")


def log_default(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


# --------------------------------------------------------------------------- #
# bookkeeping
# --------------------------------------------------------------------------- #
def record(name: str, info: dict, root: Path = SOURCES_DIR) -> None:
    """Merge ``info`` into ``sources.json`` under ``name``."""
    path = root / "sources.json"
    with _lock:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[name] = dict(data.get(name, {}), **info,
                          recorded=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def fetch_url(url: str, dest: Path, *, size: Optional[int] = None, log: Callable = log_default,
              retries: int = 5, timeout: int = 120) -> Path:
    """``url`` saved at ``dest``, resuming a partial ``.part`` with HTTP ranges;
    when ``size`` is known the file must end at exactly that many bytes."""
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = dict(UA, Range=f"bytes={have}-") if have else dict(UA)
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
                if r.status_code == 416 and size is not None and have == size:
                    break
                if r.status_code == 200 and have:
                    have = 0
                elif r.status_code not in (200, 206):
                    r.raise_for_status()
                with open(part, "ab" if have else "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except (requests.RequestException, OSError) as exc:
            log(f"{dest.name}: attempt {attempt} stopped: {exc}")
            time.sleep(min(60, 5 * attempt))
            continue
        got = part.stat().st_size
        if size is None or got == size:
            break
        log(f"{dest.name}: attempt {attempt} ended at {got} of {size} bytes")
    else:
        raise RuntimeError(f"could not download {url}")
    part.replace(dest)
    return dest


def remote_size(url: str) -> Optional[int]:
    try:
        with requests.get(url, headers=dict(UA, Range="bytes=0-0"), stream=True, timeout=60) as r:
            cr = r.headers.get("Content-Range")
            return int(cr.split("/")[-1]) if cr else None
    except (requests.RequestException, ValueError):
        return None


# --------------------------------------------------------------------------- #
# regions
# --------------------------------------------------------------------------- #
def region_outlines(vpus: list[str]) -> dict:
    """``{vpu: polygon in lon/lat}`` from the data app's region index."""
    idx = json.loads((HR_DATA_APP / "hrslim" / "vpu_index.geojson").read_text(encoding="utf-8"))
    by = dict((str(f["properties"].get("vpu") or f["properties"].get("huc4")),
               shapely.geometry.shape(f["geometry"])) for f in idx["features"])
    out = {}
    for v in vpus:
        g = by.get(v) or by.get(v[:4])
        if g is None:
            raise KeyError(f"no outline for region {v}")
        out[v] = g
    return out


def to_5070(geom):
    from pyproj import Transformer
    t = Transformer.from_crs(4269, 5070, always_xy=True)
    return shapely.transform(geom, lambda q: np.column_stack(t.transform(q[:, 0], q[:, 1])))


def lower48(vpus: list[str]) -> list[str]:
    """The regions of hydrologic regions 01 to 18 (19 Alaska, 20 Hawaii, 21 the Caribbean and 22 the
    Pacific islands are not)."""
    return [v for v in vpus if v[:2].isdigit() and 1 <= int(v[:2]) <= 18]


# --------------------------------------------------------------------------- #
# NLCD
# --------------------------------------------------------------------------- #
def nlcd_tile_box(i: int, j: int) -> tuple[float, float, float, float]:
    side = NLCD_TILE * NLCD_CELL
    x0 = NLCD_X0 + i * side
    y1 = NLCD_Y0 - j * side
    return x0, y1 - side, x0 + side, y1


def nlcd_tile_inside(i: int, j: int) -> bool:
    """Whether the tile overlaps NLCD's lower-48 raster (a region crossing into Canada or Mexico
    touches tiles beyond it, where every cell is outside NLCD's footprint)."""
    x0, y0, x1, y1 = nlcd_tile_box(i, j)
    return x1 > NLCD_X0 and x0 < NLCD_X1 and y1 > NLCD_Y1 and y0 < NLCD_Y0


def nlcd_tiles(vpus: list[str], buffer_m: float = 1000.0) -> list[tuple[int, int]]:
    """Tiles (column, row from the grid's top-left corner) the regions touch inside NLCD's raster."""
    side = NLCD_TILE * NLCD_CELL
    want = set()
    for vpu, g in region_outlines(lower48(vpus)).items():
        p = to_5070(g).buffer(buffer_m)
        x0, y0, x1, y1 = p.bounds
        for i in range(int((x0 - NLCD_X0) // side), int((x1 - NLCD_X0) // side) + 1):
            for j in range(int((NLCD_Y0 - y1) // side), int((NLCD_Y0 - y0) // side) + 1):
                if nlcd_tile_inside(i, j) and p.intersects(shapely.box(*nlcd_tile_box(i, j))):
                    want.add((i, j))
    return sorted(want)


def nlcd_tile_path(layer: str, i: int, j: int, root: Path = SOURCES_DIR) -> Path:
    return root / "nlcd" / layer / f"{i:03d}_{j:03d}.tif"


def _nlcd_one(layer: str, i: int, j: int, root: Path, log: Callable) -> int:
    import io

    import rasterio
    dest = nlcd_tile_path(layer, i, j, root)
    if dest.exists():
        return 0
    x0, y0, x1, y1 = nlcd_tile_box(i, j)
    params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
              ("coverageId", NLCD_LAYERS[layer]), ("subset", f"X({x0:.0f},{x1:.0f})"),
              ("subset", f"Y({y0:.0f},{y1:.0f})"), ("format", "image/tiff")]
    for attempt in range(1, 6):
        try:
            r = requests.get(NLCD_WCS, params=params, headers=UA, timeout=600)
            if r.ok and r.content[:2] in (b"II", b"MM"):
                break
            log(f"nlcd {layer} {i},{j}: HTTP {r.status_code} {r.text[:120]!r}")
        except requests.RequestException as exc:
            log(f"nlcd {layer} {i},{j}: attempt {attempt}: {exc}")
        time.sleep(10 * attempt)
    else:
        raise RuntimeError(f"nlcd tile {layer} {i},{j} failed")
    with rasterio.open(io.BytesIO(r.content)) as src:
        a = src.read(1)
        prof = src.profile
        if src.crs is None or src.crs.to_epsg() != 5070 or abs(src.transform.a - NLCD_CELL) > 1e-6:
            raise RuntimeError(f"nlcd tile {layer} {i},{j}: unexpected grid {src.crs} {src.transform}")
        if (abs((src.transform.c - NLCD_X0) % NLCD_CELL) > 1e-6
                or abs((NLCD_Y0 - src.transform.f) % NLCD_CELL) > 1e-6):
            raise RuntimeError(f"nlcd tile {layer} {i},{j}: off the NLCD grid {src.transform}")
    prof.update(driver="GTiff", compress="deflate", predictor=2, tiled=True, blockxsize=512, blockysize=512)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with rasterio.open(tmp, "w", **prof) as out:
        out.write(a, 1)
    tmp.replace(dest)
    return len(r.content)


def nlcd(vpus: list[str], root: Path = SOURCES_DIR, workers: int = 4, log: Callable = log_default) -> dict:
    tiles = nlcd_tiles(vpus)
    jobs = [(layer, i, j) for layer in NLCD_LAYERS for i, j in tiles]
    todo = [jb for jb in jobs if not nlcd_tile_path(*jb, root=root).exists()]
    log(f"nlcd: {len(tiles)} tiles x {len(NLCD_LAYERS)} layers, {len(todo)} to fetch")
    total, done = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_nlcd_one, *jb, root, log) for jb in todo]
        for f in as_completed(futs):
            total += f.result()
            done += 1
            if done % 25 == 0 or done == len(todo):
                log(f"nlcd: {done}/{len(todo)} tiles, {total / 1e6:,.0f} MB received")
    record("nlcd", {"service": NLCD_WCS, "layers": NLCD_LAYERS, "tiles": len(tiles), "tile_cells": NLCD_TILE,
                    "grid": {"crs": 5070, "cell": NLCD_CELL, "x0": NLCD_X0, "y0": NLCD_Y0},
                    "vintage": "legacy NLCD 2021 release (land cover and impervious 2021; impervious 2001 "
                               "unchanged from the 2019 release)",
                    "regions": vpus}, root)
    return {"tiles": len(tiles), "bytes": total}


# --------------------------------------------------------------------------- #
# NID, WBD
# --------------------------------------------------------------------------- #
def nid(root: Path = SOURCES_DIR, log: Callable = log_default) -> Path:
    stamp = datetime.now().strftime("%Y%m%d")
    existing = sorted((root / "nid").glob("nid_nation_*.gpkg"))
    if existing:
        return existing[-1]
    dest = fetch_url(NID_URL, root / "nid" / f"nid_nation_{stamp}.gpkg", log=log)
    record("nid", {"url": NID_URL, "file": dest.name, "bytes": dest.stat().st_size,
                   "vintage": f"NID national export downloaded {stamp}"}, root)
    log(f"nid: {dest.stat().st_size / 1e6:.1f} MB")
    return dest


NID_FS_URL = ("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
              "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")
NID_FS_FIELDS = "OBJECTID,NIDID,NAME,NID_STORAGE,NORMAL_STORAGE,DAM_HEIGHT,NID_HEIGHT,STATE,RIVER_OR_STREAM"


def nid_featureserver(root: Path = SOURCES_DIR, log: Callable = log_default) -> Path:
    """Every dam from the USACE NID FeatureServer the site engine and the apps query, with the
    fields they read and the EPSG:4326 coordinates they ask for (``outSR=4326``), paged by OBJECTID.
    The national GeoPackage export places dams up to about a metre apart from this service."""
    import requests
    import pyarrow as pa
    import pyarrow.parquet as pq
    stamp = datetime.now().strftime("%Y%m%d")
    dest = root / "nid" / f"nid_fs_{stamp}.parquet"
    if dest.exists():
        return dest
    rows, last = [], -1
    while True:
        params = {"where": f"OBJECTID > {last}", "outFields": NID_FS_FIELDS, "orderByFields": "OBJECTID",
                  "returnGeometry": "true", "outSR": "4326", "resultRecordCount": 2000, "f": "json"}
        for attempt in range(5):
            try:
                r = requests.get(NID_FS_URL, params=params, timeout=180)
                data = r.json()
                if r.status_code == 200 and "error" not in data:
                    break
            except Exception:  # noqa: BLE001 - retried below
                data = None
            time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"NID FeatureServer page after OBJECTID {last} failed")
        feats = data.get("features") or []
        if not feats:
            break
        for f in feats:
            a, g = f.get("attributes") or {}, f.get("geometry") or {}
            rows.append((a.get("OBJECTID"), a.get("NIDID"), a.get("NAME"), a.get("NID_STORAGE"), a.get("NORMAL_STORAGE"),
                         a.get("DAM_HEIGHT"), a.get("NID_HEIGHT"), a.get("STATE"), a.get("RIVER_OR_STREAM"),
                         g.get("x"), g.get("y")))
        last = max(int(f["attributes"]["OBJECTID"]) for f in feats)
        if len(rows) % 20000 < len(feats):
            log(f"nid FeatureServer: {len(rows):,} dams")
    names = ("objectid", "nid_id", "name", "nid_storage_acft", "normal_storage_acft", "dam_height_ft", "nid_height_ft",
             "state", "river", "lon", "lat")
    types = (pa.int64(), pa.string(), pa.string(), pa.float64(), pa.float64(), pa.float64(), pa.float64(), pa.string(),
             pa.string(), pa.float64(), pa.float64())
    tab = pa.table(dict((n, pa.array([r[i] for r in rows], type=t)) for i, (n, t) in enumerate(zip(names, types))))
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(tab, dest, compression="zstd", compression_level=19)
    record("nid_featureserver", {"url": NID_FS_URL, "file": dest.name, "dams": tab.num_rows,
                                 "bytes": dest.stat().st_size, "fields": NID_FS_FIELDS, "out_sr": 4326,
                                 "vintage": f"NID FeatureServer read {stamp}"}, root)
    log(f"nid FeatureServer: {tab.num_rows:,} dams, {dest.stat().st_size / 1e6:.1f} MB")
    return dest


def latest_nid_fs(root: Path = SOURCES_DIR) -> Path:
    found = sorted((root / "nid").glob("nid_fs_*.parquet"))
    if not found:
        raise FileNotFoundError("no NID FeatureServer pull: run `sources nid-fs`")
    return found[-1]


def wbd(root: Path = SOURCES_DIR, log: Callable = log_default) -> Path:
    size = remote_size(WBD_URL)
    dest = fetch_url(WBD_URL, root / "wbd" / "WBD_National_GDB.zip", size=size, log=log, timeout=300)
    record("wbd", {"url": WBD_URL, "file": dest.name, "bytes": dest.stat().st_size,
                   "vintage": f"WBD national geodatabase downloaded {datetime.now():%Y-%m-%d}"}, root)
    log(f"wbd: {dest.stat().st_size / 1e6:,.0f} MB")
    return dest


# --------------------------------------------------------------------------- #
# TIGER roads
# --------------------------------------------------------------------------- #
def counties(vpus: list[str]) -> list[str]:
    """County GEOIDs (state + county FIPS) each region touches, from TIGERweb."""
    out = set()
    for vpu, g in region_outlines(vpus).items():
        s = g.buffer(0.05).simplify(0.01)          # edge catchments reach past the outline
        polys = [s] if s.geom_type == "Polygon" else list(s.geoms)
        rings = [list(map(list, p.exterior.coords)) for p in polys]
        body = dict(geometry=json.dumps(dict(rings=rings, spatialReference=dict(wkid=4326))),
                    geometryType="esriGeometryPolygon", inSR="4326", spatialRel="esriSpatialRelIntersects",
                    outFields="GEOID", returnGeometry="false", f="json")
        r = requests.post(TIGERWEB_COUNTIES, data=body, headers=UA, timeout=180)
        r.raise_for_status()
        out.update(f["attributes"]["GEOID"] for f in r.json().get("features", []))
    return sorted(out)


def _is_zip(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(4) == b"PK\x03\x04"


def tiger_county_zip(geoid: str, root: Path = SOURCES_DIR, log: Callable = log_default) -> tuple[Path, int]:
    """The county's TIGER/Line roads zip and its year. The Census server answers a few county files
    with a firewall rejection page whatever the request (2025: 36059 and 48001); such a county takes
    the year before's file. A rejection page is deleted, so a later run asks again."""
    for year in (TIGER_YEAR, TIGER_YEAR - 1):
        z = root / "tiger" / str(year) / f"tl_{year}_{geoid}_roads.zip"
        if not z.exists():
            url = TIGER_URL.format(year=year, geoid=geoid)
            fetch_url(url, z, size=remote_size(url), log=log)
        if _is_zip(z):
            return z, year
        log(f"TIGER/Line {year} roads for county {geoid}: the server sent a rejection page, not the zip")
        z.unlink()
    raise RuntimeError(f"no TIGER/Line roads for county {geoid}")


def tiger(vpus: list[str], root: Path = SOURCES_DIR, workers: int = 4, log: Callable = log_default) -> dict:
    geoids = counties(vpus)
    folder = root / "tiger" / str(TIGER_YEAR)
    log(f"tiger: {len(geoids)} counties")

    def one(geoid):
        dest, year = tiger_county_zip(geoid, root, log)
        return dest.stat().st_size, geoid, year

    with ThreadPoolExecutor(max_workers=workers) as pool:
        got = list(pool.map(one, geoids))
    total = sum(n for n, _g, _y in got)
    other_year = dict((g, y) for _n, g, y in sorted(got, key=lambda r: r[1]) if y != TIGER_YEAR)
    vintage = f"TIGER/Line {TIGER_YEAR} roads"
    if other_year:
        names = list(other_year)
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        vintage += f" ({TIGER_YEAR - 1} for {'county' if len(names) == 1 else 'counties'} {listed})"
    (folder / "counties.json").write_text(json.dumps(dict(regions=vpus, geoids=geoids), indent=1), encoding="utf-8")
    record("tiger", {"url": TIGER_URL.format(year=TIGER_YEAR, geoid="<county>"), "counties": len(geoids),
                     "bytes": total, "vintage": vintage, "other_year": other_year}, root)
    log(f"tiger: {total / 1e6:,.0f} MB")
    return {"counties": len(geoids), "bytes": total, "other_year": other_year}


# --------------------------------------------------------------------------- #
# NWI
# --------------------------------------------------------------------------- #
def states(vpus: list[str]) -> list[str]:
    """Postal codes of the states each region touches (DEEP's Census 1:500k outlines)."""
    with gzip.open(REPO_ROOT / "apps" / "deep" / "data" / "us_states.geojson.gz", "rt", encoding="utf-8") as fh:
        feats = json.load(fh)["features"]
    shapes = [(f["properties"]["state"], shapely.geometry.shape(f["geometry"])) for f in feats]
    out = set()
    for g in region_outlines(vpus).values():
        out.update(st for st, s in shapes if s.intersects(g))
    return sorted(out)


def nwi(vpus: list[str], root: Path = SOURCES_DIR, workers: int = 3, skip: tuple = ("AK",),
        log: Callable = log_default) -> dict:
    sts = [s for s in states(vpus) if s not in skip]
    log(f"nwi: {len(sts)} states: {' '.join(sts)}")

    def one(st):
        url = NWI_URL.format(st=st)
        dest = root / "nwi" / f"{st}_geodatabase_wetlands.zip"
        if not dest.exists():
            fetch_url(url, dest, size=remote_size(url), log=log, timeout=300)
            log(f"nwi {st}: {dest.stat().st_size / 1e6:,.0f} MB")
        return dest.stat().st_size

    with ThreadPoolExecutor(max_workers=workers) as pool:
        total = sum(pool.map(one, sts))
    record("nwi", {"url": NWI_URL.format(st="<ST>"), "states": sts, "skipped": list(skip), "bytes": total,
                   "vintage": f"NWI state geodatabases downloaded {datetime.now():%Y-%m-%d}"}, root)
    return {"states": sts, "bytes": total}


# --------------------------------------------------------------------------- #
# gNATSGO on Planetary Computer
# --------------------------------------------------------------------------- #
_tokens: dict = {}


def _pc_token(collection: str, max_age_s: float = 2400.0) -> str:
    """A Planetary Computer read token, reused for 40 minutes (the anonymous
    endpoint is rate limited); a 429 waits and tries again."""
    held = _tokens.get(collection)
    if held and time.time() - held[1] < max_age_s:
        return held[0]
    for attempt in range(1, 8):
        r = requests.get(PC_SAS.format(collection=collection), headers=UA, timeout=60)
        if r.status_code == 429:
            time.sleep(min(300, 30 * attempt))
            continue
        r.raise_for_status()
        _tokens[collection] = (r.json()["token"], time.time())
        return _tokens[collection][0]
    raise RuntimeError(f"no Planetary Computer token for {collection}")


def gnatsgo_tables(root: Path = SOURCES_DIR, log: Callable = log_default) -> dict:
    """The component, horizon, map-unit and legend tables (Parquet, every blob
    under each table's path in the ``soils`` storage account)."""
    import xml.etree.ElementTree as ET
    token = _pc_token("soils/gnatsgo")
    out = {}
    for name in GNATSGO_TABLES:
        item = requests.get(f"{PC_STAC}/collections/gnatsgo-tables/items/{name}", headers=UA, timeout=60).json()
        asset = item["assets"]["data"]
        account = asset.get("table:storage_options", {}).get("account_name", "soils")
        container, path = asset["href"].split("://", 1)[1].split("/", 1)
        base = f"https://{account}.blob.core.windows.net/{container}"
        listing = requests.get(f"{base}?restype=container&comp=list&prefix={path}&{token}", headers=UA, timeout=60)
        listing.raise_for_status()
        blobs = [(b.findtext("Name"), int(b.findtext("Properties/Content-Length") or 0))
                 for b in ET.fromstring(listing.content).iter("Blob")]
        folder = root / "gnatsgo" / "tables" / name
        size = 0
        for blob, nbytes in blobs:
            if nbytes == 0:
                continue
            rel = blob[len(path):].lstrip("/") or blob.rsplit("/", 1)[1]
            dest = fetch_url(f"{base}/{blob}?{token}", folder / rel.replace("/", "__"), size=nbytes, log=log)
            size += dest.stat().st_size
        out[name] = size
        log(f"gnatsgo table {name}: {len(blobs)} blobs, {size / 1e6:,.1f} MB")
    record("gnatsgo_tables", {"collection": "gnatsgo-tables", "tables": out,
                              "vintage": "gNATSGO July 2020 (Microsoft Planetary Computer)"}, root)
    return out


def gnatsgo_mukey(vpus: list[str], root: Path = SOURCES_DIR, buffer_m: float = 1000.0,
                  log: Callable = log_default) -> dict:
    """Each region's window of the 10 m map-unit grid, written as one GeoTIFF per
    source tile (``gnatsgo/mukey/<item>.tif``, cropped to the regions' extents)."""
    import rasterio
    import rasterio.errors
    from rasterio.windows import Window, from_bounds

    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "6")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "5")
    outlines = dict((v, to_5070(g).buffer(buffer_m)) for v, g in region_outlines(lower48(vpus)).items())
    need: dict = {}
    for vpu, poly in outlines.items():
        search = requests.post(f"{PC_STAC}/search", headers=UA, timeout=120, json={
            "collections": ["gnatsgo-rasters"], "limit": 100,
            "intersects": json.loads(shapely.to_geojson(region_outlines([vpu])[vpu].buffer(0.02)))}).json()
        for it in search.get("features", []):
            if not it["id"].startswith("conus_"):
                continue
            need.setdefault(it["id"], {"href": it["assets"]["mukey"]["href"], "boxes": []})
            need[it["id"]]["boxes"].append(poly.bounds)
    log(f"gnatsgo: {len(need)} map-unit tiles touch the regions")
    folder = root / "gnatsgo" / "mukey"
    folder.mkdir(parents=True, exist_ok=True)
    total = 0
    for k, (item_id, info) in enumerate(sorted(need.items()), 1):
        dest = folder / f"{item_id}.tif"
        if dest.exists():
            continue
        token = _pc_token("soils/gnatsgo")
        with rasterio.open(f"{info['href']}?{token}") as src:
            box = shapely.union_all([shapely.box(*b) for b in info["boxes"]]).bounds
            win = from_bounds(*box, transform=src.transform).round_offsets().round_lengths()
            try:            # the catalogue footprint is a lon/lat box, larger than the tile
                win = win.intersection(Window(0, 0, src.width, src.height))
            except rasterio.errors.WindowError:
                log(f"gnatsgo: {k}/{len(need)} {item_id} misses the regions, skipped")
                continue
            prof = src.profile
            prof.update(driver="GTiff", width=int(win.width), height=int(win.height),
                        transform=src.window_transform(win), compress="deflate", predictor=2, tiled=True,
                        blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER")
            tmp = dest.with_name(dest.name + ".tmp")
            with rasterio.open(tmp, "w", **prof) as out:
                step = 2048
                for r0 in range(0, int(win.height), step):
                    h = min(step, int(win.height) - r0)
                    sub = Window(win.col_off, win.row_off + r0, win.width, h)
                    out.write(src.read(1, window=sub), 1, window=Window(0, r0, win.width, h))
        tmp.replace(dest)
        total += dest.stat().st_size
        log(f"gnatsgo: {k}/{len(need)} {item_id} {dest.stat().st_size / 1e6:,.0f} MB")
    record("gnatsgo_mukey", {"collection": "gnatsgo-rasters", "items": sorted(need), "regions": vpus,
                             "grid": {"crs": 5070, "cell": 10.0, "offset": 5.0},
                             "vintage": "gNATSGO July 2020 (Microsoft Planetary Computer)"}, root)
    return {"items": len(need), "bytes": total}
