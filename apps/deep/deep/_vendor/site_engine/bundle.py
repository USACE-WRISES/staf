"""The STAF data bundle as the engine's HR source (engine 0.5.0).

The bundle (written by ``tools/hr-slim``; the layout of ``apps/hr-data/data``: ``manifest.json``,
the region files, ``links2.parquet``, ``values/``, ``tables/`` and ``coverage_absent.geojson``)
answers ``hr.py``'s calls locally wherever it covers the request; anywhere else the service
answers, so a partial bundle (the pilot) and the national one behave the same way to callers. The
records are ``hr.parse_feature`` records built from the same fields. Flowline geometry is the
bundle's (2 m, line ends kept); catchments are exact, so a watershed's outline and area are the
service's. The reader is ``_hrslim``, byte-synced from ``apps/hr-data/hrslim``
(``scripts/sync_hrslim.py``).

Coverage: ``coverage_absent.geojson`` holds the outlines of the regions the bundle lacks. A box
within ``EDGE_DEG`` of one goes to the service, and so does a watershed whose outline comes that
close (its tree might continue into the missing region, which the bundle cannot see). Without
that file the bundle answers id lookups only.

The switch is ``STAF_DATA_SOURCE`` (or ``set_source``): ``service`` (the default until the bundle
is hosted) asks USGS only; ``bundle`` and ``auto`` answer from the bundle where it covers and from
the service elsewhere. ``STAF_DATA_BUNDLE`` is a bundle folder; without one the bundle is
delivered from its rolling release (``delivery``: the core on first use, a region or a national
table the first time a request reads it, into ``STAF_DATA_CACHE``). Never raises: a broken
bundle, or a release or an asset that cannot be had now, reads as absent and the service answers.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

ENV_SOURCE = "STAF_DATA_SOURCE"
ENV_ROOT = "STAF_DATA_BUNDLE"
SOURCES = ("service", "bundle", "auto")
ABSENT_FILE = "coverage_absent.geojson"
#: A request this close (degrees) to a region the bundle lacks goes to the service.
EDGE_DEG = 0.005
#: a release that could not be had is asked again after this long
RELEASE_RETRY_S = 300.0
_LINE_FIELDS = ("nhdplusid", "gnis_name", "reachcode", "lengthkm", "totdasqkm", "slope", "fcode", "ftype",
                "streamorde", "hydroseq", "uphydroseq", "dnhydroseq", "vpuid", "qama")

_lock = threading.RLock()
_state: dict = {"source": None, "root": None}
_cache: dict = {}


def set_source(name: Optional[str], root=None) -> None:
    """Choose this process's HR source and bundle folder (apps at startup, tests); ``None`` goes
    back to the environment."""
    if name is not None and name not in SOURCES:
        raise ValueError(f"unknown HR source {name!r}")
    with _lock:
        _state["source"] = name
        _state["root"] = None if root is None else Path(root)
        _cache.clear()
    # the cross-sections read USGS's tile files with the catalogs the bundle ships
    from ._extracted import dem_tiles
    if name is None:
        dem_tiles.use_environment()
    elif name == "service":
        dem_tiles.set_catalog_folder(None)
    elif root is not None:
        dem_tiles.set_catalog_folder(Path(root) / "tables")
    else:
        from .delivery import default_cache
        dem_tiles.set_catalog_folder(default_cache() / "tables")


def source() -> str:
    name = _state["source"] or (os.environ.get(ENV_SOURCE) or "").strip().lower() or "service"
    return name if name in SOURCES else "service"


def local_root() -> Optional[Path]:
    """The bundle folder ``set_source`` or ``STAF_DATA_BUNDLE`` names, or None."""
    folder = _state["root"] or os.environ.get(ENV_ROOT)
    return Path(folder) if folder else None


def release():
    """The release the bundle is delivered from, its core in the cache (``delivery.Release``), or
    None: the service source, a local bundle folder, or a release that cannot be had now (asked
    again after ``RELEASE_RETRY_S``)."""
    if source() == "service" or local_root() is not None:
        return None
    with _lock:
        rel = _cache.get("release")
        if rel is not None:
            return rel
        failed = _cache.get("releaseFailed")
        if failed is not None and time.monotonic() - failed < RELEASE_RETRY_S:
            return None
        try:
            from .delivery import Release
            rel = Release()
            rel.prepare()
        except Exception as exc:  # noqa: BLE001 - no release now: the service answers
            _cache["releaseFailed"] = time.monotonic()
            _cache["error"] = f"bundle release unavailable: {exc}"
            return None
        _cache["release"] = rel
        _cache.pop("releaseFailed", None)
        _cache.pop("error", None)
        return rel


def root() -> Optional[Path]:
    """The bundle folder: the local one, else the delivered release's cache, else None."""
    local = local_root()
    if local is not None:
        return local
    rel = release()
    return rel.cache if rel is not None else None


def _hook(kind: str):
    """The delivered release's ``ensure_<kind>`` (a region or a table fetched on first read), or
    None for a local folder."""
    rel = _cache.get("release") if local_root() is None else None
    return None if rel is None else getattr(rel, f"ensure_{kind}")


def dataset():
    """The bundle's reader, or None (the service source, no folder or release, or an unreadable
    bundle). A release that cannot be had now is not remembered: it is asked again later."""
    if source() == "service":
        return None
    with _lock:
        if "ds" in _cache:
            return _cache["ds"]
        folder = root()
        if folder is None:
            _cache.setdefault("error", "no bundle folder")
            return None
        _cache["ds"] = None
        try:
            if (folder / "manifest.json").exists():
                from ._hrslim.reader2 import Dataset2
                _cache["ds"] = Dataset2(folder, ensure=_hook("region"))
            else:
                _cache["error"] = "no bundle folder"
        except Exception as exc:  # noqa: BLE001 - a broken bundle reads as absent
            _cache["error"] = f"bundle unreadable: {exc}"
        return _cache["ds"]


def enabled() -> bool:
    return dataset() is not None


def describe() -> dict:
    """What answers the HR calls: ``{"source": "service"}`` or the bundle's identity."""
    ds = dataset()
    if ds is None:
        out = {"source": "service"}
        if source() != "service" and _cache.get("error"):
            out["bundleUnavailable"] = _cache["error"]
        return out
    out = {"source": "bundle", "built": ds.manifest.get("built"), "regions": len(ds.vpus),
           "format": ds.manifest.get("format")}
    rel = _cache.get("release") if local_root() is None else None
    if rel is not None:
        out["delivered"] = rel.describe()
    return out


def values():
    """The bundle's per-catchment value tables (``values/``), or None."""
    ds = dataset()
    if ds is None:
        return None
    with _lock:
        if "values" not in _cache:
            folder = root() / "values"
            from ._hrslim.values import ValueTables
            hook = _hook("region")
            _cache["values"] = ValueTables(folder, ensure=hook) if (hook or folder.is_dir()) else None
        return _cache["values"]


def point_tables():
    """The bundle's national tables (``tables/``: WQP, NID, NWIS, NAS), or None."""
    ds = dataset()
    if ds is None:
        return None
    with _lock:
        if "points" not in _cache:
            folder = root() / "tables"
            from ._hrslim.points import PointTables
            tables = PointTables(folder, ensure=_hook("table"))
            _cache["points"] = tables if tables.available() else None
        return _cache["points"]


def lookups():
    """``(points, tables)``: the bundle's point-lookup module (``_hrslim.points``, the apps' own
    rules for WQP, NID, NWIS and NAS) and its national tables, or None when the bundle is off or
    has no tables. The apps' lookups ask it first and query the services only without it."""
    tables = point_tables()
    if tables is None:
        return None
    from ._hrslim import points
    return points, tables


def v2_dataset():
    """The bundle's NHDPlus V2 regions (``v2/``: exact V2 catchments and flowlines with their
    network, StreamCat slices and EROM flows, for EASI's covered streams), or None."""
    ds = dataset()
    if ds is None:
        return None
    with _lock:
        if "v2" not in _cache:
            _cache["v2"] = None
            folder = root() / "v2"
            if (folder / "manifest.json").exists():
                try:
                    from ._hrslim.reader2 import Dataset2
                    _cache["v2"] = Dataset2(folder, ensure=_hook("region"))
                except Exception:  # noqa: BLE001 - an unreadable V2 part reads as absent
                    _cache["v2"] = None
        return _cache["v2"]


def _v2_table(prefix: str, vpu: str):
    """``(table, {comid: row})`` of a V2 region's ``<prefix>2_<vpu>.parquet``, read once."""
    key = (prefix, vpu)
    with _lock:
        if key not in _cache:
            import pyarrow.parquet as pq
            hook = _hook("region")
            if hook is not None:
                hook(vpu)                                    # raises when the region cannot be had
            path = root() / "v2" / f"{prefix}2_{vpu}.parquet"
            t = pq.read_table(path) if path.exists() else None
            index = None
            if t is not None and "comid" in t.column_names:
                index = dict((int(c), i) for i, c in enumerate(t.column("comid").to_pylist()))
            _cache[key] = (t, index)
        return _cache[key]


def streamcat(comid: int, base_names: Iterable[str]) -> Optional[dict]:
    """StreamCat values for the COMID from the bundle (``{column: value}`` for every column a
    requested base name begins, as the API returns them), or None unless the bundle holds the
    COMID and a column for every base name (then the API answers the whole request)."""
    try:
        ds2 = v2_dataset()
        names = [str(b).lower() for b in base_names if b]
        if ds2 is None or not names:
            return None
        found = ds2._find("by_id", [int(comid)])
        if not found:
            return None
        t, index = _v2_table("streamcat", found[0][0].vpu)
        if t is None or index is None or int(comid) not in index:
            return None
        cols = [c for c in t.column_names if c != "comid"]
        wanted = [c for c in cols if any(c.lower().startswith(b) for b in names)]
        if any(not any(c.lower().startswith(b) for c in wanted) for b in names):
            return None
        row = index[int(comid)]
        out: dict = {"comid": int(comid)}
        for c in wanted:
            v = t.column(c)[row].as_py()
            out[c.lower()] = v
        return out
    except Exception:  # noqa: BLE001
        return None


def v2_flowline(comid: int) -> Optional[dict]:
    """The V2 flowline's attributes (``parse_feature`` fields) with its gage-adjusted EROM
    flows (``qe_ma``, ``qe_01`` to ``qe_12``) and sinuosity, or None where the bundle lacks it."""
    try:
        ds2 = v2_dataset()
        if ds2 is None:
            return None
        found = ds2._find("by_id", [int(comid)])
        if not found:
            return None
        region, rows = found[0]
        recs = _records(ds2._as_v1(region, ds2._line_rows(region, rows)))
        if len(recs) != 1:
            return None
        rec = recs[0]
        import pyarrow.parquet as pq
        attrs_file = (region.meta.get("v2attrs") or {}).get("file")
        if attrs_file:
            key = ("v2attrs", region.vpu)
            with _lock:
                if key not in _cache:
                    _cache[key] = pq.read_table(root() / "v2" / attrs_file)
                t = _cache[key]
            for c in t.column_names:
                rec[c] = t.column(c)[int(rows[0])].as_py()
        return rec
    except Exception:  # noqa: BLE001
        return None


def v2_lines_in_box(west: float, south: float, east: float, north: float) -> Optional[list[dict]]:
    """The V2 flowlines intersecting the box (``parse_feature`` records, ``nhdplusid`` the COMID),
    or None where the bundle does not cover it."""
    try:
        ds2 = v2_dataset()
        if ds2 is None or not covers_box(west, south, east, north):
            return None
        t = ds2.lines_in_bbox(min(west, east), min(south, north), max(west, east), max(south, north))
        return [] if t is None else _records(t)
    except Exception:  # noqa: BLE001
        return None


#: the fabric API's NHDPlus V2 flowline properties EASI reads (``easi.datasources.fabric``)
_FABRIC_FIELDS = (("gnis_name", "gnis_name"), ("reachcode", "reachcode"), ("totdasqkm", "totdasqkm"),
                  ("slope", "slope"), ("fcode", "fcode"), ("streamorde", "stream_order"),
                  ("lengthkm", "lengthkm"))


def v2_feature(comid: int) -> Optional[dict]:
    """The COMID's flowline as the fabric API gives it (``properties``: comid, gnis_name,
    reachcode, totdasqkm, slope, fcode, streamorde, lengthkm and the EROM ``qe_*`` flows;
    ``geometry`` the exact V2 line), or None where the bundle lacks it."""
    rec = v2_flowline(comid)
    if rec is None:
        return None
    props = {"comid": int(comid)}
    for out_key, rec_key in _FABRIC_FIELDS:
        props[out_key] = rec.get(rec_key)
    for k, v in rec.items():
        if k.startswith("qe_"):
            props[k] = v
    return {"type": "Feature", "properties": props, "geometry": rec["geometry"]}


#: a V2 basin past this many reaches is left to NLDI (its basins are precomputed server-side)
V2_MAX_REACHES = 50_000


def v2_watershed(comid: int) -> Optional[dict]:
    """The COMID's upstream basin from the bundle's V2 regions (the walk follows minor
    divergences, as NLDI's basins do; exact catchments on the 30 m grid): ``{"geometry",
    "areaSqkm", "nReaches"}``, or None where the bundle cannot answer for the whole basin."""
    try:
        from shapely.geometry import shape
        ds2 = v2_dataset()
        if ds2 is None or not ds2._find("by_id", [int(comid)]):
            return None
        ws = ds2.watershed(int(comid), max_reaches=V2_MAX_REACHES, max_hops=V2_MAX_REACHES)
        if ws.get("status") != "ok" or not ws.get("geometry") or _near_absent(shape(ws["geometry"])):
            return None
        return {"geometry": ws["geometry"], "areaSqkm": ws.get("areaSqkm"), "nReaches": ws.get("nReaches")}
    except Exception:  # noqa: BLE001
        return None


def v2_mainstem(comid: int, km: float, *, upstream: bool = True) -> Optional[list[dict]]:
    """The COMID's flowline record and the mainstem above it (``uphydroseq``) or below it
    (``dnhydroseq``), in walk order, as NLDI's ``upstreamMain`` / ``downstreamMain`` navigations
    within ``km`` give them: NLDI rounds the distance to whole kilometres (half to even, 8.5 km
    walks 8) and takes each next flowline while the line already walked, the COMID's own
    included, is shorter than that (checked against NLDI in 24 walks and at distances from 8.04
    to 9 km, ``tools/hr-slim/scripts/v2_check.py nav``). None where the bundle lacks the COMID or
    the walk runs into a region it does not hold."""
    try:
        ds2 = v2_dataset()
        first = v2_flowline(comid) if ds2 is not None else None
        if first is None:
            return None
        limit = float(round(float(km)))
        key = "uphydroseq" if upstream else "dnhydroseq"
        out, total, rec = [first], float(first.get("lengthkm") or 0.0), first
        for _ in range(5000):
            nxt = rec.get(key)
            if total >= limit or not nxt:
                return out
            t = ds2.reach(hydroseq=int(nxt))
            recs = _records(t) if t is not None else []
            if len(recs) != 1:
                return None
            rec = recs[0]
            out.append(rec)
            total += float(rec.get("lengthkm") or 0.0)
        return None
    except Exception:  # noqa: BLE001
        return None


def v2_upstream_main(comid: int, km: float) -> Optional[list[dict]]:
    """The geometries of ``v2_mainstem(comid, km)`` (an assessment reach's lines), or None."""
    recs = v2_mainstem(comid, km)
    return None if recs is None else [r["geometry"] for r in recs]


def v2_catchment_at(lat: float, lon: float) -> Optional[int]:
    """The COMID of the V2 catchment that contains the point (NLDI's ``comid/position``), or None
    where the bundle holds no V2 catchment there."""
    try:
        ds2 = v2_dataset()
        return _catchment_at(ds2, lat, lon) if ds2 is not None else None
    except Exception:  # noqa: BLE001
        return None


def _catchment_at(ds, lat: float, lon: float) -> Optional[int]:
    """The id of the reader's exact catchment that contains the point, or None."""
    import shapely

    from ._hrslim import fmt
    eps = 1e-6
    t = ds.catchments_in_bbox(lon - eps, lat - eps, lon + eps, lat + eps)
    if t is None:
        return None
    pt = shapely.Point(lon, lat)
    for nid, geom in zip(t.column("nhdplusid").to_pylist(), fmt.decode_polygons(t)):
        if geom is not None and geom.covers(pt):
            return int(nid)
    return None


#: a point this close to an HR flowline is on it (the apps' snap tolerance)
POINT_TOL_FT = 150.0
RAINDROP_METHOD = "staf-data-bundle-v2-catchment"


def raindrop(lat: float, lon: float) -> Optional[dict]:
    """NLDI's hydrolocation answer from the bundle: ``{"comid", "snap_lat", "snap_lon", "method"}``,
    the V2 reach whose catchment contains the point and the nearest point on its flowline, or
    None where the bundle holds no V2 catchment there (NLDI answers then).

    An NHDPlus V2 catchment is the land that drains to its flowline on the V2 flow grid, so the
    catchment that contains the point names the reach NLDI's raindrop trace reaches: the same
    COMID at 103 of 106 points sampled along pilot HR flowlines (2026-10-02; NLDI's trace left the
    point's catchment at the other three). The trace's end on the flowline is not reproduced: the
    snap is the nearest point on the flowline (median 130 ft from NLDI's). The exact V2 catchments
    form a partition, so a catchment found is the one NLDI would find, wherever the point lies."""
    try:
        from .geometry import nearest_point_on_records
        comid = v2_catchment_at(lat, lon)
        rec = v2_flowline(comid) if comid is not None else None
        hit = nearest_point_on_records([rec], lat, lon) if rec is not None else None
        if hit is None:
            return None
        return {"comid": int(comid), "snap_lat": round(hit[0], 7), "snap_lon": round(hit[1], 7),
                "method": RAINDROP_METHOD}
    except Exception:  # noqa: BLE001
        return None


def point_extras(lat: float, lon: float, *, tol_ft: float = POINT_TOL_FT) -> Optional[dict]:
    """The precomputed per-flowline lookups (``_hrslim.values.flowline_extras``: the HUC12, the
    ATTAINS unit at the point and the nearest within 2 km, the 150 m NWI strip) of the HR flowline
    nearest the point, at the sample (5, 50 or 95 percent along it) nearest the point's place on
    it, or None where the bundle cannot answer (no flowline within ``tol_ft``, no tables). An
    assessment asks for one point several times (HUC12, ATTAINS at it, ATTAINS near it): the last
    answers are kept."""
    key = ("extras", round(float(lat), 7), round(float(lon), 7), float(tol_ft))
    with _lock:
        if key in _cache:
            return _cache[key]
    got = _point_extras(lat, lon, tol_ft)
    with _lock:
        kept = [k for k in _cache if isinstance(k, tuple) and k and k[0] == "extras"]
        for k in kept[:-63]:
            _cache.pop(k, None)
        _cache[key] = got
    return got


def _point_extras(lat: float, lon: float, tol_ft: float) -> Optional[dict]:
    try:
        from shapely.geometry import Point, shape
        from shapely.ops import linemerge

        from ._hrslim import values as value_tables
        from .geometry import nearest_point_on_records
        ds, tables = dataset(), values()
        half = 0.004
        if ds is None or tables is None or not covers_box(lon - half, lat - half, lon + half, lat + half):
            return None
        recs = lines_in_box(lon - half, lat - half, lon + half, lat + half) or []
        hit = nearest_point_on_records(recs, lat, lon)
        if hit is None or hit[3] is None or hit[2] > tol_ft:
            return None
        nid = int(hit[3])
        found = ds._find("by_id", [nid])
        if not found or not all(tables.present(p, found[0][0].vpu) for p in ("extras", "au")):
            return None
        rec = next(r for r in recs if r.get("nhdplusid") == nid)
        line = shape(rec["geometry"])
        if line.geom_type == "MultiLineString":
            line = linemerge(line)
        fraction = float(line.project(Point(hit[1], hit[0]), normalized=True)) if line.length else 0.5
        return value_tables.flowline_extras(ds, tables, nid, fraction)
    except Exception:  # noqa: BLE001
        return None


#: NWI systems in the per-flowline strips (``nwi_<code>_m2``)
NWI_SYSTEMS = (("r", "riverine"), ("p", "palustrine"), ("l", "lacustrine"), ("e", "estuarine"), ("m", "marine"))
NWI_STRIP_HALF_WIDTH_M = 150.0


def _single_geometry(gj):
    """The one geometry of a GeoJSON FeatureCollection, Feature or geometry, or None."""
    from shapely.geometry import shape
    if not gj:
        return None
    if gj.get("type") == "FeatureCollection":
        feats = [f for f in gj.get("features") or [] if f.get("geometry")]
        return shape(feats[0]["geometry"]) if len(feats) == 1 else None
    if gj.get("type") == "Feature":
        return shape(gj["geometry"]) if gj.get("geometry") else None
    return shape(gj)


def reach_wetlands(nhdplusid: int, reach_geojson) -> Optional[dict]:
    """NWI wetland area within ``NWI_STRIP_HALF_WIDTH_M`` of the assessment reach: the precomputed
    150 m strips of the HR flowlines the reach runs along (the anchor flowline and its upstream
    mainstem, as ``reach.derive_reach`` walks them), each counted for the share of its length the
    reach covers. ``{"wetlandM2": {system: m2}, "wetlandM2Total", "stripM2", "reachM",
    "flowlines": [{"nhdplusid", "share"}]}``, or None where the bundle cannot answer."""
    try:
        import numpy as np
        import shapely
        from pyproj import Transformer
        from shapely.geometry import shape
        ds, tables = dataset(), values()
        reach = _single_geometry(reach_geojson)
        if ds is None or tables is None or reach is None or reach.is_empty:
            return None
        tr = Transformer.from_crs(4326, 5070, always_xy=True)

        def metres(g):
            return shapely.transform(g, lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))

        reach_m = metres(reach)
        rec = flowline(nhdplusid=int(nhdplusid))
        if rec is None:
            return None
        wanted_km = reach_m.length / 1000.0 + float(rec.get("lengthkm") or 0.0) + 0.3
        chain, total = [rec], float(rec.get("lengthkm") or 0.0)
        while total < wanted_km and chain[-1].get("uphydroseq") and len(chain) < 26:
            nxt = flowline(hydroseq=int(chain[-1]["uphydroseq"]))
            if nxt is None:
                break
            chain.append(nxt)
            total += float(nxt.get("lengthkm") or 0.0)
        area = dict((name, 0.0) for _, name in NWI_SYSTEMS)
        strip, used = 0.0, []
        for r in chain:
            line_m = metres(shape(r["geometry"]))
            if not line_m.length:
                continue
            share = min(1.0, reach_m.intersection(line_m.buffer(1.0)).length / line_m.length)
            if share <= 0.0:
                continue
            found = ds._find("by_id", [int(r["nhdplusid"])])
            if not found or not tables.present("extras", found[0][0].vpu):
                return None
            region, rows = found[0]
            t = tables._table("extras", region.vpu)
            row = int(rows[0])
            if "strip_m2" not in t.column_names:
                return None
            for code, name in NWI_SYSTEMS:
                area[name] += share * float(t.column(f"nwi_{code}_m2")[row].as_py() or 0.0)
            strip += share * float(t.column("strip_m2")[row].as_py() or 0.0)
            used.append({"nhdplusid": int(r["nhdplusid"]), "share": round(share, 4)})
        if not used:
            return None
        return {"wetlandM2": dict((k, round(v, 1)) for k, v in area.items()),
                "wetlandM2Total": round(sum(area.values()), 1), "stripM2": round(strip, 1),
                "reachM": round(reach_m.length, 1), "flowlines": used}
    except Exception:  # noqa: BLE001
        return None


def wqp_start(text):
    """The apps' WQP start date (``MM-DD-YYYY``, the portal's format) as a date, or None."""
    from datetime import datetime
    try:
        return datetime.strptime(str(text), "%m-%d-%Y").date() if text else None
    except ValueError:
        return None


# ------------------------------------------------------------------ coverage
def _absent():
    """``(STRtree, geometries)`` of the regions the bundle lacks, or None without the file."""
    with _lock:
        if "absent" not in _cache:
            _cache["absent"] = None
            path = root() / ABSENT_FILE
            if path.exists():
                import shapely
                from shapely.geometry import shape
                fc = json.loads(path.read_text(encoding="utf-8"))
                geoms = [shape(f["geometry"]) for f in fc.get("features") or [] if f.get("geometry")]
                _cache["absent"] = (shapely.STRtree(geoms), geoms)
        return _cache["absent"]


def _near_absent(geom) -> bool:
    """Whether ``geom`` comes within ``EDGE_DEG`` of a region the bundle lacks."""
    absent = _absent()
    if absent is None:
        return True
    tree, _ = absent
    return bool(len(tree.query(geom.buffer(EDGE_DEG), predicate="intersects")))


def covers_box(west: float, south: float, east: float, north: float) -> bool:
    """Whether the bundle holds every network flowline of the box."""
    if not enabled():
        return False
    try:
        import shapely
        return not _near_absent(shapely.box(min(west, east), min(south, north), max(west, east), max(south, north)))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ records
def _records(table) -> list[dict]:
    """``hr.parse_feature`` records (GeoJSON geometry, LineString when single-part as the service
    gives it) from a reader line table."""
    from . import hr
    from ._hrslim import fmt
    geoms = fmt.line_geojson(table)
    cols = dict((c, table.column(c).to_pylist()) for c in _LINE_FIELDS if c in table.column_names)
    out = []
    for i, geom in enumerate(geoms):
        if not geom.get("coordinates"):
            continue
        props = dict((c, cols[c][i]) for c in cols)
        props["innetwork"] = 1
        rec = hr.parse_feature({"type": "Feature", "properties": props, "geometry": geom})
        if rec and rec.get("geometry"):
            out.append(rec)
    return out


def lines_in_box(west: float, south: float, east: float, north: float) -> Optional[list[dict]]:
    """The network flowlines intersecting the box, or None where the bundle does not cover it."""
    try:
        if not covers_box(west, south, east, north):
            return None
        t = dataset().lines_in_bbox(min(west, east), min(south, north), max(west, east), max(south, north))
        return [] if t is None else _records(t)
    except Exception:  # noqa: BLE001 - the service answers instead
        return None


def flowline(nhdplusid: Optional[int] = None, hydroseq: Optional[int] = None) -> Optional[dict]:
    """One flowline record by id or hydroseq, or None where the bundle does not hold it."""
    try:
        ds = dataset()
        if ds is None:
            return None
        t = ds.reach(nhdplusid=int(nhdplusid)) if nhdplusid is not None else ds.reach(hydroseq=int(hydroseq))
        recs = _records(t) if t is not None else []
        return recs[0] if len(recs) == 1 else None
    except Exception:  # noqa: BLE001
        return None


def _holds_all(ds, ids: list[int]) -> bool:
    found = sum(int(rows.size) for _, rows in ds._find("by_id", ids))
    return found >= len(set(ids))


def flowline_geometries(ids: Iterable[int]) -> Optional[list[dict]]:
    """``[{"nhdplusid", "geometry"}]`` for the ids, or None unless the bundle holds every one."""
    try:
        ds = dataset()
        ids = sorted({int(v) for v in ids})
        if ds is None or not ids or not _holds_all(ds, ids):
            return None
        t = ds.flowlines_by_ids(ids, attributes=False)
        return [] if t is None else [{"nhdplusid": r["nhdplusid"], "geometry": r["geometry"]} for r in _records(t)]
    except Exception:  # noqa: BLE001
        return None


def catchments(ids: Iterable[int]) -> Optional[list[dict]]:
    """``[{"nhdplusid", "areasqkm", "geometry"}]`` for the ids' catchments (a reach without one
    has no row, as in the service), or None unless the bundle holds every id's flowline."""
    try:
        import geopandas as gpd

        from ._hrslim import fmt
        ds = dataset()
        ids = sorted({int(v) for v in ids})
        if ds is None or not ids or not _holds_all(ds, ids):
            return None
        t = ds.catchments_by_ids(ids)
        if t is None:
            return []
        # areas from the exact polygons in EPSG:5070, the grid they were cut on
        areas = gpd.GeoSeries(fmt.decode_polygons(t), crs=4326).to_crs(5070).area.to_numpy() / 1e6
        return [{"nhdplusid": int(nid), "areasqkm": round(float(a), 6), "geometry": geom}
                for nid, a, geom in zip(t.column("nhdplusid").to_pylist(), areas, fmt.polygon_geojson(t))]
    except Exception:  # noqa: BLE001
        return None


def parents(frontier: list[dict]) -> Optional[list[dict]]:
    """One level of the upstream walk: the records whose downstream hydroseq is one of the
    frontier's, within and across the bundle's regions; None unless the bundle holds the whole
    frontier."""
    try:
        import numpy as np
        from shapely.geometry import shape
        ds = dataset()
        ids = [int(r["nhdplusid"]) for r in frontier if r.get("nhdplusid")]
        if ds is None or not ids or not _holds_all(ds, ids):
            return None
        # a frontier near a region the bundle lacks may have parents there: the service answers
        import shapely
        lines = [shape(r["geometry"]) for r in frontier if r.get("geometry")]
        if not lines or _near_absent(shapely.union_all(lines)):
            return None
        targets, link_vpu, link_row = ds._links_index()
        wanted: dict = {}
        for region, rows in ds._find("by_id", ids):
            t = region.topo()
            for row in rows:
                h = int(t["hs"][row])
                if not h:
                    continue
                for p in t["parents"][t["parent_ptr"][row]:t["parent_ptr"][row + 1]]:
                    wanted.setdefault(region.vpu, set()).add(int(p))
                lo, hi = np.searchsorted(targets, h, "left"), np.searchsorted(targets, h, "right")
                for k in range(lo, hi):
                    if link_vpu[k] in ds._regions:
                        wanted.setdefault(link_vpu[k], set()).add(int(link_row[k]))
        out: list[dict] = []
        seen: set = set(ids)
        for vpu, rows in wanted.items():
            region = ds._regions[vpu]
            t = ds._line_rows(region, np.asarray(sorted(rows), dtype=np.int64))
            if t is None:
                continue
            for rec in _records(ds._as_v1(region, t)):
                if rec["nhdplusid"] not in seen:
                    seen.add(rec["nhdplusid"])
                    out.append(rec)
        return out
    except Exception:  # noqa: BLE001
        return None


def watershed(nhdplusid: int, *, max_hops: int, max_reaches: int) -> Optional[dict]:
    """The bundle's walk, the outline of the tree's exact catchments and their cell-count area
    (``Dataset2.watershed`` with the tree's ids), or None where the bundle cannot answer for the
    whole tree (the reach is not in it, or the outline comes near a region it lacks)."""
    try:
        from shapely.geometry import shape
        ds = dataset()
        if ds is None or not ds._find("by_id", [int(nhdplusid)]):
            return None
        ws = ds.watershed(int(nhdplusid), max_reaches=max_reaches, max_hops=max_hops, with_ids=True)
        if ws.get("status") == "ok":
            if not ws.get("geometry") or _near_absent(shape(ws["geometry"])):
                return None
        elif ws.get("status") != "refused":
            return None
        return ws
    except Exception:  # noqa: BLE001
        return None


def sinuosity(nhdplusid: int) -> Optional[float]:
    """The reach's sinuosity from its original (unsimplified) line, rounded as
    ``geometry.line_sinuosity`` rounds, or None."""
    try:
        ds, tables = dataset(), values()
        if ds is None or tables is None:
            return None
        found = ds._find("by_id", [int(nhdplusid)])
        if not found or not tables.present("extras", found[0][0].vpu):
            return None
        region, rows = found[0]
        value = tables._table("extras", region.vpu).column("sinuosity")[int(rows[0])].as_py()
        return None if value is None else round(float(value), 3)
    except Exception:  # noqa: BLE001
        return None
