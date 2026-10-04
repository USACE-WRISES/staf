"""Read the slim NHDPlus HR files: map boxes, ids, the upstream walk, watersheds.

A ``Dataset`` is a folder holding ``manifest.json`` and the per-VPU files that
``hrslim.fmt`` describes. Everything loads lazily: a VPU's row-group extents and
its id/hydroseq index are read the first time a query touches that VPU, and
decoded row groups sit in a small byte-bounded cache. Answers carry the same
fields and GeoJSON shapes as the USGS layer 3 and layer 10 answers the STAF site
engine parses today.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from shapely.errors import GEOSException

from . import fmt

LINE_COLUMNS = fmt.LINE_ATTR_NAMES + fmt.BBOX_COLUMNS + fmt.LINE_GEOM_COLUMNS
CATCHMENT_COLUMNS = ("nhdplusid",) + fmt.BBOX_COLUMNS + fmt.POLYGON_GEOM_COLUMNS
#: Largest map box answered (square degrees), the engine's own display cap.
MAX_LINES_BOX_DEG2 = 0.05
MAX_CATCHMENTS_BOX_DEG2 = 0.02

_proj_lock = threading.Lock()
_proj: dict = {}


def _transformer(src: int, dst: int):
    from pyproj import Transformer
    with _proj_lock:
        key = (src, dst)
        if key not in _proj:
            _proj[key] = Transformer.from_crs(src, dst, always_xy=True)
        return _proj[key]


def _project(geoms, src: int, dst: int):
    tr = _transformer(src, dst)
    return shapely.transform(geoms, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))


def _qbox(west: float, south: float, east: float, north: float) -> tuple[int, int, int, int]:
    s = fmt.SCALE
    return (math.floor(west * s), math.floor(south * s), math.ceil(east * s), math.ceil(north * s))


def _overlaps(bounds: Iterable[float], west: float, south: float, east: float, north: float) -> bool:
    w, s, e, n = bounds
    return w <= east and e >= west and s <= north and n >= south


class _RowGroups:
    """Row counts, row offsets and bounding extents (scaled ints) of one file."""

    def __init__(self, path: Path):
        md = pq.read_metadata(path)
        leaf = dict((md.row_group(0).column(j).path_in_schema, j)
                    for j in range(md.row_group(0).num_columns)) if md.num_row_groups else {}
        self.n = md.num_row_groups
        self.rows = np.array([md.row_group(i).num_rows for i in range(self.n)], dtype=np.int64)
        self.start = np.concatenate([[0], np.cumsum(self.rows)])
        ext = np.zeros((self.n, 4), dtype=np.int64)
        for i in range(self.n):
            rg = md.row_group(i)
            ext[i, 0] = rg.column(leaf["xmin"]).statistics.min
            ext[i, 1] = rg.column(leaf["ymin"]).statistics.min
            ext[i, 2] = rg.column(leaf["xmax"]).statistics.max
            ext[i, 3] = rg.column(leaf["ymax"]).statistics.max
        self.ext = ext

    def overlapping(self, qbox: tuple[int, int, int, int]) -> np.ndarray:
        w, s, e, n = qbox
        m = (self.ext[:, 0] <= e) & (self.ext[:, 2] >= w) & (self.ext[:, 1] <= n) & (self.ext[:, 3] >= s)
        return np.nonzero(m)[0]


class _Index:
    """Sorted lookups over one int64 column: value -> row positions."""

    def __init__(self, values: np.ndarray):
        self.order = np.argsort(values, kind="stable")
        self.sorted = values[self.order]

    def rows(self, wanted: Iterable[int]) -> np.ndarray:
        w = np.unique(np.asarray(list(wanted), dtype=np.int64))
        if not w.size:
            return np.empty(0, dtype=np.int64)
        lo = np.searchsorted(self.sorted, w, side="left")
        hi = np.searchsorted(self.sorted, w, side="right")
        hit = hi > lo
        if not hit.any():
            return np.empty(0, dtype=np.int64)
        return np.concatenate([self.order[a:b] for a, b in zip(lo[hit], hi[hit])])


class _Vpu:
    def __init__(self, root: Path, vpu: str, meta: dict):
        self.vpu = vpu
        self.meta = meta
        self.lines_path = root / meta["lines"]["file"]
        self.cat_paths = dict((float(k), root / v["file"]) for k, v in meta["catchments"].items())
        qa = meta.get("qa") or {}
        self.qa_path = root / qa["file"] if qa.get("file") else None
        self._lock = threading.RLock()
        self._lines_rg: Optional[_RowGroups] = None
        self._cat_rg: dict = {}
        self._topo: Optional[dict] = None
        self._cat_ids: dict = {}

    def lines_rg(self) -> _RowGroups:
        with self._lock:
            if self._lines_rg is None:
                self._lines_rg = _RowGroups(self.lines_path)
            return self._lines_rg

    def cat_rg(self, tol: float) -> _RowGroups:
        with self._lock:
            if tol not in self._cat_rg:
                self._cat_rg[tol] = _RowGroups(self.cat_paths[tol])
            return self._cat_rg[tol]

    def topo(self) -> dict:
        """nhdplusid, hydroseq and dnhydroseq of every line, with their indexes."""
        with self._lock:
            if self._topo is None:
                t = pq.read_table(self.lines_path, columns=["nhdplusid", "hydroseq", "dnhydroseq"])
                ids = t.column("nhdplusid").to_numpy()
                hs = t.column("hydroseq").to_numpy()
                dn = t.column("dnhydroseq").to_numpy()
                self._topo = {"ids": ids, "hs": hs, "dn": dn, "by_id": _Index(ids),
                              "by_hs": _Index(hs), "by_dn": _Index(dn)}
            return self._topo

    def cat_ids(self, tol: float) -> _Index:
        with self._lock:
            if tol not in self._cat_ids:
                ids = pq.read_table(self.cat_paths[tol], columns=["nhdplusid"]).column("nhdplusid").to_numpy()
                self._cat_ids[tol] = _Index(ids)
            return self._cat_ids[tol]

    def in_range(self, key: str, values: Iterable[int]) -> bool:
        rng = self.meta.get(key)
        if not rng:
            return True
        lo, hi = rng
        return any(lo <= v <= hi for v in values)


class Dataset:
    """A folder of slim NHDPlus HR files (see ``hrslim.fmt``)."""

    def __init__(self, root, *, cache_mb: int = 256):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.recipe = self.manifest.get("recipe", {})
        tols = self.recipe.get("catchment_tolerances_m") or []
        self.tolerances = sorted(float(t) for t in tols)
        self.default_tolerance = float(self.recipe.get("default_catchment_tolerance_m")
                                       or (self.tolerances[-1] if self.tolerances else 0.0))
        self._vpus = dict((v, _Vpu(self.root, v, m)) for v, m in sorted(self.manifest["vpus"].items()))
        self._cache: "OrderedDict[tuple, pa.Table]" = OrderedDict()
        self._cache_bytes = 0
        self._cache_cap = int(cache_mb) * 2 ** 20
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ basics
    @property
    def vpus(self) -> list[str]:
        return list(self._vpus)

    def tolerance(self, tol=None) -> float:
        if tol in (None, ""):
            return self.default_tolerance
        t = float(tol)
        if t not in self.tolerances:
            raise ValueError(f"catchment tolerance {tol} not in this dataset ({self.tolerances})")
        return t

    def _read_rg(self, path: Path, rg: int, columns: tuple) -> pa.Table:
        key = (str(path), int(rg), columns)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        table = pq.ParquetFile(path).read_row_group(int(rg), columns=list(columns))
        with self._lock:
            if key not in self._cache:
                self._cache[key] = table
                self._cache_bytes += table.nbytes
                while self._cache_bytes > self._cache_cap and len(self._cache) > 1:
                    _, old = self._cache.popitem(last=False)
                    self._cache_bytes -= old.nbytes
        return table

    def _take(self, path: Path, rgs: _RowGroups, rows: np.ndarray, columns: tuple) -> pa.Table:
        rows = np.unique(np.asarray(rows, dtype=np.int64))
        if not rows.size:
            return None
        groups = np.searchsorted(rgs.start, rows, side="right") - 1
        parts = []
        for g in np.unique(groups):
            local = rows[groups == g] - rgs.start[g]
            parts.append(self._read_rg(path, g, columns).take(pa.array(local)))
        return pa.concat_tables(parts)

    @staticmethod
    def _box_filter(table: pa.Table, q: tuple[int, int, int, int]) -> pa.Table:
        w, s, e, n = q
        mask = pc.and_(pc.and_(pc.less_equal(table["xmin"], e), pc.greater_equal(table["xmax"], w)),
                       pc.and_(pc.less_equal(table["ymin"], n), pc.greater_equal(table["ymax"], s)))
        return table.filter(mask)

    def _candidates(self, west, south, east, north) -> list[_Vpu]:
        return [v for v in self._vpus.values() if _overlaps(v.meta["bounds"], west, south, east, north)]

    # ------------------------------------------------------------------ boxes
    def lines_in_bbox(self, west: float, south: float, east: float, north: float,
                      *, exact: bool = True) -> pa.Table:
        """Network flowlines intersecting the box (the service's envelope query)."""
        q = _qbox(west, south, east, north)
        parts = []
        for vp in self._candidates(west, south, east, north):
            rgs = vp.lines_rg()
            for g in rgs.overlapping(q):
                t = self._box_filter(self._read_rg(vp.lines_path, g, LINE_COLUMNS), q)
                if t.num_rows:
                    parts.append(t)
        if not parts:
            return None
        table = pa.concat_tables(parts)
        if exact and table.num_rows:
            keep = shapely.intersects(fmt.decode_lines(table), shapely.box(west, south, east, north))
            table = table.filter(pa.array(keep))
        return table if table.num_rows else None

    def catchments_in_bbox(self, west: float, south: float, east: float, north: float,
                           tol=None) -> pa.Table:
        tol = self.tolerance(tol)
        q = _qbox(west, south, east, north)
        parts = []
        for vp in self._candidates(west, south, east, north):
            if tol not in vp.cat_paths:
                continue
            rgs = vp.cat_rg(tol)
            for g in rgs.overlapping(q):
                t = self._box_filter(self._read_rg(vp.cat_paths[tol], g, CATCHMENT_COLUMNS), q)
                if t.num_rows:
                    parts.append(t)
        return pa.concat_tables(parts) if parts else None

    # ------------------------------------------------------------------ ids
    def _lines_where(self, key: str, values: Iterable[int], columns: tuple = LINE_COLUMNS) -> Optional[pa.Table]:
        values = [int(v) for v in values]
        range_key = {"by_id": "id_range", "by_hs": "hydroseq_range", "by_dn": "dnhydroseq_range"}[key]
        parts = []
        for vp in self._vpus.values():
            if not vp.in_range(range_key, values):
                continue
            rows = vp.topo()[key].rows(values)
            if rows.size:
                t = self._take(vp.lines_path, vp.lines_rg(), rows, columns)
                if t is not None:
                    parts.append(t)
        return pa.concat_tables(parts) if parts else None

    def reach(self, *, nhdplusid: Optional[int] = None, hydroseq: Optional[int] = None) -> Optional[pa.Table]:
        if nhdplusid is not None:
            return self._lines_where("by_id", [nhdplusid])
        if hydroseq is not None:
            return self._lines_where("by_hs", [hydroseq])
        raise ValueError("give nhdplusid or hydroseq")

    def flowlines_by_ids(self, ids: Iterable[int], *, attributes: bool = False) -> Optional[pa.Table]:
        cols = LINE_COLUMNS if attributes else ("nhdplusid",) + fmt.BBOX_COLUMNS + fmt.LINE_GEOM_COLUMNS
        return self._lines_where("by_id", ids, cols)

    def catchments_by_ids(self, ids: Iterable[int], tol=None) -> Optional[pa.Table]:
        tol = self.tolerance(tol)
        ids = [int(v) for v in ids]
        parts = []
        for vp in self._vpus.values():
            if tol not in vp.cat_paths or not vp.in_range("id_range", ids):
                continue
            rows = vp.cat_ids(tol).rows(ids)
            if rows.size:
                t = self._take(vp.cat_paths[tol], vp.cat_rg(tol), rows, CATCHMENT_COLUMNS)
                if t is not None:
                    parts.append(t)
        return pa.concat_tables(parts) if parts else None

    # ------------------------------------------------------------------ walk
    def upstream_tree(self, nhdplusid: int, *, max_reaches: int = 5000,
                      max_hops: int = 200) -> dict:
        """The engine's upstream walk (``delineate._tree_and_catchments``) in one
        call: breadth-first by ``dnhydroseq`` membership, the same budget check
        before every level, ``status`` ``ok``, ``refused`` or ``failed``."""
        nid = int(nhdplusid)
        anchor = None
        for vp in self._vpus.values():
            if not vp.in_range("id_range", [nid]):
                continue
            rows = vp.topo()["by_id"].rows([nid])
            if rows.size:
                anchor = (vp, int(rows[0]))
                break
        if anchor is None:
            return {"status": "failed", "reason": "reach not found", "nReaches": 0, "nHops": 0}
        tree = {nid}
        frontier = [anchor]
        hops = 0
        while frontier:
            if hops >= max_hops or len(tree) >= max_reaches:
                return {"status": "refused", "nReaches": len(tree), "nHops": hops,
                        "reason": (f"watershed exceeds the engine budget ({len(tree)} reaches, "
                                   f"{hops} hops; the budget is {max_reaches} reaches and "
                                   f"{max_hops} hops)")}
            wanted = set()
            for vp, row in frontier:
                h = int(vp.topo()["hs"][row])
                if h:
                    wanted.add(h)
            nxt = []
            if wanted:
                for vp in self._vpus.values():
                    if not vp.in_range("dnhydroseq_range", wanted):
                        continue
                    topo = vp.topo()
                    for row in topo["by_dn"].rows(wanted):
                        rid = int(topo["ids"][row])
                        if rid in tree:
                            continue
                        tree.add(rid)
                        if int(topo["hs"][row]):
                            nxt.append((vp, int(row)))
            frontier = nxt
            hops += 1
        return {"status": "ok", "nReaches": len(tree), "nHops": hops, "ids": sorted(tree)}

    def watershed(self, nhdplusid: int, tol=None, *, max_reaches: int = 5000,
                  max_hops: int = 200) -> dict:
        """The walk plus the union of the tree's catchments (EPSG:5070 area)."""
        tol = self.tolerance(tol)
        t0 = time.perf_counter()
        tree = self.upstream_tree(nhdplusid, max_reaches=max_reaches, max_hops=max_hops)
        t1 = time.perf_counter()
        out = {k: v for k, v in tree.items() if k != "ids"}
        out["tolerance_m"] = tol
        out["walkMs"] = round(1000 * (t1 - t0), 1)
        if tree["status"] != "ok":
            return out
        cats = self.catchments_by_ids(tree["ids"], tol)
        t2 = time.perf_counter()
        if cats is None or not cats.num_rows:
            out.update(status="failed", reason="no catchments for the upstream tree")
            return out
        polys = _project(fmt.decode_polygons(cats), 4269, 5070)
        try:
            union = shapely.union_all(polys)
        except GEOSException:
            union = shapely.union_all(shapely.make_valid(polys))
        t3 = time.perf_counter()
        anchor = self.reach(nhdplusid=int(nhdplusid))
        vaa = None
        if anchor is not None and anchor.num_rows:
            vaa = anchor.column("totdasqkm")[0].as_py()
        area = round(float(union.area) / 1e6, 4)
        out.update({
            "nCatchments": int(cats.num_rows),
            "areaSqkm": area,
            "vaaAreaSqkm": vaa,
            "areaAgreement": round(area / vaa, 4) if vaa and vaa > 0 else None,
            "catchmentsMs": round(1000 * (t2 - t1), 1),
            "unionMs": round(1000 * (t3 - t2), 1),
            "geometry": json.loads(shapely.to_geojson(
                shapely.set_precision(_project(union, 5070, 4269), 1e-6))),
        })
        return out

    # ------------------------------------------------------------------ QA
    def qa_in_bbox(self, west: float, south: float, east: float, north: float) -> list[dict]:
        """Original full-precision geometry stored for the QA boxes."""
        feats = []
        box = shapely.box(west, south, east, north)
        for vp in self._candidates(west, south, east, north):
            if vp.qa_path is None or not vp.qa_path.exists():
                continue
            t = self._read_rg(vp.qa_path, 0, ("kind", "nhdplusid", "wkb"))   # one row group
            geoms = shapely.from_wkb(t.column("wkb").to_numpy(zero_copy_only=False))
            keep = shapely.intersects(geoms, box)
            kinds = t.column("kind").to_pylist()
            ids = t.column("nhdplusid").to_pylist()
            for i in np.nonzero(keep)[0]:
                feats.append({"type": "Feature",
                              "geometry": json.loads(shapely.to_geojson(geoms[i])),
                              "properties": {"kind": kinds[i], "nhdplusid": ids[i]}})
        return feats

    # ------------------------------------------------------------------ summary
    def summary(self) -> dict:
        files = 0
        vpus = []
        for v, vp in self._vpus.items():
            m = vp.meta
            size = m["lines"]["bytes"] + sum(c["bytes"] for c in m["catchments"].values())
            files += size
            vpus.append({"vpu": v, "bounds": m["bounds"], "lines": m["lines"]["rows"],
                         "catchments": max((c["rows"] for c in m["catchments"].values()), default=0),
                         "bytes": dict([("lines", m["lines"]["bytes"])]
                                       + [(f"catchments{int(float(k))}", c["bytes"])
                                          for k, c in m["catchments"].items()]),
                         "qaBoxes": (m.get("qa") or {}).get("boxes", [])})
        return {"format": self.manifest.get("format"), "recipe": self.recipe,
                "tolerances": self.tolerances, "defaultTolerance": self.default_tolerance,
                "vpus": vpus, "bytes": files, "built": self.manifest.get("built")}


# ---------------------------------------------------------------------- GeoJSON
def line_features(table: Optional[pa.Table], *, attributes: bool = True) -> list[dict]:
    """Layer-3-shaped GeoJSON features (``innetwork`` is always 1)."""
    if table is None or not table.num_rows:
        return []
    geoms = fmt.line_geojson(table)
    names = [n for n in fmt.LINE_ATTR_NAMES if n in table.column_names] if attributes else ["nhdplusid"]
    cols = dict((n, table.column(n).to_pylist()) for n in names)
    feats = []
    for i, geom in enumerate(geoms):
        props = {}
        for name in fmt.SERVICE_FIELDS:
            if name == "innetwork":
                if attributes:
                    props[name] = 1
            elif name in cols:
                props[name] = cols[name][i]
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return feats


def catchment_features(table: Optional[pa.Table]) -> list[dict]:
    if table is None or not table.num_rows:
        return []
    geoms = fmt.polygon_geojson(table)
    ids = table.column("nhdplusid").to_pylist()
    return [{"type": "Feature", "geometry": g, "properties": {"nhdplusid": ids[i]}}
            for i, g in enumerate(geoms)]
