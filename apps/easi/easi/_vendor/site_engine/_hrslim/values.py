"""Watershed values from the precomputed per-catchment tables (bundle v2, Phase 3).

The builder (``tools/hr-slim/hrbuild/zonal.py``) counts, for every catchment, its 10 m cells by
NLCD 2021 class and the impervious 2021 and 2001 values under them (``lc2_<vpu>.parquet``), and
the cells of its 100 m riparian strip in pieces keyed by ``k`` (``rip2_<vpu>.parquet``). A
watershed's values are sums over the catchments of its walk: every catchment counts whole for the
watershed, and its riparian pieces with ``k`` at most its level above the outlet (the site engine
buffers the tree's flowlines only, so a neighbour's buffer counts once the outlet lies below the
neighbour). Results carry the site engine's names (``metrics/landcover.py``): ``cropPctWatershed``,
``imperviousPctRiparian``, ``imperviousPct2001Watershed`` and so on.

``ValueTables`` reads the builder's exact files or their lean encoding (``hrslim.lean``, whole
percents and compact units, detected from each file's metadata); both decode to the same arrays.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pyarrow.parquet as pq

from . import lean

NLCD_CODES = (11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95)
LC_COLUMNS = tuple(f"lc{c}" for c in NLCD_CODES) + ("lc_none",)
IMP_COLUMNS = ("imp21_sum", "imp21_n", "imp01_sum", "imp01_n")
#: The site engine's land-cover stems (``_CLASS_KEYWORDS``) as NLCD codes.
ENGINE_STEMS = {"crop": (82,), "hayPasture": (81,), "forest": (41, 42, 43), "shrub": (52,),
                "grassland": (71,), "woodyWetland": (90,), "herbWetland": (95,)}
_STEM_INDEX = dict((stem, [NLCD_CODES.index(c) for c in codes]) for stem, codes in ENGINE_STEMS.items())


def summarize(counts: np.ndarray, imp: np.ndarray, label: str) -> dict:
    """Engine-shaped values from summed class counts (17) and impervious sums (4)."""
    counts = np.asarray(counts, dtype=np.float64)
    total = counts.sum()
    covered = total - counts[16]
    out: dict = {}
    if covered <= 0:
        return {f"landcover{label}Unavailable": True}
    for stem, idx in _STEM_INDEX.items():
        out[f"{stem}Pct{label}"] = round(100.0 * counts[idx].sum() / covered, 2)
    out[f"imperviousPct{label}"] = round(float(imp[0]) / imp[1], 2) if imp[1] else None
    out[f"imperviousPct2001{label}"] = round(float(imp[2]) / imp[3], 2) if imp[3] else None
    out[f"cells{label}"] = int(round(total))
    if counts[16] > 0:
        out[f"coveredFraction{label}"] = round(covered / total, 4)
    return out


class ValueTables:
    """The per-catchment tables of one folder, loaded per region on first use. ``ensure(vpu)``,
    when given, makes a region's tables present before they are read (a delivered bundle)."""

    def __init__(self, folder, *, ensure=None):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        self._lc: dict = {}
        self._rip: dict = {}
        self._ensure = ensure
        self._ensured: set = set()

    def _need(self, vpu: str) -> None:
        if self._ensure is not None and vpu not in self._ensured:
            self._ensure(vpu)
            self._ensured.add(vpu)

    def present(self, prefix: str, vpu: str) -> bool:
        """Whether the region has the ``<prefix>2_<vpu>.parquet`` table (False when a delivered
        region cannot be had now)."""
        try:
            self._need(vpu)
        except Exception:  # noqa: BLE001 - not here now is not here
            return False
        return (self.folder / f"{prefix}2_{vpu}.parquet").exists()

    def has(self, vpu: str) -> bool:
        return self.present("lc", vpu)

    def lc(self, vpu: str):
        """``(class counts 17, impervious sums 4)`` per catchment row, float64."""
        self._need(vpu)
        with self._lock:
            if vpu not in self._lc:
                t = pq.read_table(self.folder / f"lc2_{vpu}.parquet")
                if lean.is_lean(t):
                    self._lc[vpu] = lean.decode_lc(t)
                else:
                    counts = np.column_stack([t.column(c).to_numpy() for c in LC_COLUMNS]).astype(np.float64)
                    imp = np.column_stack([t.column(c).to_numpy() for c in IMP_COLUMNS]).astype(np.float64)
                    self._lc[vpu] = (counts, imp)
            return self._lc[vpu]

    def _table(self, prefix: str, vpu: str):
        key = (prefix, vpu)
        self._need(vpu)
        with self._lock:
            if key not in self._lc:
                t = pq.read_table(self.folder / f"{prefix}2_{vpu}.parquet")
                if prefix == "extras" and lean.is_lean(t):
                    t = lean.decode_extras(t)
                self._lc[key] = t
            return self._lc[key]

    def roads(self, vpu: str) -> np.ndarray:
        t = self._table("roads", vpu)
        return lean.decode_roads(t) if lean.is_lean(t) else t.column("road_m").to_numpy().astype(np.float64)

    def dams(self, vpu: str) -> dict:
        t = self._table("dams", vpu)
        if lean.is_lean(t):
            return lean.decode_dams(t)
        return dict((c, t.column(c).to_numpy()) for c in ("dams", "normal_acft", "normal_missing", "nid_acft"))

    def soils(self, vpu: str) -> dict:
        t = self._table("soils", vpu)
        if lean.is_lean(t):
            return lean.decode_soils(t)
        return dict((c, t.column(c).to_numpy()) for c in ("k_sum", "k_cells", "ssurgo_cells", "statsgo_cells"))

    def xings(self, vpu: str):
        t = self._table("xings", vpu)
        return tuple(t.column(c).to_numpy().astype(np.int64) for c in ("line_row", "road", "x_dm", "y_dm"))

    def rip(self, vpu: str):
        self._need(vpu)
        with self._lock:
            if vpu not in self._rip:
                t = pq.read_table(self.folder / f"rip2_{vpu}.parquet")
                row, k = t.column("row").to_numpy().astype(np.int64), t.column("k").to_numpy().astype(np.int64)
                if lean.is_lean(t):
                    counts, imp = lean.decode_lc(t)
                else:
                    counts = np.column_stack([t.column(c).to_numpy() for c in LC_COLUMNS]).astype(np.float64)
                    imp = np.column_stack([t.column(c).to_numpy() for c in IMP_COLUMNS]).astype(np.float64)
                self._rip[vpu] = (row, k, counts, imp)
            return self._rip[vpu]


def _catchment_rows(region, line_rows: np.ndarray) -> np.ndarray:
    """The catchment row of each line row (-1 when a line has no catchment)."""
    ids = region.topo()["ids"][np.asarray(line_rows, dtype=np.int64)]
    index = region.cats()["by_id"]
    pos = np.searchsorted(index.sorted, ids)
    pos = np.minimum(pos, len(index.sorted) - 1)
    return np.where(index.sorted[pos] == ids, index.order[pos], -1)


def watershed_landcover(ds, tables: ValueTables, nhdplusid: int, *, max_reaches: int = 3000,
                        max_hops: int = 190) -> dict:
    """Land cover of the walked watershed and of its 100 m riparian strip, from the tables."""
    levels: dict = {}
    tree, members = ds._walk(int(nhdplusid), max_reaches, max_hops, levels)
    out = {k: v for k, v in tree.items() if k != "ids"}
    if tree["status"] != "ok":
        return out
    ws_counts, ws_imp = np.zeros(17), np.zeros(4)
    rp_counts, rp_imp = np.zeros(17), np.zeros(4)
    missing = []
    for vpu, line_rows in members.items():
        if not tables.has(vpu):
            missing.append(vpu)
            continue
        region = ds._regions[vpu]
        cat_rows = _catchment_rows(region, line_rows)
        lv = np.asarray(levels[vpu], dtype=np.int64)
        ok = cat_rows >= 0
        cat_rows, lv = cat_rows[ok], lv[ok]
        counts, imp = tables.lc(vpu)
        ws_counts += counts[cat_rows].sum(axis=0)
        ws_imp += imp[cat_rows].sum(axis=0)
        prow, pk, pcounts, pimp = tables.rip(vpu)
        level_of = np.full(len(counts), -1, dtype=np.int64)
        level_of[cat_rows] = lv
        take = level_of[prow] >= pk
        rp_counts += pcounts[take].sum(axis=0)
        rp_imp += pimp[take].sum(axis=0)
    if missing:
        out.update(status="failed", reason=f"no precomputed land cover for region(s) {', '.join(missing)}")
        return out
    out.update(summarize(ws_counts, ws_imp, "Watershed"))
    out.update(summarize(rp_counts, rp_imp, "Riparian"))
    return out


def watershed_values(ds, tables: ValueTables, nhdplusid: int, *, max_reaches: int = 3000,
                     max_hops: int = 190) -> dict:
    """Land cover (watershed and riparian), roads, road-stream crossings and dams of the walked
    watershed, with the site engine's names and rounding."""
    out = watershed_landcover(ds, tables, nhdplusid, max_reaches=max_reaches, max_hops=max_hops)
    if out.get("status") != "ok":
        return out
    levels: dict = {}
    _, members = ds._walk(int(nhdplusid), max_reaches, max_hops, levels)
    area_m2 = 0.0
    road_m = 0.0
    dam_n, normal, missing, nid_acft = 0, 0.0, 0, 0.0
    crossings = 0
    k_sum, k_cells, ssurgo = 0.0, 0, 0
    for vpu, line_rows in members.items():
        region = ds._regions[vpu]
        cat_rows = _catchment_rows(region, line_rows)
        cat_rows = cat_rows[cat_rows >= 0]
        area_m2 += float(region.cats()["enc"].area2[cat_rows].sum()) / 2.0 * region.grid.cell_area_m2
        road_m += float(tables.roads(vpu)[cat_rows].sum())
        d = tables.dams(vpu)
        dam_n += int(d["dams"][cat_rows].sum())
        normal += float(d["normal_acft"][cat_rows].sum())
        missing += int(d["normal_missing"][cat_rows].sum())
        nid_acft += float(d["nid_acft"][cat_rows].sum())
        so = tables.soils(vpu)
        k_sum += float(so["k_sum"][cat_rows].sum())
        k_cells += int(so["k_cells"][cat_rows].sum())
        ssurgo += int(so["ssurgo_cells"][cat_rows].sum())
        lrow, road, x, y = tables.xings(vpu)
        take = np.isin(lrow, np.asarray(line_rows, dtype=np.int64))
        if take.any():
            crossings += len(set(zip(road[take].tolist(), x[take].tolist(), y[take].tolist())))
    area = area_m2 / 1e6
    km = road_m / 1000.0
    out.update({
        "areaSqkm": round(area, 4),
        "roadLengthKm": round(km, 3), "roadDensity": round(km / area, 4),
        "roadCrossings": int(crossings), "roadCrossingDensity": round(crossings / area, 4),
        "damCount": dam_n, "damDensityPerSqkm": round(dam_n / area, 4),
        "damStorageAcreFt": round(normal, 1), "damStoragePerSqkm": round(normal / area, 3),
        "damNidStorageAcreFt": round(nid_acft, 1), "damNidStoragePerSqkm": round(nid_acft / area, 3),
        "damsWithoutNormalStorage": missing,
        "soilKFactor": round(k_sum / k_cells, 3) if k_cells else None,
        "soilKCoverage": round(k_cells / ssurgo, 4) if ssurgo else None,
    })
    return out


_SAMPLES = (0.05, 0.5, 0.95)


def flowline_extras(ds, tables: ValueTables, nhdplusid: int, fraction: float = 0.5) -> Optional[dict]:
    """Per-flowline lookups at the sample nearest ``fraction`` (0 = upstream end, 1 = downstream
    end): sinuosity, the HUC12, the ATTAINS unit at the point and the nearest within 2 km (the
    record shape of EASI's ``attains.impairment_*``), and the line's 150 m NWI strip."""
    found = ds._find("by_id", [int(nhdplusid)])
    if not found:
        return None
    region, rows = found[0]
    row = int(rows[0])
    t = tables._table("extras", region.vpu)
    au = tables._table("au", region.vpu)
    tag = ("05", "50", "95")[int(np.argmin([abs(fraction - s) for s in _SAMPLES]))]

    def col(name):
        return t.column(name)[row].as_py()

    def unit(index, distance, match):
        if index is None or index < 0:
            return {}
        rec = dict((c, au.column(c)[index].as_py()) for c in au.column_names)
        return {"assessment_unit": rec.get("assessment_unit"), "assessment_name": rec.get("assessment_name"),
                "overallstatus": rec.get("overallstatus"), "isimpaired": rec.get("isimpaired"),
                "ircategory": rec.get("ircategory"), "distance_m": distance, "match_type": match,
                "source_layer": rec.get("layer")}

    near_m = col(f"au_near_m_{tag}")
    return {
        "nhdplusid": int(nhdplusid), "sample": tag,
        "sinuosity": None if col("sinuosity") is None else round(col("sinuosity"), 3),
        "huc12": f"{col(f'huc12_{tag}'):012d}" if col(f"huc12_{tag}") else None,
        "attains_exact": unit(col(f"au_exact_{tag}"), 0.0, "intersect"),
        "attains_nearby": unit(col(f"au_near_{tag}"), None if near_m is None else round(near_m, 1), "nearby"),
        "nwi_strip_m2": dict((c, round(col(c), 1)) for c in t.column_names if c.startswith("nwi_") or c == "strip_m2"),
    }
