"""Local readers for the two bulk geodatabases EPA publishes, so the geometry,
HUC12 and ATTAINS stages stop paging web services:

- NHDPlus V2 national seamless geodatabase (``national/nhdplus/.../*.gdb``):
  ``NHDFlowline_Network`` carries the flowline geometry with the VAA columns
  the fabric API used to join, and ``HUC12`` the subwatershed polygons.
- ATTAINS national geodatabase (``national/attains/*.gdb``): the assessed
  points, lines and areas with the fields the app reads.

Each is converted once (a national job step, skipped when the geodatabase is
absent) into a parquet under ``national/``; a chunk then filters that file.
The chunk stages keep their service paths for roots without the files, and
their outputs are identical either way, so a chunk finished one way stays done.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Optional

from ..paths import DataRoot
from ..state import Progress
from . import common

FLOWLINE_COLUMNS = ("comid", "gnis_name", "reachcode", "lengthkm", "fcode", "streamorde",
                    "slope", "totdasqkm", "flowdir")
_FLOWLINE_SOURCE = {"COMID": "comid", "GNIS_NAME": "gnis_name", "REACHCODE": "reachcode",
                    "LENGTHKM": "lengthkm", "FCODE": "fcode", "StreamOrde": "streamorde",
                    "SLOPE": "slope", "TotDASqKM": "totdasqkm", "FLOWDIR": "flowdir"}
HUC12_COLUMNS = ("huc_12", "huc_8", "hu_12_name", "states")
_HUC12_SOURCE = {"HUC_12": "huc_12", "HUC_8": "huc_8", "HU_12_NAME": "hu_12_name", "STATES": "states"}
#: the app's layer numbers on the ATTAINS map service -> geodatabase layers
ATTAINS_LAYERS = {0: "attains_au_points", 1: "attains_au_lines", 2: "attains_au_areas"}
_ATTAINS_FIELDS = ("assessmentunitidentifier", "assessmentunitname", "overallstatus",
                   "isimpaired", "ircategory")
PRECISION = 7


# ------------------------------------------------------------- locations
def nhdplus_gdb(root: DataRoot) -> Optional[Path]:
    hits = sorted(glob.glob(str(root.national / "nhdplus" / "**" / "*.gdb"), recursive=True))
    hits = [h for h in hits if "Seamless" in Path(h).name] or hits
    return Path(hits[0]) if hits else None


def attains_gdb(root: DataRoot) -> Optional[Path]:
    hits = sorted(glob.glob(str(root.national / "attains" / "*.gdb")))
    return Path(hits[-1]) if hits else None            # the newest vintage by name


def flowlines_path(root: DataRoot) -> Path:
    return root.national / "flowlines.parquet"


def huc12_path(root: DataRoot) -> Path:
    return root.national / "huc12.parquet"


def attains_path(root: DataRoot) -> Path:
    return root.national / "attains.parquet"


# ------------------------------------------------------------ conversions
def write_flowlines(root: DataRoot, gdf) -> Path:
    gdf = gdf[list(FLOWLINE_COLUMNS) + ["geometry"]].copy()
    gdf["comid"] = gdf["comid"].astype("int64")
    gdf = gdf.sort_values("comid").reset_index(drop=True)
    return common.write_parquet(gdf, flowlines_path(root))


def convert_flowlines(root: DataRoot, progress: Progress, *, gdb: Optional[Path] = None) -> Path:
    """``NHDFlowline_Network`` -> ``national/flowlines.parquet`` (EPSG:4326, 2D,
    the columns the fabric stage served, sorted by comid)."""
    import pyogrio
    gdb = gdb or nhdplus_gdb(root)
    if gdb is None:
        raise RuntimeError("NHDPlus seamless geodatabase not found under national/nhdplus")
    progress.say(f"reading NHDFlowline_Network from {gdb.name} ...")
    gdf = pyogrio.read_dataframe(str(gdb), layer="NHDFlowline_Network",
                                 columns=list(_FLOWLINE_SOURCE), force_2d=True)
    gdf = gdf.rename(columns=_FLOWLINE_SOURCE).to_crs("EPSG:4326")
    path = write_flowlines(root, gdf)
    progress.say(f"flowlines.parquet: {len(gdf):,} reaches")
    return path


def write_huc12(root: DataRoot, gdf) -> Path:
    gdf = gdf[list(HUC12_COLUMNS) + ["geometry"]].copy()
    gdf["huc_12"] = gdf["huc_12"].astype(str)
    gdf["huc_8"] = gdf["huc_8"].astype(str)
    gdf = gdf.drop_duplicates("huc_12").sort_values("huc_12").reset_index(drop=True)
    return common.write_parquet(gdf, huc12_path(root))


def convert_huc12(root: DataRoot, progress: Progress, *, gdb: Optional[Path] = None) -> Path:
    """``HUC12`` -> ``national/huc12.parquet`` (EPSG:4326, the fabric columns)."""
    import pyogrio
    gdb = gdb or nhdplus_gdb(root)
    if gdb is None:
        raise RuntimeError("NHDPlus seamless geodatabase not found under national/nhdplus")
    progress.say(f"reading HUC12 from {gdb.name} ...")
    gdf = pyogrio.read_dataframe(str(gdb), layer="HUC12", columns=list(_HUC12_SOURCE))
    gdf = gdf.rename(columns=_HUC12_SOURCE).to_crs("EPSG:4326")
    path = write_huc12(root, gdf)
    progress.say(f"huc12.parquet: {len(gdf):,} subwatersheds")
    return path


def _text(value) -> Optional[str]:
    if value is None or value != value:                       # None or NaN
        return None
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


def attains_rows_from(gdf, layer: int) -> list[dict]:
    """One row per single-part geometry with the app's fields, WKB and bounds."""
    parts = gdf.explode(index_parts=False)
    rows = []
    for record in parts.itertuples(index=False):
        geom = record.geometry
        if geom is None or geom.is_empty:
            continue
        minx, miny, maxx, maxy = geom.bounds
        rows.append({"layer": layer,
                     "assessment_unit": _text(getattr(record, "assessmentunitidentifier", None)),
                     "assessment_name": _text(getattr(record, "assessmentunitname", None)),
                     "overallstatus": _text(getattr(record, "overallstatus", None)),
                     "isimpaired": _text(getattr(record, "isimpaired", None)),
                     "ircategory": _text(getattr(record, "ircategory", None)),
                     "wkb": geom.wkb, "minx": float(minx), "miny": float(miny),
                     "maxx": float(maxx), "maxy": float(maxy)})
    return rows


ATTAINS_ROW_GROUP = 25_000


def write_attains_rows(root: DataRoot, rows: list[dict]) -> Path:
    """Sorted by west edge in small row groups, so a chunk's bounding-box
    filter prunes most of the file through the row-group statistics."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.Table.from_pylist(rows, schema=pa.schema([
        ("layer", pa.int8()), ("assessment_unit", pa.string()), ("assessment_name", pa.string()),
        ("overallstatus", pa.string()), ("isimpaired", pa.string()), ("ircategory", pa.string()),
        ("wkb", pa.large_binary()),                 # 3 GB of geometry overflows 32-bit offsets
        ("minx", pa.float64()), ("miny", pa.float64()),
        ("maxx", pa.float64()), ("maxy", pa.float64())])).sort_by("minx")
    path = attains_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    pq.write_table(table, tmp, compression="zstd", row_group_size=ATTAINS_ROW_GROUP)
    tmp.replace(path)
    return path


def convert_attains(root: DataRoot, progress: Progress, *, gdb: Optional[Path] = None) -> Path:
    """The three assessed layers -> ``national/attains.parquet`` (EPSG:4326,
    one row per single-part geometry, WKB plus bounds)."""
    import pyogrio
    gdb = gdb or attains_gdb(root)
    if gdb is None:
        raise RuntimeError("ATTAINS geodatabase not found under national/attains")
    rows: list[dict] = []
    for layer, name in ATTAINS_LAYERS.items():
        progress.say(f"reading {name} from {gdb.name} ...")
        gdf = pyogrio.read_dataframe(str(gdb), layer=name, columns=list(_ATTAINS_FIELDS), force_2d=True)
        gdf = gdf.to_crs("EPSG:4326")
        rows.extend(attains_rows_from(gdf, layer))
        progress.say(f"{name}: {len(gdf):,} features")
    path = write_attains_rows(root, rows)
    progress.say(f"attains.parquet: {len(rows):,} segments of "
                 f"{len({r['assessment_unit'] for r in rows}):,} assessment units")
    return path


# ------------------------------------------------------------ chunk reads
def flowlines_for(root: DataRoot, comids: list[int]):
    """The chunk's flowlines from the national file, None without the file."""
    import geopandas as gpd
    path = flowlines_path(root)
    if not path.exists():
        return None
    wanted = sorted({int(c) for c in comids})
    gdf = gpd.read_parquet(path, filters=[("comid", "in", wanted)])
    return gdf.sort_values("comid").reset_index(drop=True)


def huc12_for(root: DataRoot, huc8s: list[str]):
    """The chunk's HUC12 polygons from the national file, None without it."""
    import geopandas as gpd
    path = huc12_path(root)
    if not path.exists():
        return None
    gdf = gpd.read_parquet(path, filters=[("huc_8", "in", [str(h) for h in huc8s])])
    return gdf.sort_values("huc_12").reset_index(drop=True)


def esri_json(geom) -> Optional[dict]:
    """The ESRI JSON the app's ``attains._shape`` reads (one part per row)."""
    def ring(coords):
        return [[round(x, PRECISION), round(y, PRECISION)] for x, y in coords]
    kind = geom.geom_type
    if kind == "Point":
        return {"x": round(geom.x, PRECISION), "y": round(geom.y, PRECISION)}
    if kind == "MultiPoint":
        return {"points": [[round(p.x, PRECISION), round(p.y, PRECISION)] for p in geom.geoms]}
    if kind == "LineString":
        return {"paths": [ring(geom.coords)]}
    if kind == "MultiLineString":
        return {"paths": [ring(line.coords) for line in geom.geoms]}
    if kind == "Polygon":
        return {"rings": [ring(geom.exterior.coords)] + [ring(r.coords) for r in geom.interiors]}
    if kind == "MultiPolygon":
        rings = []
        for poly in geom.geoms:
            rings.append(ring(poly.exterior.coords))
            rings.extend(ring(r.coords) for r in poly.interiors)
        return {"rings": rings}
    return None


def attains_for(root: DataRoot, bbox: list[float]) -> Optional[list[dict]]:
    """Rows in the ATTAINS chunk schema (ESRI JSON geometry) whose bounds
    touch ``bbox``; None without the national file."""
    import pyarrow.parquet as pq
    from shapely import wkb as shapely_wkb
    path = attains_path(root)
    if not path.exists():
        return None
    west, south, east, north = [float(v) for v in bbox]
    table = pq.read_table(path, filters=[("maxx", ">=", west), ("minx", "<=", east),
                                         ("maxy", ">=", south), ("miny", "<=", north)])
    rows = []
    for row in table.to_pylist():
        geometry = esri_json(shapely_wkb.loads(row.pop("wkb")))
        if geometry is None:
            continue
        row["geometry"] = json.dumps(geometry, separators=(",", ":"))
        rows.append(row)
    return rows
