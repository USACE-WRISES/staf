"""Step ``strata``: the ecoregion crosswalk and the national strata table.

``analysis/l3_to_l2_l1.csv``: EPA Level III -> Level II -> Level I, one row
per Level III code, from the NRSA site files (the mode per code with its
share; a share under ``CHECK_SHARE`` is flagged for a hand check).

``analysis/strata.parquet``: every NHDPlus V2 flowline with the region keys
(Level III, II, I, NARS-9, physiographic division), the HUC12, the state, the
anchor point the scoring uses (10 m upstream of the downstream node, the rule
of ``derive._anchor_and_sinuosity``), whole-flowline sinuosity, and the slope,
drainage-area and FCODE classes the reference panels stratify on.
"""
from __future__ import annotations

import csv
import glob
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from .. import REPO_ROOT
from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common
from ..stages.derive import BACK_M
from ..stages.states import load_comid_states, states_of
from . import ANALYSIS_VERSION

EASI_DATA = REPO_ROOT / "apps" / "easi" / "data"
L3_PATH = EASI_DATA / "ecoregions_l3.geojson"
NARS9_PATH = EASI_DATA / "nars-ecoregions-9.geojson.gz"
PHYSIO_PATH = EASI_DATA / "physio_divisions.geojson"
NRSA_RAW = REPO_ROOT / "notes" / "DEEP_Working" / "nrsa_raw"
SITE_FILE_PATTERNS = ("*/*siteinfo*.csv", "*/*SiteInfo*.csv", "*/*siteinformation*.csv")
BATCH = 200_000
#: a Level III whose Level II mode share is under this is checked by hand
CHECK_SHARE = 0.6
#: m/m: the 0.5 % and 2 % channel-slope classes of the stratifier registry
SLOPE_BREAKS = (0.005, 0.02)
SLOPE_LABELS = ("lt_0.5", "0.5_to_2", "ge_2")
#: the entrenchment stream-type rule (alluvial, transitional, entrenched by nature)
SLOPE_ER_BREAKS = (0.02, 0.04)
SLOPE_ER_LABELS = ("lt_2", "2_to_4", "ge_4")
#: km2
DA_BREAKS = (10.0, 100.0)
DA_LABELS = ("le_10", "10_to_100", "gt_100")
FCODE_CLASS = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
               33600: "canal", 33601: "canal", 33603: "canal",
               55800: "artificial_path", 42800: "pipeline", 56600: "coastline"}
WADEABLE_MAX_ORDER = 5


def crosswalk_path(root: DataRoot) -> Path:
    return root.analysis / "l3_to_l2_l1.csv"


def crosswalk_check_path(root: DataRoot) -> Path:
    return root.analysis / "crosswalk_check.csv"


def strata_path(root: DataRoot) -> Path:
    return root.analysis / "strata.parquet"


def parity_path(root: DataRoot) -> Path:
    return root.analysis / "strata_parity.json"


# ------------------------------------------------------------ crosswalk
def site_files(raw_dir: Path = NRSA_RAW) -> list[Path]:
    """The NRSA site-information CSVs (every cycle), metadata files excluded."""
    found = {Path(p) for pattern in SITE_FILE_PATTERNS for p in glob.glob(str(raw_dir / pattern))}
    return sorted(p for p in found if "metadata" not in p.name.lower())


def norm_code(value, level: str) -> Optional[str]:
    """Codes as plain strings: Level III ``45`` (never ``45.0``), Level II
    ``8.3``, Level I ``8``."""
    text = str(value if value is not None else "").strip()
    if not text or text.upper() in ("NA", "NAN", "NONE", "NULL"):
        return None
    if level == "l3":
        try:
            number = float(text)
        except ValueError:
            return text
        if number.is_integer():
            return str(int(number))
    return text


def _sort_key(code: str):
    try:
        return (0, float(code), code)
    except ValueError:
        return (1, 0.0, code)


def build_crosswalk(files: Iterable[Path], l3_codes: Optional[Iterable[str]] = None) -> list[dict]:
    """One row per Level III code: the Level II and I modes over every site
    row, with shares and the minority codes. Raises when ``l3_codes`` (the
    codes of the ecoregion layer) are not all covered."""
    votes_l2: dict[str, Counter] = defaultdict(Counter)
    votes_l1: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, str] = {}
    wanted = ("US_L3CODE", "US_L3NAME", "NA_L2CODE", "NA_L2NAME", "NA_L1CODE", "NA_L1NAME")
    for path in files:
        with open(path, encoding="latin-1", newline="") as handle:
            reader = csv.reader(handle)
            # a UTF-8 byte-order mark read under latin-1 arrives as "ï»¿"
            header = [h.strip().lstrip("﻿").replace("ï»¿", "").strip().upper()
                      for h in next(reader, [])]
            col = {name: (header.index(name) if name in header else None) for name in wanted}
            if col["US_L3CODE"] is None or col["NA_L2CODE"] is None:
                continue
            for row in reader:
                def cell(name: str):
                    index = col[name]
                    return row[index] if index is not None and index < len(row) else None
                l3 = norm_code(cell("US_L3CODE"), "l3")
                if not l3:
                    continue
                l2 = norm_code(cell("NA_L2CODE"), "l2")
                l1 = norm_code(cell("NA_L1CODE"), "l1") or (l2.split(".")[0] if l2 else None)
                if l2:
                    votes_l2[l3][l2] += 1
                if l1:
                    votes_l1[l3][l1] += 1
                for key, name in ((f"l3:{l3}", cell("US_L3NAME")), (f"l2:{l2}", cell("NA_L2NAME")),
                                  (f"l1:{l1}", cell("NA_L1NAME"))):
                    if name and str(name).strip() and key not in names:
                        names[key] = str(name).strip()
    rows: list[dict] = []
    for l3 in sorted(votes_l2, key=_sort_key):
        counter2 = votes_l2[l3]
        total2 = sum(counter2.values())
        l2, n2 = counter2.most_common(1)[0]
        counter1 = votes_l1[l3]
        total1 = sum(counter1.values())
        l1, n1 = counter1.most_common(1)[0] if counter1 else (l2.split(".")[0], total2)
        share2 = n2 / total2
        share1 = (n1 / total1) if total1 else 1.0
        rows.append({
            "l3": l3, "l2": l2, "l1": l1,
            "l3_name": names.get(f"l3:{l3}", ""), "l2_name": names.get(f"l2:{l2}", ""),
            "l1_name": names.get(f"l1:{l1}", ""),
            "n_rows": total2, "mode_share_l2": round(share2, 3), "mode_share_l1": round(share1, 3),
            "needs_check": bool(share2 < CHECK_SHARE or share1 < CHECK_SHARE),
            "minority_l2": ";".join(f"{code}:{n}" for code, n in counter2.most_common()[1:]),
        })
    if l3_codes is not None:
        missing = sorted(set(l3_codes) - {r["l3"] for r in rows}, key=_sort_key)
        if missing:
            raise ValueError(f"the NRSA site files cover no rows for Level III codes {missing}")
    return rows


def l3_codes_in(path: Path = L3_PATH) -> list[str]:
    fc = json.loads(Path(path).read_text(encoding="utf-8"))
    codes = {norm_code((f.get("properties") or {}).get("US_L3CODE"), "l3") for f in fc.get("features") or []}
    return sorted((c for c in codes if c), key=_sort_key)


def write_crosswalk(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["l3"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_crosswalk(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as handle:
        return {row["l3"]: row for row in csv.DictReader(handle)}


# ------------------------------------------------------------ locators
class RegionLocator:
    """Vectorised point-in-polygon over a polygon set with a nearest fallback
    (the ``states.StateLocator`` idiom)."""

    def __init__(self, geoms, codes):
        import shapely
        self.codes = np.asarray(list(codes), dtype=object)
        self.tree = shapely.STRtree(list(geoms))

    @classmethod
    def from_geojson(cls, path: Path, prop: str, *, transform=None) -> "RegionLocator":
        from shapely.geometry import shape
        opener = (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz")
                  else open(path, "r", encoding="utf-8"))
        with opener as handle:
            fc = json.load(handle)
        geoms, codes = [], []
        for feature in fc.get("features") or []:
            geometry = feature.get("geometry")
            value = (feature.get("properties") or {}).get(prop)
            if not geometry or value is None:
                continue
            geom = shape(geometry)
            if geom.is_empty:
                continue
            code = transform(value) if transform else str(value)
            if code is None:
                continue
            geoms.append(geom)
            codes.append(code)
        return cls(geoms, codes)

    @classmethod
    def from_parquet(cls, path: Path, prop: str) -> "RegionLocator":
        """A GeoParquet polygon file (WKB ``geometry`` column)."""
        import pyarrow.parquet as pq
        import shapely
        table = pq.read_table(path, columns=[prop, "geometry"])
        geoms = shapely.from_wkb(table.column("geometry").to_numpy(zero_copy_only=False))
        codes = np.asarray([str(c) for c in table.column(prop).to_pylist()], dtype=object)
        keep = ~(shapely.is_missing(geoms) | shapely.is_empty(geoms))
        return cls(geoms[keep], codes[keep])

    def assign(self, points, *, nearest: bool = True):
        """``(codes, by_nearest)``: one code per point (None for a missing or
        empty point, and for a point no polygon contains when ``nearest`` is
        off) and the mask of points that took the nearest polygon."""
        import shapely
        points = np.asarray(points, dtype=object)
        n = len(points)
        codes = np.full(n, None, dtype=object)
        by_nearest = np.zeros(n, dtype=bool)
        if n == 0:
            return codes, by_nearest
        valid = ~(shapely.is_missing(points) | shapely.is_empty(points))
        pos = np.flatnonzero(valid)
        if len(pos) == 0:
            return codes, by_nearest
        hits_pt, hits_poly = self.tree.query(points[pos], predicate="within")
        first = np.full(len(pos), -1, dtype=np.int64)
        for p, t in zip(hits_pt[::-1].tolist(), hits_poly[::-1].tolist()):
            first[p] = t
        missing = np.flatnonzero(first < 0)
        if len(missing) and nearest:
            found = self.tree.nearest(points[pos[missing]])
            first[missing] = np.asarray(found, dtype=np.int64)
            by_nearest[pos[missing]] = True
        have = first >= 0
        codes[pos[have]] = self.codes[first[have]]
        return codes, by_nearest


def physio_abbr(name) -> Optional[str]:
    """USGS physiographic DIVISION name -> the abbreviation ``derived.physio_division`` carries."""
    from easi import bieger
    return bieger._DIV_ABBR.get(str(name or "").strip().upper())


# ------------------------------------------------------------ anchors
def anchors(lines, flowdir) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(lat, lon, sinuosity)`` per flowline, vectorised: the point ``BACK_M``
    upstream of the downstream node (EPSG:5070, ``flowdir`` against reverses
    the digitised direction, a multi-part line is merged and its longest part
    kept) and length over the straight distance, rounded to 3 places."""
    import shapely
    from pyproj import Transformer
    forward = Transformer.from_crs(4326, 5070, always_xy=True)
    inverse = Transformer.from_crs(5070, 4326, always_xy=True)
    lines = np.asarray(lines, dtype=object)
    projected = shapely.transform(lines, lambda xy: np.column_stack(forward.transform(xy[:, 0], xy[:, 1])))
    multi = shapely.get_type_id(projected) == shapely.GeometryType.MULTILINESTRING
    if multi.any():
        merged = shapely.line_merge(projected[multi])
        fixed = []
        for geom in merged:
            if geom is not None and geom.geom_type == "MultiLineString":
                geom = max(geom.geoms, key=lambda part: part.length)
            fixed.append(geom)
        projected[multi] = np.asarray(fixed, dtype=object)
    against = np.asarray([str(f or "").lower().startswith("against") for f in flowdir], dtype=bool)
    if against.any():
        projected[against] = shapely.reverse(projected[against])
    length = shapely.length(projected)
    straight = shapely.distance(shapely.get_point(projected, 0), shapely.get_point(projected, -1))
    safe = np.where(straight > 0, straight, np.nan)
    sinuosity = np.round(length / safe, 3)
    anchor = shapely.line_interpolate_point(projected, np.maximum(0.0, length - BACK_M))
    lon, lat = inverse.transform(shapely.get_x(anchor), shapely.get_y(anchor))
    return np.asarray(lat, dtype=float), np.asarray(lon, dtype=float), sinuosity


# ------------------------------------------------------------ classes
def classify(values, breaks, labels, *, right: bool = False):
    """Interval labels for ``values`` (None where the value is missing)."""
    values = np.asarray(values, dtype=float)
    index = np.digitize(values, breaks, right=right)
    out = np.asarray(labels, dtype=object)[np.clip(index, 0, len(labels) - 1)]
    out[~np.isfinite(values)] = None
    return out


def fcode_classes(fcodes) -> np.ndarray:
    out = np.empty(len(fcodes), dtype=object)
    for i, code in enumerate(fcodes):
        try:
            out[i] = FCODE_CLASS.get(int(code), "other") if code is not None else None
        except (TypeError, ValueError):
            out[i] = None
    return out


# ------------------------------------------------------------ the step
def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    flowlines = root.national / "flowlines.parquet"
    stamp = (flowlines.stat().st_size, int(flowlines.stat().st_mtime)) if flowlines.exists() else None
    sites = [(p.name, p.stat().st_size) for p in site_files()]
    return digest("strata", ANALYSIS_VERSION, stamp, sites, L3_PATH.stat().st_size if L3_PATH.exists() else 0, 1)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    return run_strata(root, progress, control)


def run_strata(root: DataRoot, progress: Progress, control: Control, *, batch: int = BATCH,
               flowlines: Optional[Path] = None, l3_path: Path = L3_PATH,
               nars9_path: Path = NARS9_PATH, physio_path: Path = PHYSIO_PATH,
               huc12: Optional[Path] = None, sites: Optional[list[Path]] = None,
               parity: bool = True) -> Path:
    """Write the crosswalk and ``analysis/strata.parquet``; returns the parquet path."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    flowlines = flowlines or (root.national / "flowlines.parquet")
    huc12 = huc12 or (root.national / "huc12.parquet")
    if not flowlines.exists():
        raise RuntimeError(f"{flowlines.name} not found: run the national flowlines step first")
    root.analysis.mkdir(parents=True, exist_ok=True)

    rows = build_crosswalk(sites if sites is not None else site_files(), l3_codes_in(l3_path))
    write_crosswalk(rows, crosswalk_path(root))
    write_crosswalk([r for r in rows if r["needs_check"] or r["minority_l2"]] or [], crosswalk_check_path(root))
    crosswalk = {r["l3"]: (r["l2"], r["l1"]) for r in rows}
    progress.say(f"crosswalk: {len(rows)} Level III codes, "
                 f"{sum(1 for r in rows if r['needs_check'])} flagged for a hand check")

    l3_locator = RegionLocator.from_geojson(l3_path, "US_L3CODE", transform=lambda v: norm_code(v, "l3"))
    nars9_locator = RegionLocator.from_geojson(nars9_path, "WSA_9")
    physio_locator = RegionLocator.from_geojson(physio_path, "DIVISION", transform=physio_abbr)
    huc12_locator = RegionLocator.from_parquet(huc12, "huc_12") if huc12.exists() else None
    state_index = load_comid_states(root)

    reader = pq.ParquetFile(flowlines)
    total = reader.metadata.num_rows
    progress.begin("analysis", "strata", total=total, message=f"strata: {total:,} flowlines")
    columns = ["comid", "geometry", "flowdir", "fcode", "streamorde", "slope", "totdasqkm", "lengthkm", "reachcode"]
    present = [c for c in columns if c in reader.schema_arrow.names]
    parts: dict[str, list] = defaultdict(list)
    done = 0
    for record_batch in reader.iter_batches(batch_size=batch, columns=present):
        control.check()
        table = pa.Table.from_batches([record_batch])
        n = table.num_rows
        comid = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
        lines = shapely.from_wkb(table.column("geometry").to_numpy(zero_copy_only=False))
        flowdir = table.column("flowdir").to_pylist() if "flowdir" in present else [None] * n
        lat, lon, sinuosity = anchors(lines, flowdir)
        points = shapely.points(lon, lat)
        l3, l3_near = l3_locator.assign(points)
        nars9, n9_near = nars9_locator.assign(points)
        physio, ph_near = physio_locator.assign(points)
        if huc12_locator is not None:
            huc12_codes, huc12_near = huc12_locator.assign(points)
        else:
            huc12_codes, huc12_near = np.full(n, None, dtype=object), np.zeros(n, dtype=bool)
        l2 = np.asarray([crosswalk.get(c, (None, None))[0] if c else None for c in l3], dtype=object)
        l1 = np.asarray([crosswalk.get(c, (None, None))[1] if c else None for c in l3], dtype=object)
        state = states_of(state_index, comid) if state_index is not None else np.full(n, None, dtype=object)
        reach = table.column("reachcode").to_pylist() if "reachcode" in present else [None] * n
        slope = np.asarray(table.column("slope").to_pylist() if "slope" in present else [None] * n, dtype=float)
        slope = np.where(slope < 0, np.nan, slope)         # NHDPlus flags unknown slopes with -9998
        da = np.asarray(table.column("totdasqkm").to_pylist() if "totdasqkm" in present else [None] * n, dtype=float)
        fcode = table.column("fcode").to_pylist() if "fcode" in present else [None] * n
        order = table.column("streamorde").to_pylist() if "streamorde" in present else [None] * n
        lengthkm = table.column("lengthkm").to_pylist() if "lengthkm" in present else [None] * n
        parts["comid"].append(comid)
        parts["huc8"].append(np.asarray([str(r)[:8] if r else None for r in reach], dtype=object))
        parts["huc12"].append(huc12_codes)
        parts["huc12_imputed"].append(huc12_near)
        parts["state"].append(np.asarray(state, dtype=object))
        parts["lat"].append(lat)
        parts["lon"].append(lon)
        parts["l3"].append(l3)
        parts["l2"].append(l2)
        parts["l1"].append(l1)
        parts["nars9"].append(nars9)
        parts["physio"].append(physio)
        parts["region_imputed"].append(l3_near | n9_near | ph_near)
        parts["slope"].append(slope)
        parts["slope_class"].append(classify(slope, SLOPE_BREAKS, SLOPE_LABELS))
        parts["slope_class_er"].append(classify(slope, SLOPE_ER_BREAKS, SLOPE_ER_LABELS))
        parts["totdasqkm"].append(da)
        parts["da_class"].append(classify(da, DA_BREAKS, DA_LABELS, right=True))
        parts["fcode"].append(np.asarray([int(c) if c is not None else -1 for c in fcode], dtype=np.int32))
        parts["fcode_class"].append(fcode_classes(fcode))
        parts["streamorde"].append(np.asarray([int(o) if o is not None else -1 for o in order], dtype=np.int16))
        parts["lengthkm"].append(np.asarray([float(v) if v is not None else np.nan for v in lengthkm], dtype=float))
        parts["sinuosity_flowline"].append(np.asarray(sinuosity, dtype=float))
        parts["wadeable"].append(np.asarray([o is not None and int(o) <= WADEABLE_MAX_ORDER for o in order], dtype=bool))
        done += n
        progress.tick(done=done)

    def cat(name: str):
        return np.concatenate(parts[name]) if parts[name] else np.zeros(0)

    comid_all = cat("comid")
    order_idx = np.argsort(comid_all, kind="stable")

    def col(name: str, arrow_type):
        values = cat(name)[order_idx] if len(order_idx) else cat(name)
        if arrow_type in (pa.string(),):
            return pa.array(values.tolist(), arrow_type)
        if arrow_type == pa.bool_():
            return pa.array(np.asarray(values, dtype=bool), arrow_type)
        if arrow_type in (pa.float64(),):
            arr = np.asarray(values, dtype=float)
            return pa.array(arr, arrow_type, mask=~np.isfinite(arr))
        return pa.array(values, arrow_type)

    table = pa.table({
        "comid": col("comid", pa.int64()), "huc8": col("huc8", pa.string()), "huc12": col("huc12", pa.string()),
        "huc12_imputed": col("huc12_imputed", pa.bool_()), "state": col("state", pa.string()),
        "lat": col("lat", pa.float64()), "lon": col("lon", pa.float64()),
        "l3": col("l3", pa.string()), "l2": col("l2", pa.string()), "l1": col("l1", pa.string()),
        "nars9": col("nars9", pa.string()), "physio": col("physio", pa.string()),
        "region_imputed": col("region_imputed", pa.bool_()),
        "slope": col("slope", pa.float64()), "slope_class": col("slope_class", pa.string()),
        "slope_class_er": col("slope_class_er", pa.string()),
        "totdasqkm": col("totdasqkm", pa.float64()), "da_class": col("da_class", pa.string()),
        "fcode": col("fcode", pa.int32()), "fcode_class": col("fcode_class", pa.string()),
        "streamorde": col("streamorde", pa.int16()), "lengthkm": col("lengthkm", pa.float64()),
        "sinuosity_flowline": col("sinuosity_flowline", pa.float64()), "wadeable": col("wadeable", pa.bool_()),
    })
    path = common.write_parquet(table, strata_path(root))
    imputed = int(np.asarray(cat("region_imputed"), dtype=bool).sum()) if table.num_rows else 0
    progress.say(f"strata.parquet: {table.num_rows:,} flowlines, {imputed:,} with a region by the nearest polygon")
    if parity:
        report = parity_report(root, table)
        parity_path(root).write_text(json.dumps(report, indent=1), encoding="utf-8")
        if report.get("huc8s"):
            progress.say("strata parity with derived.parquet: " + ", ".join(
                f"{key} {report[key]['agree']:.4f}" for key in ("l3", "nars9", "physio") if key in report))
    return path


def parity_report(root: DataRoot, table) -> dict:
    """Agreement of the national keys with the keys the derive stage stored
    for every scored reach (the anchor rule is the same; disagreements are
    boundary reaches)."""
    import pyarrow.parquet as pq
    files = sorted(root.huc8.glob("*/derived.parquet")) if root.huc8.exists() else []
    if not files:
        return {"huc8s": 0}
    strata_comid = np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    keys = {"l3": table.column("l3").to_pylist(), "nars9": table.column("nars9").to_pylist(),
            "physio": table.column("physio").to_pylist()}
    report: dict = {"huc8s": len(files), "reaches": 0}
    agree = Counter()
    compared = Counter()
    for path in files:
        derived = pq.read_table(path, columns=["comid", "l3_code", "nars9", "physio_division"])
        comids = np.asarray(derived.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
        pos = np.searchsorted(strata_comid, comids)
        pos = np.minimum(pos, max(len(strata_comid) - 1, 0))
        found = (len(strata_comid) > 0) & (strata_comid[pos] == comids)
        report["reaches"] += int(found.sum())
        for key, column in (("l3", "l3_code"), ("nars9", "nars9"), ("physio", "physio_division")):
            theirs = derived.column(column).to_pylist()
            ours = keys[key]
            for i in np.flatnonzero(found).tolist():
                a, b = theirs[i], ours[pos[i]]
                if a is None:
                    continue
                compared[key] += 1
                if str(a) == str(b):
                    agree[key] += 1
    for key in ("l3", "nars9", "physio"):
        report[key] = {"compared": compared[key],
                       "agree": (agree[key] / compared[key]) if compared[key] else None}
    return report
