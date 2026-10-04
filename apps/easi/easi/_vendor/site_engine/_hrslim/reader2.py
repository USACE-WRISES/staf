"""Read slim NHDPlus HR version 2 (``hrslim.fmt2``) with the version 1 interface.

``Dataset2`` answers the same calls as ``hrslim.reader.Dataset`` and returns
tables of the same shape (line tables carry the 15 USGS layer 3 fields; catchment
tables carry ``x``/``y``/``rings``/``polys`` in 1e-5 degree units), so the API and
the feature builders work unchanged. Underneath, the walk runs on row offsets,
watershed outlines are assembled from shared borders, and areas are exact cell
counts. Catchment tolerance no longer applies: catchments are exact.
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

from . import arcs as arcs_mod
from . import fmt, fmt2
from .grid import Grid
from .reader import _Index, _overlaps, _qbox, _RowGroups

READ_COLUMNS = fmt2.LINE_COLUMNS + fmt.BBOX_COLUMNS + fmt.LINE_GEOM_COLUMNS
#: an id and hydroseq index beside a dataset whose ranges overlap across regions, as NHDPlus V2's
#: COMIDs and hydroseqs do (HR's carry their region in their digits): each value and the region
#: that holds it (``write_index``), so a lookup opens only that region. Without it every region
#: whose range covers the value is opened, and a delivered bundle fetches each region it opens.
INDEX_FILE = "idindex.parquet"
_INDEX_KEYS = (("by_id", 0), ("by_hs", 1))


class _Region:
    def __init__(self, root: Path, vpu: str, meta: dict, ensure=None):
        self.vpu = vpu
        self.meta = meta
        self.grid = Grid.from_dict(meta["grid"])
        self.lines_path = root / meta["lines"]["file"]
        self.cats_path = root / meta["catchments"]["file"]
        self.arcs_path = root / meta["arcs"]["file"]
        self.steps_path = root / meta["steps"]["file"]
        self.vpuid = meta.get("vpuid") or vpu
        self.ftype_from_fcode = meta.get("ftype_from_fcode", True)
        self._lock = threading.RLock()
        self._lines_rg: Optional[_RowGroups] = None
        self._topo: Optional[dict] = None
        self._cats: Optional[dict] = None
        self._ensure = ensure
        self._present = ensure is None

    def present(self) -> None:
        """The region's files are on disk when this returns: a delivered bundle fetches them the
        first time the region is read (``ensure(vpu)``, which raises when it cannot)."""
        if not self._present:
            self._ensure(self.vpu)
            self._present = True

    def lines_rg(self) -> _RowGroups:
        self.present()
        with self._lock:
            if self._lines_rg is None:
                self._lines_rg = _RowGroups(self.lines_path)
            return self._lines_rg

    def topo(self) -> dict:
        """Network arrays for the whole region, with id, hydroseq and parent indexes."""
        self.present()
        with self._lock:
            if self._topo is None:
                names = pq.read_schema(self.lines_path).names
                cols = list(fmt2.TOPOLOGY_COLUMNS) + [c for c in fmt2.MINOR_COLUMNS if c in names]
                t = pq.read_table(self.lines_path, columns=cols)
                ids = t.column("nhdplusid").to_numpy()
                hs = t.column("hydroseq").to_numpy()
                dn_step = t.column("dn_step").to_numpy().astype(np.int64)
                up_step = t.column("up_step").to_numpy().astype(np.int64)
                dn_out = t.column("dn_out").fill_null(0).to_numpy().astype(np.int64)
                up_out = t.column("up_out").fill_null(0).to_numpy().astype(np.int64)
                n = len(ids)
                has = dn_step != 0
                child = np.arange(n)[has]
                target = child + dn_step[has]
                if "dnminor_step" in t.column_names:
                    # a line that splits off a minor divergence is a parent of that branch too
                    mstep = t.column("dnminor_step").to_numpy().astype(np.int64)
                    mhas = mstep != 0
                    child = np.concatenate([child, np.arange(n)[mhas]])
                    target = np.concatenate([target, np.arange(n)[mhas] + mstep[mhas]])
                order = np.argsort(target, kind="stable")
                parent_ptr = np.concatenate([[0], np.cumsum(np.bincount(target, minlength=n))])
                self._topo = {"ids": ids, "hs": hs, "dn_step": dn_step, "up_step": up_step,
                              "dn_out": dn_out, "up_out": up_out,
                              "da": t.column("totdasqkm").to_numpy(zero_copy_only=False),
                              "by_id": _Index(ids), "by_hs": _Index(hs),
                              "parent_ptr": parent_ptr, "parents": child[order]}
            return self._topo

    def dn_hydroseq(self, rows: np.ndarray) -> np.ndarray:
        t = self.topo()
        step = t["dn_step"][rows]
        return np.where(step != 0, t["hs"][np.where(step != 0, rows + step, 0)], t["dn_out"][rows])

    def up_hydroseq(self, rows: np.ndarray) -> np.ndarray:
        t = self.topo()
        step = t["up_step"][rows]
        return np.where(step != 0, t["hs"][np.where(step != 0, rows + step, 0)], t["up_out"][rows])

    def cats(self) -> dict:
        """The region's catchment encoding, ids and boxes (loaded once)."""
        self.present()
        with self._lock:
            if self._cats is None:
                enc, ids, bbox = fmt2.read_encoded(self.cats_path, self.arcs_path, self.steps_path)
                self._cats = {"enc": enc, "ids": ids, "bbox": bbox, "by_id": _Index(ids)}
            return self._cats

    def in_range(self, key: str, values: Iterable[int]) -> bool:
        rng = self.meta.get(key)
        if not rng:
            return True
        lo, hi = rng
        return any(lo <= v <= hi for v in values)


class Dataset2:
    """A folder of slim NHDPlus HR version 2 files. ``ensure(vpu)``, when given, makes a region's
    files present before they are read (a delivered bundle fetches them on first use); the
    manifest and the cross-region links must be there from the start."""

    def __init__(self, root, *, cache_mb: int = 256, ensure=None):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("format") != fmt2.FORMAT_VERSION:
            raise ValueError("not a version 2 dataset")
        self.recipe = self.manifest.get("recipe", {})
        self.tolerances: list = []
        self.default_tolerance = 0.0
        self._regions = dict((v, _Region(self.root, v, m, ensure)) for v, m in sorted(self.manifest["vpus"].items()))
        self._cache: "OrderedDict[tuple, pa.Table]" = OrderedDict()
        self._cache_bytes = 0
        self._cache_cap = int(cache_mb) * 2 ** 20
        self._lock = threading.RLock()
        self._links = None
        self._index: Optional[dict] = None

    # ------------------------------------------------------------------ basics
    @property
    def vpus(self) -> list[str]:
        return list(self._regions)

    def tolerance(self, tol=None) -> float:
        """Catchments are exact; any tolerance asked for answers 0."""
        return 0.0

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

    def _line_rows(self, region: _Region, rows: np.ndarray, columns: tuple = READ_COLUMNS) -> Optional[pa.Table]:
        rows = np.unique(np.asarray(rows, dtype=np.int64))
        if not rows.size:
            return None
        rgs = region.lines_rg()
        groups = np.searchsorted(rgs.start, rows, side="right") - 1
        parts = []
        for g in np.unique(groups):
            local = rows[groups == g] - rgs.start[g]
            t = self._read_rg(region.lines_path, g, columns).take(pa.array(local))
            parts.append(t.append_column("_row", pa.array(rows[groups == g])))
        return pa.concat_tables(parts)

    def _as_v1(self, region: _Region, t: pa.Table, attributes: bool = True) -> pa.Table:
        """A version 1 shaped line table from version 2 rows (needs ``_row``)."""
        rows = t.column("_row").to_numpy()
        cols = {}
        if attributes:
            rc = t.column("reachcode")
            if not pa.types.is_string(rc.type):
                rc = pa.array([None if v is None else f"{v:014d}" for v in rc.to_pylist()], type=pa.string())
            fcode = t.column("fcode")
            ftype = pc.divide(fcode, pa.scalar(100, pa.int32())) if region.ftype_from_fcode else t.column("ftype")
            cols.update({
                "nhdplusid": t.column("nhdplusid"), "gnis_name": t.column("gnis_name"), "reachcode": rc,
                "lengthkm": t.column("lengthkm").cast(pa.float64()), "totdasqkm": t.column("totdasqkm"),
                "slope": t.column("slope").cast(pa.float64()), "fcode": fcode, "ftype": ftype,
                "streamorde": t.column("streamorde").cast(pa.int16()), "hydroseq": t.column("hydroseq"),
                "uphydroseq": pa.array(region.up_hydroseq(rows)), "dnhydroseq": pa.array(region.dn_hydroseq(rows)),
                "vpuid": pa.array([region.vpuid] * len(rows), type=pa.string()),
                "qama": t.column("qama").cast(pa.float64()),
            })
        else:
            cols["nhdplusid"] = t.column("nhdplusid")
        for name in fmt.BBOX_COLUMNS + fmt.LINE_GEOM_COLUMNS:
            cols[name] = t.column(name)
        return pa.table(cols)

    # ------------------------------------------------------------------ boxes
    def _candidates(self, west, south, east, north) -> list[_Region]:
        return [r for r in self._regions.values() if _overlaps(r.meta["bounds"], west, south, east, north)]

    def lines_in_bbox(self, west: float, south: float, east: float, north: float,
                      *, exact: bool = True) -> Optional[pa.Table]:
        q = _qbox(west, south, east, north)
        parts = []
        for region in self._candidates(west, south, east, north):
            rgs = region.lines_rg()
            for g in rgs.overlapping(q):
                t = self._read_rg(region.lines_path, g, READ_COLUMNS)
                t = t.append_column("_row", pa.array(np.arange(t.num_rows, dtype=np.int64) + rgs.start[g]))
                w, s, e, n = q
                mask = pc.and_(pc.and_(pc.less_equal(t["xmin"], e), pc.greater_equal(t["xmax"], w)),
                               pc.and_(pc.less_equal(t["ymin"], n), pc.greater_equal(t["ymax"], s)))
                t = t.filter(mask)
                if t.num_rows:
                    parts.append(self._as_v1(region, t))
        if not parts:
            return None
        table = pa.concat_tables(parts)
        if exact and table.num_rows:
            keep = shapely.intersects(fmt.decode_lines(table), shapely.box(west, south, east, north))
            table = table.filter(pa.array(keep))
        return table if table.num_rows else None

    def _cat_table(self, region: _Region, rows: np.ndarray) -> Optional[pa.Table]:
        """Catchment rows decoded to a version 1 shaped table; the vertices are
        degrees rounded to 1e-7 (about 1 cm), not 1e-5 units, so they stay exact."""
        rows = np.asarray(rows, dtype=np.int64)
        if not rows.size:
            return None
        c = region.cats()
        xs, ys, ring_off, poly_off, row_off = arcs_mod.rows_ragged(c["enc"], rows)
        lon, lat = region.grid.to_lonlat(xs, ys)
        q = np.round(np.column_stack([lon, lat]), 7)
        row_ring_off = poly_off[row_off]
        row_vert_off = ring_off[row_ring_off]
        cols = {"nhdplusid": pa.array(c["ids"][rows])}
        cols.update(fmt._bbox(fmt.quantize(q), row_vert_off))
        off = pa.array(np.asarray(row_vert_off, dtype=np.int32))
        cols.update({"x": pa.ListArray.from_arrays(off, pa.array(q[:, 0])),
                     "y": pa.ListArray.from_arrays(off, pa.array(q[:, 1])),
                     "rings": fmt._list(row_ring_off, np.diff(ring_off)),
                     "polys": fmt._list(row_off, np.diff(poly_off))})
        return pa.table(cols)

    def catchments_in_bbox(self, west: float, south: float, east: float, north: float,
                           tol=None) -> Optional[pa.Table]:
        w, s, e, n = _qbox(west, south, east, north)
        parts = []
        for region in self._candidates(west, south, east, north):
            b = region.cats()["bbox"]
            rows = np.nonzero((b[:, 0] <= e) & (b[:, 2] >= w) & (b[:, 1] <= n) & (b[:, 3] >= s))[0]
            t = self._cat_table(region, rows)
            if t is not None:
                parts.append(t)
        return pa.concat_tables(parts) if parts else None

    # ------------------------------------------------------------------ ids
    def _value_index(self) -> dict:
        """``{"by_id" | "by_hs": (sorted values, region codes, region names)}`` from ``INDEX_FILE``,
        or {} when the dataset has none (loaded once)."""
        with self._lock:
            if self._index is None:
                index = {}
                path = self.root / INDEX_FILE
                if path.exists():
                    t = pq.read_table(path)
                    names = json.loads(t.schema.metadata[b"regions"])
                    kind = t.column("key").to_numpy()
                    values = t.column("value").to_numpy()
                    codes = t.column("region").to_numpy()
                    for key, k in _INDEX_KEYS:
                        lo, hi = np.searchsorted(kind, [k, k + 1])
                        index[key] = (values[lo:hi], codes[lo:hi], names)
                self._index = index
            return self._index

    def _regions_holding(self, key: str, values: list[int]) -> list["_Region"]:
        """The regions that may hold ``values`` under ``key`` ("by_id" or "by_hs"), in region
        order: the index's when the dataset has one, else every region whose range covers one."""
        index = self._value_index().get(key)
        if index is None:
            range_key = {"by_id": "id_range", "by_hs": "hydroseq_range"}[key]
            return [r for r in self._regions.values() if r.in_range(range_key, values)]
        sorted_values, codes, names = index
        q = np.unique(np.asarray(values, dtype=np.int64))
        lo = np.searchsorted(sorted_values, q, side="left")
        hi = np.searchsorted(sorted_values, q, side="right")
        held = set()
        for a, b in zip(lo.tolist(), hi.tolist()):
            held.update(codes[a:b].tolist())
        vpus = set(names[c] for c in held)
        return [r for v, r in self._regions.items() if v in vpus]

    def _find(self, key: str, values: Iterable[int]) -> list[tuple[_Region, np.ndarray]]:
        values = [int(v) for v in values]
        out = []
        for region in self._regions_holding(key, values):
            rows = region.topo()[key].rows(values)
            if rows.size:
                out.append((region, rows))
        return out

    def _lines_where(self, key: str, values: Iterable[int], attributes: bool = True) -> Optional[pa.Table]:
        parts = []
        for region, rows in self._find(key, values):
            t = self._line_rows(region, rows)
            if t is not None:
                parts.append(self._as_v1(region, t, attributes))
        return pa.concat_tables(parts) if parts else None

    def reach(self, *, nhdplusid: Optional[int] = None, hydroseq: Optional[int] = None) -> Optional[pa.Table]:
        if nhdplusid is not None:
            return self._lines_where("by_id", [nhdplusid])
        if hydroseq is not None:
            return self._lines_where("by_hs", [hydroseq])
        raise ValueError("give nhdplusid or hydroseq")

    def flowlines_by_ids(self, ids: Iterable[int], *, attributes: bool = False) -> Optional[pa.Table]:
        return self._lines_where("by_id", ids, attributes)

    def catchments_by_ids(self, ids: Iterable[int], tol=None) -> Optional[pa.Table]:
        ids = [int(v) for v in ids]
        parts = []
        for region in self._regions_holding("by_id", ids):
            rows = region.cats()["by_id"].rows(ids)
            t = self._cat_table(region, rows)
            if t is not None:
                parts.append(t)
        return pa.concat_tables(parts) if parts else None

    # ------------------------------------------------------------------ walk
    def _links_index(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cross-region links sorted by target hydroseq: (targets, region keys, rows)."""
        with self._lock:
            if self._links is None:
                path = self.root / fmt2.LINKS_FILE
                if path.exists():
                    t = pq.read_table(path)
                    targets = t.column("target").to_numpy()
                    order = np.argsort(targets, kind="stable")
                    self._links = (targets[order], np.asarray(t.column("vpu").to_pylist(), dtype=object)[order],
                                   t.column("row").to_numpy()[order].astype(np.int64))
                else:
                    self._links = (np.empty(0, np.int64), np.empty(0, object), np.empty(0, np.int64))
            return self._links

    def _walk(self, nhdplusid: int, max_reaches: int, max_hops: int, levels: Optional[dict] = None):
        """The breadth-first walk; ``members`` maps region to the rows it holds, and
        ``levels`` (when given, a dict to fill) to each row's hops above the outlet."""
        nid = int(nhdplusid)
        found = self._find("by_id", [nid])
        if not found:
            return {"status": "failed", "reason": "reach not found", "nReaches": 0, "nHops": 0}, {}
        region, rows = found[0]
        targets, link_vpu, link_row = self._links_index()
        tree = {nid}
        members: dict = {region.vpu: [int(rows[0])]}
        if levels is not None:
            levels.clear()
            levels[region.vpu] = [0]
        frontier = [(region, int(rows[0]))]
        hops = 0
        while frontier:
            if hops >= max_hops or len(tree) >= max_reaches:
                return {"status": "refused", "nReaches": len(tree), "nHops": hops,
                        "reason": (f"watershed exceeds the engine budget ({len(tree)} reaches, "
                                   f"{hops} hops; the budget is {max_reaches} reaches and "
                                   f"{max_hops} hops)")}, members
            nxt = []
            for reg, row in frontier:
                t = reg.topo()
                h = int(t["hs"][row])
                if not h:
                    continue
                cands = [(reg, int(p)) for p in t["parents"][t["parent_ptr"][row]:t["parent_ptr"][row + 1]]]
                lo, hi = np.searchsorted(targets, h, "left"), np.searchsorted(targets, h, "right")
                for k in range(lo, hi):
                    other = self._regions.get(link_vpu[k])
                    if other is not None:
                        cands.append((other, int(link_row[k])))
                for preg, prow in cands:
                    pt = preg.topo()
                    pid = int(pt["ids"][prow])
                    if pid in tree:
                        continue
                    tree.add(pid)
                    members.setdefault(preg.vpu, []).append(prow)
                    if levels is not None:
                        levels.setdefault(preg.vpu, []).append(hops + 1)
                    if int(pt["hs"][prow]):
                        nxt.append((preg, prow))
            frontier = nxt
            hops += 1
        return {"status": "ok", "nReaches": len(tree), "nHops": hops, "ids": sorted(tree)}, members

    def upstream_tree(self, nhdplusid: int, *, max_reaches: int = 5000, max_hops: int = 200) -> dict:
        """The engine's upstream walk (breadth first by ``dnhydroseq`` membership, the
        budget checked before every level), across regions."""
        out, _ = self._walk(nhdplusid, max_reaches, max_hops)
        return out

    def watershed(self, nhdplusid: int, tol=None, *, max_reaches: int = 5000,
                  max_hops: int = 200, with_ids: bool = False) -> dict:
        """The walk plus the outline of the tree's catchments, assembled from shared
        borders; the area is the exact sum of their grid cells (EPSG:5070 metres).
        ``with_ids`` adds the tree's reach ids (``ids``), as the site engine needs."""
        t0 = time.perf_counter()
        tree, members = self._walk(nhdplusid, max_reaches, max_hops)
        t1 = time.perf_counter()
        out = {k: v for k, v in tree.items() if k != "ids" or with_ids}
        out["tolerance_m"] = 0.0
        out["walkMs"] = round(1000 * (t1 - t0), 1)
        if tree["status"] != "ok":
            return out
        area_m2 = 0.0
        n_cats = 0
        masks = []
        for vpu, line_rows in members.items():
            region = self._regions[vpu]
            ids = region.topo()["ids"][np.asarray(line_rows, dtype=np.int64)]
            c = region.cats()
            cat_rows = c["by_id"].rows(ids)
            if not cat_rows.size:
                continue
            member = np.zeros(c["enc"].n_rows, dtype=bool)
            member[cat_rows] = True
            n_cats += int(cat_rows.size)
            area_m2 += arcs_mod.area_cells(c["enc"], member) * region.grid.cell_area_m2
            masks.append((region, member))
        t2 = time.perf_counter()
        if not masks:
            out.update(status="failed", reason="no catchments for the upstream tree")
            return out
        pieces = []
        for region, member in masks:
            shape_cells = arcs_mod.outline(region.cats()["enc"], member)
            g = region.grid
            pieces.append(shapely.transform(shape_cells, lambda q, g=g: np.column_stack(g.cells_to_xy(q[:, 0], q[:, 1]))))
        merged = pieces[0] if len(pieces) == 1 else shapely.union_all(pieces)
        grid = masks[0][0].grid
        # vertex by vertex to 1e-7 degree (about 1 cm): grid vertices lie 10 m apart, so
        # rounding cannot make rings cross (set_precision would re-node the outline, ~10x slower)
        lonlat = shapely.transform(merged, lambda q: np.round(np.column_stack(grid.unproject(q[:, 0], q[:, 1])), 7))
        t3 = time.perf_counter()
        anchor = self._find("by_id", [int(nhdplusid)])
        vaa = None
        if anchor:
            reg, rows = anchor[0]
            v = reg.topo()["da"][rows[0]]
            vaa = None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
        area = round(area_m2 / 1e6, 4)
        out.update({
            "nCatchments": n_cats,
            "areaSqkm": area,
            "vaaAreaSqkm": vaa,
            "areaAgreement": round(area / vaa, 4) if vaa and vaa > 0 else None,
            "catchmentsMs": round(1000 * (t2 - t1), 1),
            "unionMs": round(1000 * (t3 - t2), 1),
            "geometry": json.loads(shapely.to_geojson(lonlat)),
        })
        return out

    # ------------------------------------------------------------------ QA, summary
    def qa_in_bbox(self, west: float, south: float, east: float, north: float) -> list[dict]:
        return []

    def summary(self) -> dict:
        total = 0
        vpus = []
        for v, region in self._regions.items():
            m = region.meta
            sizes = {"lines": m["lines"]["bytes"], "catchments": m["catchments"]["bytes"],
                     "arcs": m["arcs"]["bytes"] + m["steps"]["bytes"]}
            total += sum(sizes.values())
            vpus.append({"vpu": v, "bounds": m["bounds"], "lines": m["lines"]["rows"],
                         "catchments": m["catchments"]["rows"], "bytes": sizes, "qaBoxes": []})
        return {"format": fmt2.FORMAT_VERSION, "recipe": self.recipe, "tolerances": [],
                "defaultTolerance": 0.0, "vpus": vpus, "bytes": total, "built": self.manifest.get("built")}


def write_index(root) -> dict:
    """Write ``INDEX_FILE`` beside the dataset in ``root`` (its files on disk): every flowline and
    catchment id and every hydroseq with the region that holds it, sorted by key and value.
    Region codes index the ``regions`` list in the file's metadata. Returns the counts."""
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    names = sorted(manifest["vpus"])
    parts: dict = dict((k, ([], [])) for _key, k in _INDEX_KEYS)
    for code, vpu in enumerate(names):
        meta = manifest["vpus"][vpu]
        lines = pq.read_table(root / meta["lines"]["file"], columns=["nhdplusid", "hydroseq"])
        cats = pq.read_table(root / meta["catchments"]["file"], columns=["nhdplusid"])
        ids = np.union1d(lines.column("nhdplusid").to_numpy(), cats.column("nhdplusid").to_numpy())
        hs = np.unique(lines.column("hydroseq").to_numpy())
        for k, vals in ((0, ids), (1, hs)):
            parts[k][0].append(vals.astype(np.int64))
            parts[k][1].append(np.full(len(vals), code, dtype=np.int16))
    kind, values, codes = [], [], []
    for _key, k in _INDEX_KEYS:
        v = np.concatenate(parts[k][0]) if parts[k][0] else np.empty(0, np.int64)
        c = np.concatenate(parts[k][1]) if parts[k][1] else np.empty(0, np.int16)
        order = np.argsort(v, kind="stable")
        kind.append(np.full(len(v), k, dtype=np.int8))
        values.append(v[order])
        codes.append(c[order])
    table = pa.table({"key": np.concatenate(kind), "value": np.concatenate(values),
                      "region": np.concatenate(codes)}).replace_schema_metadata({"regions": json.dumps(names)})
    pq.write_table(table, root / INDEX_FILE, compression="zstd", compression_level=19,
                   column_encoding={"value": "DELTA_BINARY_PACKED"}, use_dictionary=False)
    return {"regions": len(names), "ids": int(len(values[0])), "hydroseqs": int(len(values[1])),
            "bytes": (root / INDEX_FILE).stat().st_size}
