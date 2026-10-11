"""The cross-section grid as release assets: the rolling ``staf-xs-current`` prerelease.

Each merged region becomes three files, which the apps (the Python tools and the static web app)
read as they are. Rows are sorted by flowline (``nhdplusid``, ``part``, ``k``); only ``nhdplusid``
carries row-group statistics, so a reader finds a flowline's row group from the footer and range-reads
just that one.

- ``xs-<vpu>.parquet``, the numbers each section scores with: status, DEM resolution, bank method,
  quality flags, entrenchment ratio, bank-height ratio, bankfull depth and the two widths. They are
  scaled integers, exact at the precision the grid stores them (ratios and depth 0.01, widths 0.1):
  ``q / scale`` is the stored float64 bit for bit, which :func:`pack_region` checks on every value.
- ``xs-<vpu>-sections.parquet`` (same rows), what drawing any section or pulling it again needs:
  the thalweg (float32, exact) and its sample index, the bankfull and low-bank stages above it
  (float32 metres), the regional bankfull inputs, the half-width, the sample count and the 3DEP
  project and tiles read. Where the section sits and which way it faces are not stored: they follow
  from the bundle's own lines by ``sections.place`` (the bundle the browser and the apps already
  read; ``verify_placement`` checks it rebuilds the archive's ``x``, ``y``, ``nx``, ``ny`` bit for bit).
  With these a pull from USGS's tile files rebuilds the archive's samples (``config.SAMPLING_RULE``).
- ``xs-<vpu>-median.parquet``, one profile per HR segment: the segment's section nearest both of
  its medians (``geomorph.median_candidate``, the rule the apps draw by), its samples copied from the
  archive as they are (``codec``: float32 as order-preserving integers, second differences), plus
  its stages, so a drawing needs no other read. Segments with no scored section have none.

``release.json`` (written and uploaded last) names every region packed so far with its counts and
assets, each asset's bytes and sha256, the regions still to come (``pending``) and the grid's identity
(sampling version, quality rules, the geomorph pin, the bundle build). The files on the archive drive
are only read, never changed.

    python tools/xs-grid/run.py release pack             # every merged region not packed yet (or changed)
    python tools/xs-grid/run.py release publish --yes    # upload what differs, release.json last
    python tools/xs-grid/run.py release status
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import config

TAG = "staf-xs-current"
REPO = "USACE-WRISES/staf"
MANIFEST = "release.json"
#: the release files' own format (the columns and their scales below); a change re-packs every region
FORMAT = 1
OUT = Path(os.environ.get("XSGRID_RELEASE") or (config.WORK / "release"))
NUMBERS_ROWS = SECTIONS_ROWS = 1 << 14
MEDIAN_ROWS = 512
ZSTD_LEVEL = 19
#: GitHub's limit for one release asset
MAX_ASSET_BYTES = 2 * 2**30
#: the longest one ``gh`` upload of up to ten files may take
GH_TIMEOUT_S = 3600
#: every status the scorer writes (``derive.metrics``, ``build.score_task``), coded by position
STATUS = ("ok", "unbalanced", "no_dem", "too_few_points", "ok_reference")
SCORED = ("ok", "ok_reference")
#: ``bank_detection`` by position; no bank method (an unscored section) is 255
BANK = ("slope_break", "crest_scan")
BANK_NONE = 255
#: ``flags`` bits
FLAG_BITS = (("low_bank_capped", 1), ("edge_limited", 2), ("bankfull_area_edge_limited", 4),
             ("bf_extrapolated", 8))
#: scaled integer columns: (column, source, scale, type)
NUMBER_SCALES = (("er", "entrenchment_ratio", 100, pa.uint32()), ("bhr", "bank_height_ratio", 100, pa.uint16()),
                 ("bfd", "bankfull_depth_m", 100, pa.uint16()), ("bfw", "bankfull_width_m", 10, pa.uint16()),
                 ("fpw", "flood_prone_width_m", 10, pa.uint16()))
BIEGER_SCALES = (("rc_width", "bf_width_m", 100, pa.uint32()), ("rc_depth", "bf_depth_m", 1000, pa.uint32()),
                 ("rc_area", "bf_area_m2", 100, pa.uint32()))
#: stages above the thalweg (float32 metres; both apps add them to the thalweg the same way). Top of
#: bank is left out: the apps read it only when a section has no low-bank stage, and every scored
#: grid section has one.
STAGES = (("bf_m", "bankfull_stage_m"), ("lb_m", "low_bank_stage_m"))
METRIC_COLUMNS = ("nhdplusid", "part", "k", "n_sec", "res_m", "status", "entrenchment_ratio", "bank_height_ratio",
                  "bankfull_width_m", "flood_prone_width_m", "bankfull_depth_m", "thalweg", "bankfull_stage_m",
                  "low_bank_stage_m", "low_bank_capped", "edge_limited", "bankfull_area_edge_limited",
                  "bank_detection", "thalweg_station_m")
ARCHIVE_COLUMNS = ("nhdplusid", "part", "k", "da_sqkm", "division", "bf_width_m", "bf_depth_m", "bf_area_m2",
                   "bf_extrapolated", "wide_m", "res_m", "n_pts", "source", "tiles")
COLUMN_DOC = {
    "numbers": "nhdplusid, part, k, n_sec; res (DEM metres, 0 none); status (index into STATUS); bank "
               "(index into BANK, 255 none); flags (bits); er, bhr, bfd (x100), bfw, fpw (x10), null when "
               "not scored",
    "sections": "nhdplusid, part, k; thalweg (float32 m) and thalweg_i (its sample's index); bf_m, lb_m "
                "(float32 m above the thalweg); da_sqkm, division, rc_width (x100), rc_depth (x1000), "
                "rc_area (x100): the regional bankfull inputs; wide_cm (half-width x100); n_pts; source "
                "(3DEP project, 3dep-19 or 3dep-13); tiles (';'-joined tile names). Position and direction: "
                "sections.place on the bundle's lines2_<vpu> (k of n_sec at (k + 1/2) L / n, facing the 5 m "
                "chord downstream)",
    "median": "nhdplusid, part, k, res, wide_cm, n_pts, thalweg, thalweg_i, bf_m, lb_m, and z (the "
              "archive's int32 codes; codec.decode gives the float32 samples at linspace(-wide, wide, n_pts))",
}


def numbers_asset(vpu: str) -> str:
    return f"xs-{vpu}.parquet"


def sections_asset(vpu: str) -> str:
    return f"xs-{vpu}-sections.parquet"


def median_asset(vpu: str) -> str:
    return f"xs-{vpu}-median.parquet"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def merged_regions() -> list:
    """Regions the build has merged (their record is written last, after both files)."""
    folder = config.ARCHIVE / "regions"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []


def lower48() -> list:
    m = json.loads((config.BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    return [v for v in sorted(m["vpus"]) if v[:2] <= "18"]


def read_manifest(out: Path = OUT) -> dict:
    path = Path(out) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _lock_path(out: Path) -> Path:
    return Path(out) / "release.lock"


def lock(out: Path = OUT) -> bool:
    """Take the release folder for one pack or sync at a time (a live holder keeps it)."""
    import psutil
    path = _lock_path(out)
    try:
        pid = int(json.loads(path.read_text(encoding="utf-8"))["pid"])
        if pid != os.getpid() and "python" in psutil.Process(pid).name().lower():
            return False
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds")}),
                    encoding="utf-8")
    return True


def unlock(out: Path = OUT) -> None:
    try:
        if json.loads(_lock_path(out).read_text(encoding="utf-8")).get("pid") == os.getpid():
            _lock_path(out).unlink()
    except (OSError, ValueError):
        pass


# ------------------------------------------------------------------ encoding
def _scaled(values: np.ndarray, scale: int, typ: pa.DataType, name: str) -> pa.Array:
    """``round(values * scale)`` as ``typ`` (NaN as null), refusing any value it would not give back
    exactly or that does not fit."""
    v = np.asarray(values, dtype=np.float64)
    missing = np.isnan(v)
    q = np.round(np.where(missing, 0.0, v) * scale)
    if (q[~missing] / scale != v[~missing]).any():
        bad = v[~missing][(q[~missing] / scale) != v[~missing]][:3]
        raise ValueError(f"{name}: values not exact at 1/{scale}: {bad.tolist()}")
    hi = np.iinfo(typ.to_pandas_dtype()).max
    if (q < 0).any() or (q > hi).any():
        raise ValueError(f"{name}: values outside 0..{hi / scale}")
    return pa.array(q.astype(typ.to_pandas_dtype()), type=typ, mask=missing)


def _codes(col: pa.ChunkedArray, table: tuple, name: str, none=None) -> pa.Array:
    """Each value's position in ``table`` (uint8); a missing value is ``none``, refused without one."""
    idx = pc.index_in(col, value_set=pa.array(list(table), type=pa.string()))
    unknown = pc.and_(pc.is_null(idx), pc.is_valid(col))
    if pc.any(unknown).as_py():
        raise ValueError(f"{name}: unknown values {pc.unique(col.filter(unknown)).to_pylist()[:5]}")
    if none is None and col.null_count:
        raise ValueError(f"{name}: {col.null_count} missing values")
    return pc.cast(pc.fill_null(idx, 0 if none is None else none), pa.uint8()).combine_chunks()


def _flag(col: pa.ChunkedArray) -> np.ndarray:
    return pc.fill_null(col, False).to_numpy(zero_copy_only=False).astype(np.uint8)


def _stage_rel(stage: np.ndarray, thalweg: np.ndarray) -> pa.Array:
    """A stage as float32 metres above the thalweg (within 1e-7 of its own size of the stored stage)."""
    rel = np.asarray(stage, dtype=np.float64) - np.asarray(thalweg, dtype=np.float64)
    missing = np.isnan(rel)
    return pa.array(np.where(missing, 0.0, rel).astype(np.float32), type=pa.float32(), mask=missing)


def _thalweg_index(station: np.ndarray, wide: np.ndarray, n_pts: np.ndarray) -> pa.Array:
    """The sample index of each thalweg station on ``linspace(-wide, wide, n_pts)``, exact (checked)."""
    st = np.asarray(station, dtype=np.float64)
    missing = np.isnan(st)
    n = np.asarray(n_pts, dtype=np.int64)
    w = np.asarray(wide, dtype=np.float64)
    step = np.where(n > 1, 2.0 * w / np.maximum(n - 1, 1), 1.0)
    idx = np.where(missing, 0, np.round((np.where(missing, 0.0, st) + w) / step)).astype(np.int64)
    back = np.where(idx == n - 1, w, idx * step + (-w))     # numpy's linspace: i * step + start, last = stop
    if (back[~missing] != st[~missing]).any():
        raise ValueError("a thalweg station is not one of its transect's samples")
    return pa.array(idx.astype(np.uint16), type=pa.uint16(), mask=missing)


def _write(table: pa.Table, path: Path, rows: int, md: dict) -> dict:
    """Write deterministically (no time stamps in the file) and atomically; bytes and sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = table.replace_schema_metadata({"xsgrid_release": json.dumps(md, sort_keys=True)})
    names = table.column_names
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=ZSTD_LEVEL, row_group_size=rows,
                   use_dictionary=[c for c in ("division", "source", "tiles") if c in names],
                   write_statistics=["nhdplusid"])
    os.replace(tmp, path)
    return {"bytes": path.stat().st_size, "sha256": sha256(path)}


# ------------------------------------------------------------------ one region
def _record(vpu: str) -> dict:
    return json.loads((config.ARCHIVE / "regions" / f"{vpu}.json").read_text(encoding="utf-8"))


def _median_rows(nhd: np.ndarray, part: np.ndarray, er: np.ndarray, bhr: np.ndarray,
                 scored: np.ndarray) -> np.ndarray:
    """Per segment (one ``nhdplusid``; rows sorted by part and k), the row the apps draw: the section
    nearest both of the segment's medians by rank (``geomorph.median_candidate``, the same function
    on the same values), or none when no section of it was scored."""
    from .engine import geomorph
    if not len(nhd):
        return np.zeros(0, dtype=np.int64)
    starts = np.r_[0, np.flatnonzero(np.diff(nhd)) + 1]
    stops = np.r_[starts[1:], len(nhd)]
    out = []
    for a, b in zip(starts.tolist(), stops.tolist()):
        if not scored[a:b].any():
            continue
        cands = [{"entrenchment_ratio": None if np.isnan(er[i]) else float(er[i]),
                  "bank_height_ratio": None if np.isnan(bhr[i]) else float(bhr[i])} for i in range(a, b)]
        out.append(a + geomorph.median_candidate(cands))
    return np.asarray(out, dtype=np.int64)


def _median_codes(archive_path: Path, rows: np.ndarray) -> pa.Array:
    """The archive's ``z`` codes of ``rows`` (archive row numbers, any order), as they are, read one
    row group (a cell) at a time."""
    f = pq.ParquetFile(archive_path)
    want = np.sort(np.unique(rows))
    pieces = []
    start = 0
    for g in range(f.metadata.num_row_groups):
        n = f.metadata.row_group(g).num_rows
        lo, hi = np.searchsorted(want, [start, start + n])
        if hi > lo:
            z = f.read_row_group(g, columns=["z"]).column("z").combine_chunks()
            pieces.append(z.take(pa.array(want[lo:hi] - start)))
        start += n
    if not pieces:
        return pa.array([], type=pa.list_(pa.int32()))
    allz = pa.concat_arrays(pieces)                       # in ``want`` order
    return allz.take(pa.array(np.searchsorted(want, rows)))


def pack_region(vpu: str, out: Path = OUT) -> dict:
    """Write the region's three files into ``out``; returns its release entry (with ``assets``)."""
    rec = _record(vpu)
    mpath = config.ARCHIVE / "metrics" / f"xsm_{vpu}.parquet"
    apath = config.ARCHIVE / "archive" / f"xs_{vpu}.parquet"
    if sha256(mpath) != rec["metrics_sha256"]:
        raise ValueError(f"[{vpu}] the metrics file differs from its region record")
    m = pq.read_table(mpath, columns=list(METRIC_COLUMNS))
    a = pq.read_table(apath, columns=list(ARCHIVE_COLUMNS))
    if m.num_rows != rec["sections"] or a.num_rows != rec["sections"]:
        raise ValueError(f"[{vpu}] {m.num_rows} metrics rows, {a.num_rows} archive rows, record {rec['sections']}")
    for c in ("nhdplusid", "part", "k"):
        if not m.column(c).equals(a.column(c)):
            raise ValueError(f"[{vpu}] the metrics and archive rows are not aligned ({c})")
    order = np.lexsort((m.column("k").to_numpy(), m.column("part").to_numpy(), m.column("nhdplusid").to_numpy()))
    take = pa.array(order)
    m, a = m.take(take), a.take(take)
    nhd = m.column("nhdplusid").to_numpy()
    part = m.column("part").to_numpy()
    md = {"format": FORMAT, "vpu": vpu, "sampling_version": rec.get("sampling_version"),
          "metrics_sha256": rec["metrics_sha256"], "archive_sha256": rec["archive_sha256"]}

    def num(name):
        return m.column(name).to_numpy(zero_copy_only=False).astype(np.float64)

    def anum(name):
        return a.column(name).to_numpy(zero_copy_only=False).astype(np.float64)

    flags = np.zeros(m.num_rows, dtype=np.uint8)
    for name, bit in FLAG_BITS:
        flags |= _flag((a if name == "bf_extrapolated" else m).column(name)) * np.uint8(bit)
    numbers = {"nhdplusid": m.column("nhdplusid"), "part": pc.cast(m.column("part"), pa.uint8()),
               "k": pc.cast(m.column("k"), pa.uint16()), "n_sec": pc.cast(m.column("n_sec"), pa.uint16()),
               "res": pc.cast(m.column("res_m"), pa.uint8()), "status": _codes(m.column("status"), STATUS, "status"),
               "bank": _codes(m.column("bank_detection"), BANK, "bank_detection", none=BANK_NONE),
               "flags": pa.array(flags, type=pa.uint8())}
    for col, src, scale, typ in NUMBER_SCALES:
        numbers[col] = _scaled(num(src), scale, typ, src)
    numbers_t = pa.table(numbers)

    thalweg = num("thalweg")
    if (thalweg[~np.isnan(thalweg)].astype(np.float32).astype(np.float64) != thalweg[~np.isnan(thalweg)]).any():
        raise ValueError(f"[{vpu}] a thalweg is not a float32 sample")
    thal_i = _thalweg_index(num("thalweg_station_m"), anum("wide_m"), a.column("n_pts").to_numpy())
    sections = {"nhdplusid": m.column("nhdplusid"), "part": numbers["part"], "k": numbers["k"],
                "thalweg": pa.array(thalweg.astype(np.float32), type=pa.float32(), mask=np.isnan(thalweg)),
                "thalweg_i": thal_i}
    for col, src in STAGES:
        sections[col] = _stage_rel(num(src), thalweg)
    sections["da_sqkm"] = a.column("da_sqkm")
    sections["division"] = a.column("division")
    for col, src, scale, typ in BIEGER_SCALES:
        sections[col] = _scaled(anum(src), scale, typ, src)
    sections["wide_cm"] = _scaled(anum("wide_m"), 100, pa.uint32(), "wide_m")
    sections["n_pts"] = pc.cast(a.column("n_pts"), pa.uint16())
    sections["source"] = a.column("source")
    sections["tiles"] = a.column("tiles")
    sections_t = pa.table(sections)

    scored = pc.fill_null(pc.is_in(m.column("status"), value_set=pa.array(list(SCORED))), False)
    scored = scored.to_numpy(zero_copy_only=False).astype(bool)
    mrows = _median_rows(nhd, part, num("entrenchment_ratio"), num("bank_height_ratio"), scored)
    median_t = pa.table({
        "nhdplusid": numbers_t.column("nhdplusid").take(pa.array(mrows)),
        "part": numbers_t.column("part").take(pa.array(mrows)),
        "k": numbers_t.column("k").take(pa.array(mrows)),
        "res": numbers_t.column("res").take(pa.array(mrows)),
        "wide_cm": sections_t.column("wide_cm").take(pa.array(mrows)),
        "n_pts": sections_t.column("n_pts").take(pa.array(mrows)),
        "thalweg": sections_t.column("thalweg").take(pa.array(mrows)),
        "thalweg_i": sections_t.column("thalweg_i").take(pa.array(mrows)),
        "bf_m": sections_t.column("bf_m").take(pa.array(mrows)),
        "lb_m": sections_t.column("lb_m").take(pa.array(mrows)),
        "z": _median_codes(apath, order[mrows]),
    })

    out = Path(out)
    assets = {}
    for name, table, rows in ((numbers_asset(vpu), numbers_t, NUMBERS_ROWS),
                              (sections_asset(vpu), sections_t, SECTIONS_ROWS),
                              (median_asset(vpu), median_t, MEDIAN_ROWS)):
        assets[name] = {**_write(table, out / name, rows, md), "region": vpu}
        if assets[name]["bytes"] >= MAX_ASSET_BYTES:
            raise ValueError(f"{name} is {assets[name]['bytes'] / 2**30:.2f} GiB, over a release asset's 2 GiB")
    entry = {"sections": rec["sections"], "segments": int(len(np.unique(nhd))) if len(nhd) else 0,
             "medians": int(len(mrows)), "tiers": rec.get("tiers"), "statuses": rec.get("statuses"),
             "finished": rec.get("finished"), "sampling_version": rec.get("sampling_version"),
             "metrics_sha256": rec["metrics_sha256"], "format": FORMAT, "assets": sorted(assets)}
    return {"entry": entry, "assets": assets}


def verify_region(vpu: str, out: Path = OUT) -> dict:
    """Decode the region's packed files and compare them with the archive drive's: every number exact,
    every stage within half a millimetre, every thalweg and its station exact, every median profile's
    codes identical to the archive's and its choice the drawing rule's. Raises on any difference."""
    out = Path(out)
    m = pq.read_table(config.ARCHIVE / "metrics" / f"xsm_{vpu}.parquet", columns=list(METRIC_COLUMNS))
    a = pq.read_table(config.ARCHIVE / "archive" / f"xs_{vpu}.parquet",
                      columns=["nhdplusid", "part", "k", "wide_m", "n_pts", "tiles"])
    order = np.lexsort((m.column("k").to_numpy(), m.column("part").to_numpy(), m.column("nhdplusid").to_numpy()))
    m, a = m.take(pa.array(order)), a.take(pa.array(order))
    nt = pq.read_table(out / numbers_asset(vpu))
    st = pq.read_table(out / sections_asset(vpu))
    md = pq.read_table(out / median_asset(vpu))
    if not (nt.num_rows == st.num_rows == m.num_rows):
        raise ValueError(f"[{vpu}] row counts differ")
    for c in ("nhdplusid", "k"):
        if not (nt.column(c).to_numpy().astype(np.int64) == m.column(c).to_numpy().astype(np.int64)).all():
            raise ValueError(f"[{vpu}] {c} differs")
    for col, src, scale, _typ in NUMBER_SCALES:
        got = nt.column(col).to_numpy(zero_copy_only=False).astype(np.float64) / scale
        want = m.column(src).to_numpy(zero_copy_only=False).astype(np.float64)
        same = (got == want) | (np.isnan(got) & np.isnan(want))
        if not same.all():
            raise ValueError(f"[{vpu}] {col} differs on {int((~same).sum())} rows")
    status = [STATUS[i] for i in nt.column("status").to_pylist()]
    if status != m.column("status").to_pylist():
        raise ValueError(f"[{vpu}] status differs")
    th_got = st.column("thalweg").to_numpy(zero_copy_only=False).astype(np.float64)
    th = m.column("thalweg").to_numpy(zero_copy_only=False).astype(np.float64)
    if not ((th_got == th) | (np.isnan(th_got) & np.isnan(th))).all():
        raise ValueError(f"[{vpu}] thalweg differs")
    for col, src in STAGES:
        got = st.column(col).to_numpy(zero_copy_only=False).astype(np.float64)
        want = m.column(src).to_numpy(zero_copy_only=False).astype(np.float64) - th
        ok = (got == want.astype(np.float32).astype(np.float64)) | (np.isnan(got) & np.isnan(want))
        if not ok.all():
            raise ValueError(f"[{vpu}] {col} is not the stored stage above the thalweg")
    wide = a.column("wide_m").to_numpy()
    n = a.column("n_pts").to_numpy().astype(np.int64)
    ti = st.column("thalweg_i").to_numpy(zero_copy_only=False).astype(np.float64)
    ts = m.column("thalweg_station_m").to_numpy(zero_copy_only=False).astype(np.float64)
    ok = ~np.isnan(ts)
    step = np.where(n > 1, 2.0 * wide / np.maximum(n - 1, 1), 1.0)
    back = np.where(ti == n - 1, wide, ti * step + (-wide))
    if not (back[ok] == ts[ok]).all():
        raise ValueError(f"[{vpu}] thalweg stations differ")
    if st.column("tiles").to_pylist() != a.column("tiles").to_pylist() or \
            not (st.column("wide_cm").to_numpy(zero_copy_only=False) / 100 == wide).all():
        raise ValueError(f"[{vpu}] the transects differ")
    # medians: the drawing rule's choice, and the archive's codes as they are
    nhd = m.column("nhdplusid").to_numpy()
    scored = pc.fill_null(pc.is_in(m.column("status"), value_set=pa.array(list(SCORED))), False)
    rows = _median_rows(nhd, m.column("part").to_numpy(), m.column("entrenchment_ratio").to_numpy(
        zero_copy_only=False).astype(np.float64), m.column("bank_height_ratio").to_numpy(
        zero_copy_only=False).astype(np.float64), scored.to_numpy(zero_copy_only=False).astype(bool))
    if md.num_rows != len(rows) or not (md.column("nhdplusid").to_numpy() == nhd[rows]).all() or \
            not (md.column("k").to_numpy().astype(np.int64) == m.column("k").to_numpy()[rows]).all():
        raise ValueError(f"[{vpu}] the median sections differ from the drawing rule's")
    want_z = _median_codes(config.ARCHIVE / "archive" / f"xs_{vpu}.parquet", order[rows])
    if not md.column("z").combine_chunks().equals(want_z):
        raise ValueError(f"[{vpu}] a median profile differs from the archive")
    return {"sections": nt.num_rows, "medians": md.num_rows}


def verify_placement(vpu: str, bundle: Path | None = None) -> int:
    """Place the region's sections again from the bundle's lines (``sections.place``, what a reader of
    the release does instead of reading positions) and require the archive's position, direction,
    distance and half-width bit for bit. Returns the sections compared."""
    from . import sections
    placed = sections.place(sections.flowlines(vpu, Path(bundle or config.BUNDLE)))
    keys = ("nhdplusid", "part", "k", "n_sec", "s_m", "x", "y", "nx", "ny", "wide_m")
    a = pq.read_table(config.ARCHIVE / "archive" / f"xs_{vpu}.parquet", columns=list(keys))
    if a.num_rows != len(placed["x"]):
        raise ValueError(f"[{vpu}] {len(placed['x'])} sections placed, the archive has {a.num_rows}")
    po = np.lexsort((placed["k"], placed["part"], placed["nhdplusid"]))
    ao = np.lexsort((a.column("k").to_numpy(), a.column("part").to_numpy(), a.column("nhdplusid").to_numpy()))
    for key in keys:
        got = np.asarray(placed[key])[po]
        want = a.column(key).to_numpy()[ao]
        if not np.array_equal(got.astype(want.dtype), want):
            raise ValueError(f"[{vpu}] placing the sections again gives another {key}")
    return a.num_rows


# ------------------------------------------------------------------ the release folder
def _grid_identity() -> dict:
    from . import fastgeo
    from .engine import geomorph
    bundle = json.loads((config.BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    return {"sampling_version": config.SAMPLING_VERSION, "sampling_rule": config.SAMPLING_RULE,
            "codec": "z: float32 samples as order-preserving uint32, second differences mod 2^32, int32",
            "spacing_m": config.SPACING_M, "quality_rules": getattr(geomorph, "QUALITY_RULES", None),
            "geomorph_sha256": fastgeo.PINNED, "bundle_built": bundle.get("built"),
            "bundle_format": bundle.get("format")}


def pack(vpus: list | None = None, out: Path = OUT, *, force: bool = False, log=print) -> dict:
    """Pack every merged region in ``vpus`` (default: all) whose metrics or this format changed since
    its last pack, then rewrite ``release.json`` (last). Returns ``{"packed": [...], "kept": [...]}``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    old = read_manifest(out)
    regions = dict(old.get("regions") or {})
    assets = dict(old.get("assets") or {})
    merged = merged_regions()
    todo = [v for v in (vpus or merged) if v in merged]
    packed, kept, failed = [], [], []
    for vpu in todo:
        prev = regions.get(vpu)
        rec = _record(vpu)
        fresh = (prev and prev.get("metrics_sha256") == rec["metrics_sha256"] and prev.get("format") == FORMAT
                 and all((out / n).exists() and assets.get(n, {}).get("sha256") for n in prev.get("assets", [])))
        if fresh and not force:
            kept.append(vpu)
            continue
        try:
            got = pack_region(vpu, out)
        except (ValueError, OSError) as exc:          # one region's trouble never holds up the rest
            failed.append(vpu)
            log(f"[{vpu}] NOT packed: {exc}")
            continue
        for name in (prev or {}).get("assets", []):
            assets.pop(name, None)
        regions[vpu] = got["entry"]
        assets.update(got["assets"])
        packed.append(vpu)
        log(f"[{vpu}] packed {got['entry']['sections']:,} sections, {got['entry']['medians']:,} median profiles, "
            f"{sum(a['bytes'] for a in got['assets'].values()) / 1e6:.1f} MB")
    every = lower48()
    write_manifest(out, regions, assets, [v for v in every if v not in regions])
    return {"packed": packed, "kept": kept, "failed": failed}


def write_manifest(out: Path, regions: dict, assets: dict, pending: list) -> dict:
    sections = sum(r["sections"] for r in regions.values())
    release = {
        "format": FORMAT, "tag": TAG,
        "packed": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "grid": _grid_identity(),
        "codes": {"status": list(STATUS), "bank": list(BANK), "bank_none": BANK_NONE,
                  "flags": dict((name, bit) for name, bit in FLAG_BITS)},
        "columns": COLUMN_DOC,
        "row_groups": {"numbers": NUMBERS_ROWS, "sections": SECTIONS_ROWS, "median": MEDIAN_ROWS},
        "summary": {"regions": len(regions), "pending": len(pending), "sections": sections,
                    "medians": sum(r.get("medians", 0) for r in regions.values()),
                    "bytes": sum(a["bytes"] for a in assets.values())},
        "regions": dict(sorted(regions.items())),
        "pending": pending,
        "assets": dict(sorted(assets.items())),
    }
    path = Path(out) / MANIFEST
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(release, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return release


# ------------------------------------------------------------------ the GitHub prerelease
def remote_assets(*, repo: str = REPO, tag: str = TAG, run=subprocess.run) -> dict | None:
    """``{name: sha256}`` of the release's assets, or None when the release does not exist."""
    got = run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets"], capture_output=True, text=True)
    if got.returncode != 0:
        return None
    out = {}
    for a in json.loads(got.stdout or "{}").get("assets", []):
        d = a.get("digest") or ""
        out[a["name"]] = d.split(":", 1)[1] if d.startswith("sha256:") else None
    return out


def publish(out: Path = OUT, *, repo: str = REPO, tag: str = TAG, run=subprocess.run, dry_run: bool = False,
            log=print) -> list:
    """Upload the packed assets whose sha256 differs from the release's (creating the release as a
    PRERELEASE when it is missing), ``release.json`` last. Returns the ``gh`` commands (run unless
    ``dry_run``)."""
    out = Path(out)
    release = read_manifest(out)
    if not release:
        raise ValueError(f"nothing packed in {out}")
    for name, entry in release["assets"].items():
        if sha256(out / name) != entry["sha256"]:
            raise ValueError(f"{name} changed since it was packed; pack again")
    remote = remote_assets(repo=repo, tag=tag, run=run)
    commands = []
    if remote is None:
        commands.append(["gh", "release", "create", tag, "--repo", repo, "--prerelease",
                         "--title", "STAF cross-section grid (rolling)",
                         "--notes", "Per-section cross-section metrics for every NHDPlus HR stream and canal of "
                                    "the lower 48, one profile per segment, and what rebuilds any section from "
                                    "USGS 3DEP (always a prerelease). release.json lists the regions published "
                                    "so far."])
        remote = {}
    names = [n for n in sorted(release["assets"]) if remote.get(n) != release["assets"][n]["sha256"]]
    for start in range(0, len(names), 10):
        commands.append(["gh", "release", "upload", tag, "--repo", repo, "--clobber"]
                        + [str(out / n) for n in names[start:start + 10]])
    commands.append(["gh", "release", "upload", tag, "--repo", repo, "--clobber", str(out / MANIFEST)])
    for cmd in commands:
        if dry_run:
            log(" ".join(cmd[:4]) + (" ... " + ", ".join(Path(c).name for c in cmd[7:]) if cmd[2] == "upload" else ""))
            continue
        got = run(cmd, capture_output=True, text=True, timeout=GH_TIMEOUT_S)
        if got.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(got.stderr or got.stdout or '').strip()[:400]}")
    if not dry_run:
        log(f"published {len(names)} assets and {MANIFEST} to {tag}")
    return commands


def status(out: Path = OUT, *, remote: bool = False, log=print) -> dict:
    release = read_manifest(out)
    merged = merged_regions()
    packed = sorted((release.get("regions") or {}).keys())
    stale = [v for v in packed if v in merged and release["regions"][v].get("metrics_sha256") != _record(v)["metrics_sha256"]]
    info = {"merged": len(merged), "packed": len(packed), "to_pack": [v for v in merged if v not in packed] + stale}
    log(f"merged {len(merged)} of {len(lower48())} regions; packed {len(packed)}; to pack {len(info['to_pack'])}"
        + (f" ({', '.join(info['to_pack'][:12])}{' ...' if len(info['to_pack']) > 12 else ''})" if info["to_pack"] else ""))
    if release:
        s = release["summary"]
        log(f"release folder {out}: {s['sections']:,} sections, {s['medians']:,} median profiles, "
            f"{s['bytes'] / 1e9:.2f} GB in {len(release['assets'])} assets")
    if remote and release:
        live = remote_assets()
        if live is None:
            log(f"{TAG} does not exist yet")
            info["published"] = 0
        else:
            behind = [n for n, a in release["assets"].items() if live.get(n) != a["sha256"]]
            info["published"] = len(release["assets"]) - len(behind)
            info["unpublished"] = behind
            log(f"{TAG}: {info['published']} of {len(release['assets'])} assets current"
                + ("" if not behind else f"; to upload: {len(behind)}")
                + ("" if live.get(MANIFEST) == sha256(Path(out) / MANIFEST) else f"; {MANIFEST} differs"))
    return info
