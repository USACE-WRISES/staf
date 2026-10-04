"""National and per-region tables for the apps' lookups (bundle v2, Phase 3).

- ``streamcat2_<hu4>.parquet`` (V2 root, per region): the StreamCat columns the apps read, for the
  region's V2 COMIDs (the national builder's pull of the StreamCat API, float64). Four columns the
  apps read are not in that pull: ``pctimp2001ws`` (SFARI land-use change), ``rdcrsws`` and
  ``damdensws`` (SFARI, DEEP) and ``bfiws`` (DEEP); ``streamcat_extra`` pulls them with the same
  builder code and API into ``<sources>/streamcat/national/streamcat_extra.parquet``;
- ``nid_points.parquet`` (national): every NID dam, for the "dams within 1 mile" lookups (EASI M20,
  SFARI #16 and #77, DEEP), with the fields the apps read;
- ``nas_taxa.parquet`` (national): USGS NAS occurrence records with HUC12 and status (EASI M19
  counts established taxa by HUC12; the live API stops at 500 records, this table does not);
- ``wqp_results.parquet``, ``wqp_temperature.parquet`` and ``wqp_stations.parquet`` (national): every
  TN and TP stream result since 2015 (the national builder's pull plus the back-fill and the re-pull)
  with EASI's exclusion reason and the raw value SFARI reads, and every stream temperature result
  since 2016 (EASI's temperature context), each with its WQP result identifier, so both apps'
  5-mile queries run locally (EASI M15 and M13's context, SFARI #58).
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute  # noqa: F401 - pa.compute
import pyarrow.parquet as pq

from . import REPO_ROOT, sources

NATIONAL = Path(r"D:\Data\easi-national\national")
STREAMCAT_COLUMNS = (
    "pctimp2019ws", "pctcrop2019ws", "pcthay2019ws", "pctwdwet2019ws", "pcthbwet2019ws",
    "pctwdwet2019wsrp100", "pcthbwet2019wsrp100", "pctconif2019wsrp100", "pctdecid2019wsrp100",
    "pctmxfst2019wsrp100", "pctshrb2019wsrp100", "pctgrs2019wsrp100",
    "kffactws", "rddensws", "damnrmstorws", "runoffws",
    "hydcat", "hydws", "chemcat", "chemws", "conncat", "connws", "sedcat", "sedws",
    "tempcat", "tempws", "habtcat", "habtws", "prg_bmmi0809")
#: the columns the national builder's pull lacks, by StreamCat base name (watershed values)
STREAMCAT_EXTRA = ("pctimp2001", "rdcrs", "damdens", "bfi")
STREAMCAT_EXTRA_ROOT = sources.SOURCES_DIR / "streamcat"


def log_default(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def streamcat_extra(log: Callable = log_default) -> dict:
    """The four columns the national pull lacks, for every CONUS region, through the national
    builder's own region pull (``run_streamcat_national``) pointed at a root of its own, so the
    EASI national data root is left as it is."""
    builder = str(REPO_ROOT / "tools" / "easi-national")
    if builder not in sys.path:
        sys.path.insert(0, builder)
    from builder.paths import DataRoot
    from builder.stages.streamcat_national import run_streamcat_national
    from builder.state import Control, Progress
    root = DataRoot(STREAMCAT_EXTRA_ROOT)
    for folder in (root.national, root.state):
        folder.mkdir(parents=True, exist_ok=True)
    progress = Progress(root, quiet=True)
    names = list(STREAMCAT_EXTRA)
    cache = run_streamcat_national(root, progress, Control(root), names=names,
                                   aoi_by_name=dict((n, "ws") for n in names),
                                   cache=root.national / "streamcat_extra.parquet",
                                   ledger_name="streamcat-extra", parts=root.national / "streamcat_extra_parts")
    t = pq.read_table(cache)
    stats = {"reaches": t.num_rows, "columns": [c for c in t.column_names if c != "comid"],
             "bytes": cache.stat().st_size}
    sources.record("streamcat_extra", dict(stats, service="EPA StreamCat API, by region (national builder's pull)",
                                          names=names))
    log(f"StreamCat extra columns: {stats}")
    return stats


def _streamcat_tables():
    """``(comids sorted, column -> float64 values)`` from the national pull plus the extra columns."""
    sc = pq.read_table(NATIONAL / "streamcat.parquet", columns=["comid"] + list(STREAMCAT_COLUMNS))
    extra_path = STREAMCAT_EXTRA_ROOT / "national" / "streamcat_extra.parquet"
    if extra_path.exists():
        ex = pq.read_table(extra_path)
        sc = sc.join(ex, keys="comid", join_type="full outer")
    sc = sc.sort_by("comid")
    return sc


def streamcat(v2_root: Path, log: Callable = log_default) -> dict:
    manifest = json.loads((v2_root / "data" / "manifest.json").read_text(encoding="utf-8"))
    sc = _streamcat_tables()
    columns = [c for c in sc.column_names if c != "comid"]
    comid = sc.column("comid").to_numpy().astype(np.int64)
    order = np.argsort(comid)
    out = {}
    for hu4, entry in sorted(manifest["vpus"].items()):
        ids = pq.read_table(v2_root / "data" / entry["lines"]["file"], columns=["nhdplusid"]).column("nhdplusid").to_numpy()
        pos = np.searchsorted(comid[order], ids)
        pos = np.minimum(pos, len(order) - 1)
        hit = comid[order][pos] == ids
        rows = np.where(hit, order[pos], 0)
        cols = {"comid": pa.array(ids.astype(np.int64))}
        for c in columns:
            v = sc.column(c).to_numpy(zero_copy_only=False).astype(np.float64)[rows]
            v = np.where(hit, v, np.nan)
            # float64: the number the apps parse from the API (float32 moves damnrmstorws by up to 0.05
            # and flips some values at the apps' 2 and 4 decimal rounding)
            cols[c] = pa.array(v, mask=np.isnan(v))
        path = v2_root / "data" / f"streamcat2_{hu4}.parquet"
        pq.write_table(pa.table(cols), path, compression="zstd", compression_level=19)
        out[hu4] = {"comids": int(len(ids)), "with_streamcat": int(hit.sum()), "bytes": path.stat().st_size}
        log(f"[V2 {hu4}] StreamCat: {out[hu4]}")
    return out


def _text(series) -> pa.Array:
    """Strings with missing values as nulls (pandas 3 keeps NaN in string columns)."""
    return pa.array([None if pd.isna(v) else str(v) for v in series.tolist()], type=pa.string())


def dem_catalogs(out_dir: Path, log: Callable = log_default) -> dict:
    """The catalogs of USGS's 3DEP 1 m project tiles and 1/9 arc-second quads (footprints, URLs on
    USGS's ``prd-tnm`` bucket), which the apps' cross-sections read the tile files with
    (``easi.datasources.dem_tiles``). The EASI national builder builds and refreshes them
    (``tools/easi-national/builder/dem.py``); this copies the current ones into the bundle."""
    import shutil
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for src, name in ((NATIONAL / "dem" / "1m" / "tiles.parquet", "dem1m_tiles.parquet"),
                      (NATIONAL / "dem" / "19" / "quads.parquet", "dem19_quads.parquet")):
        if not src.exists():
            raise SystemExit(f"missing {src}: build it with the EASI national builder's catalog step")
        shutil.copy2(src, out_dir / name)
        stats[name] = {"rows": pq.read_metadata(src).num_rows, "bytes": src.stat().st_size}
    log(f"3DEP catalogs: {stats}")
    return stats


def nid_points(out_dir: Path, log: Callable = log_default) -> dict:
    """Every NID dam for the "dams within 1 mile" lookups (EASI M20, SFARI #16 and #77, DEEP), from
    the FeatureServer pull the apps' own queries read, in the coordinates they ask for."""
    path_in = sources.latest_nid_fs()
    t = pq.read_table(path_in)
    keep = ["nid_id", "name", "lon", "lat", "nid_storage_acft", "normal_storage_acft", "dam_height_ft",
            "nid_height_ft", "river", "state"]
    t = t.select(keep).filter(pa.compute.and_(pa.compute.is_valid(t.column("lon")), pa.compute.is_valid(t.column("lat"))))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "nid_points.parquet"
    pq.write_table(t, path, compression="zstd", compression_level=19)
    stats = {"dams": t.num_rows, "source": path_in.name, "bytes": path.stat().st_size}
    log(f"NID points: {stats}")
    return stats


def nas_taxa(out_dir: Path, log: Callable = log_default) -> dict:
    cols = ["speciesID", "scientificName", "commonName", "group", "state", "huc8", "huc10", "huc12", "year", "status"]
    t = pq.read_table(NATIONAL / "nas.parquet", columns=cols)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "nas_taxa.parquet"
    pq.write_table(t, path, compression="zstd", compression_level=19,
                   use_dictionary=["scientificName", "commonName", "group", "state", "status"])
    stats = {"records": t.num_rows, "bytes": path.stat().st_size}
    log(f"NAS taxa: {stats}")
    return stats


#: EASI's exclusion reasons, in its order of tests (code = position; 0 keeps the result)
WQP_REASONS = ("ok", "blank", "rejected", "censored", "non_total_fraction", "unsupported_unit", "nonnumeric")
WQP_PARAMS = ("tn", "tp")
WQP_START = "2015-01-01"


def _wqp_builder():
    for path in (REPO_ROOT / "tools" / "easi-national", REPO_ROOT / "apps" / "easi"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from builder.stages import wqp_local
    from easi.datasources import wqp as app_wqp
    return wqp_local, app_wqp


def _sfari_value(raw: str) -> float:
    """SFARI's reading of ResultMeasureValue: ``float`` of the stripped text, NaN when it fails."""
    text = (raw or "").strip()
    if not text:
        return math.nan
    try:
        return float(text)
    except ValueError:
        return math.nan


def _result_ids(ids) -> dict:
    """WQP result identifiers in three compact columns: ``rid_num`` for ``STORET-<number>``,
    ``rid_uuid`` (16 bytes) for the UUIDs USGS results carry, ``rid_text`` for anything else; one of
    the three is set per row. ``hrslim.points.result_id`` turns them back into the identifier."""
    import re
    import uuid
    num = np.zeros(len(ids), dtype=np.int64)
    has_num = np.zeros(len(ids), dtype=bool)
    uu: list = [None] * len(ids)
    text: list = [None] * len(ids)
    storet = re.compile(r"^STORET-(\d{1,18})$")
    for i, rid in enumerate(ids):
        rid = (rid or "").strip()
        m = storet.match(rid)
        if m:
            num[i], has_num[i] = int(m.group(1)), True
            continue
        try:
            u = uuid.UUID(rid)
            if str(u) == rid.lower():
                uu[i] = u.bytes
                continue
        except ValueError:
            pass
        text[i] = rid or None
    return {"rid_num": pa.array(num, mask=~has_num), "rid_uuid": pa.array(uu, type=pa.binary(16)),
            "rid_text": pa.array(text, type=pa.string())}


def wqp_tables(out_dir: Path, log: Callable = log_default) -> dict:
    """The bundle's WQP tables: ``wqp_results`` (TN and TP since 2015), ``wqp_temperature`` (stream
    temperature since 2016) and the ``wqp_stations`` both index into; every row keeps its WQP result
    identifier, so an answer leads back to its results (``hrslim.points.wqp_results``) and a result
    to its full record (``wqp_temperature.record`` for temperature).

    TN and TP rows come from the national builder's own selection and normalization (``wqp_local``),
    so the station, date, reason and unit rules are EASI's; ``raw`` keeps the number SFARI's median
    reads (any status, fraction or unit). EASI's value is ``raw * 0.001`` where ``milli`` (ug/L),
    else ``raw``, for rows with reason 0. Duplicates are dropped per parameter on the result
    identifier as the builder does. Months re-pulled into ``wqp_recent.parquet`` replace the same
    months of the national pull (late submissions). Temperature rows follow EASI's temperature rule
    (``wqp_temperature.normalize``) and keep Fahrenheit results converted under their own reason;
    values in hundredths of a degree C (``value_c100``, int16). A station keeps the coordinates of
    its first result, newest pull first; a few were moved between pulls and are counted."""
    from . import wqp_temperature
    wqp_local, app_wqp = _wqp_builder()
    recent = sources.SOURCES_DIR / "wqp" / "wqp_recent.parquet"
    replaced: set = set()
    paths = [NATIONAL / "wqp" / "wqp_results.parquet", sources.SOURCES_DIR / "wqp" / "wqp_backfill.parquet"]
    if recent.exists():
        # months re-pulled later hold the late submissions: they replace those months of the others
        replaced = set(pq.read_table(recent, columns=["source_month"]).column("source_month").unique().to_pylist())
        paths.insert(0, recent)
    reason_code = dict((r, i) for i, r in enumerate(WQP_REASONS))
    seen: set = set()
    cols = dict(param=[], station=[], date=[], raw=[], reason=[], milli=[], rid=[])
    stations: dict = {}
    station_coords: dict = {}
    moved = 0
    counts = {}
    for path in paths:
        picked = wqp_local.select_rows(path, [-180.0, -90.0, 180.0, 90.0], WQP_START)
        if path != recent and replaced:
            picked = [r for r in picked if r["ActivityStartDate"][:7] not in replaced]
        log(f"WQP {path.name}: {len(picked):,} TN/TP results since {WQP_START}")
        kept = 0
        for lo in range(0, len(picked), 200_000):
            part = picked[lo:lo + 200_000]
            normalized = wqp_local.normalize(part)
            if len(normalized) != len(part):
                raise RuntimeError(f"normalize returned {len(normalized)} rows for {len(part)}")
            for src, r in zip(part, normalized):
                ident = (r["param"], r.get("result_id") or (r["station"], r.get("date"), r.get("value"), r["reason"]))
                if ident in seen:
                    continue
                seen.add(ident)
                if r["lat"] is None or r["lon"] is None or not r.get("date"):
                    continue
                key = r["station"]
                if key not in stations:
                    stations[key] = len(stations)
                    station_coords[key] = (r["lat"], r["lon"], r.get("station_name") or "", r.get("org") or "")
                elif station_coords[key][:2] != (r["lat"], r["lon"]):
                    moved += 1
                cols["param"].append(WQP_PARAMS.index(r["param"]))
                cols["station"].append(stations[key])
                cols["date"].append(r["date"])
                cols["raw"].append(_sfari_value(src["ResultMeasureValue"]))
                cols["reason"].append(reason_code[r["reason"]])
                cols["milli"].append(app_wqp._unit_factor(src["ResultMeasure/MeasureUnitCode"]) == 0.001)
                cols["rid"].append(r.get("result_id") or "")
                kept += 1
            del normalized
        counts[path.name] = kept
        del picked
    order = np.lexsort((np.asarray(cols["date"]), np.asarray(cols["param"]), np.asarray(cols["station"])))
    tab = pa.table(dict({
        "station": pa.array(np.asarray(cols["station"], dtype=np.int32)[order]),
        "param": pa.array(np.asarray(cols["param"], dtype=np.int8)[order]),
        "date": pa.array(np.asarray(cols["date"], dtype="datetime64[D]")[order]),
        "raw": pa.array(np.asarray(cols["raw"], dtype=np.float64)[order]),
        "reason": pa.array(np.asarray(cols["reason"], dtype=np.int8)[order]),
        "milli": pa.array(np.asarray(cols["milli"], dtype=bool)[order]),
    }, **_result_ids([cols["rid"][i] for i in order])))
    nutrients_dates = (str(min(cols["date"])), str(max(cols["date"])))
    del cols

    # temperature: every month of the pull, newest first for station coordinates
    import pandas as pd
    parts = sorted((wqp_temperature.FOLDER / "normalized").glob("*.parquet"), reverse=True)
    temp = pd.concat([pq.read_table(p).to_pandas() for p in parts], ignore_index=True) if parts else None
    temp_stats = {}
    if temp is not None and len(temp):
        rid = temp["result_id"].fillna("")
        temp = temp[~(rid.duplicated(keep="first") & (rid != ""))]     # a blank identifier is no duplicate
        first = temp.drop_duplicates("station", keep="first")
        for key, la, lo, nm, og in zip(first["station"], first["lat"], first["lon"], first["station_name"], first["org"]):
            if key not in stations:
                stations[key] = len(stations)
                station_coords[key] = (float(la), float(lo), nm or "", og or "")
        idx = temp["station"].map(stations).to_numpy(dtype=np.int32)
        moved += int((temp["lat"].to_numpy() != np.array([station_coords[k][0] for k in temp["station"]])).sum())
        # Celsius results that are not realistic stream temperatures get their own reasons (the
        # owner's screen); their values stay, so EASI's own rule can still count them
        screened, screen_stats = wqp_temperature.screen(temp.assign(station=idx))
        temp = temp.assign(reason=screened)
        torder = np.lexsort((temp["date"].to_numpy(dtype="datetime64[D]"), idx))
        temp = temp.iloc[torder]
        v = temp["value"].to_numpy(dtype=np.float64)
        c100 = np.round(np.nan_to_num(v) * 100).astype(np.int64)
        # a Celsius result beyond +-327.67 (a sentinel or a unit slip; EASI's rule still counts it)
        # does not fit int16, so the column widens to int32 rather than wrapping
        ctype = np.int16 if int(np.abs(c100).max(initial=0)) <= np.iinfo(np.int16).max else np.int32
        ttab = pa.table(dict({
            "station": pa.array(idx[torder]),
            "date": pa.array(temp["date"].to_numpy(dtype="datetime64[D]")),
            "value_c100": pa.array(c100.astype(ctype), mask=np.isnan(v)),
            "reason": pa.array(temp["reason"].to_numpy(dtype=np.int8)),
        }, **_result_ids(temp["result_id"].tolist())))
        tmeta = {b"wqp_param": b"temp", b"wqp_reasons": json.dumps(wqp_temperature.REASONS).encode(),
                 b"wqp_value_scale": b"0.01", b"wqp_start": (wqp_temperature.START + "-01").encode(),
                 b"wqp_screen": json.dumps(wqp_temperature.screen_parameters()).encode()}
        tpath = out_dir / "wqp_temperature.parquet"
        out_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(ttab.replace_schema_metadata(tmeta), tpath, compression="zstd", compression_level=19,
                       use_dictionary=False, column_encoding={"station": "DELTA_BINARY_PACKED",
                                                              "date": "DELTA_BINARY_PACKED"})
        treasons = np.bincount(ttab.column("reason").to_numpy(), minlength=len(wqp_temperature.REASONS))
        temp_stats = {"results": ttab.num_rows, "bytes": tpath.stat().st_size,
                      "bytes_per_result": round(tpath.stat().st_size / max(1, ttab.num_rows), 2),
                      "value_type": np.dtype(ctype).name,
                      "outside_minus1_to_40_c": int(((v < -1) | (v > 40)).sum()),
                      "screen": screen_stats,
                      "reasons": dict(zip(wqp_temperature.REASONS, (int(v) for v in treasons))),
                      "first_date": str(temp["date"].min()), "last_date": str(temp["date"].max())}
        del temp, ttab
    st = sorted(stations.items(), key=lambda kv: kv[1])
    stab = pa.table({
        "station": pa.array([k for k, _ in st]),
        "lat": pa.array([station_coords[k][0] for k, _ in st], type=pa.float64()),
        "lon": pa.array([station_coords[k][1] for k, _ in st], type=pa.float64()),
        "name": pa.array([station_coords[k][2] for k, _ in st]),
        "org": pa.array([station_coords[k][3] for k, _ in st]),
    })
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {b"wqp_params": json.dumps(WQP_PARAMS).encode(), b"wqp_reasons": json.dumps(WQP_REASONS).encode(),
            b"wqp_start": WQP_START.encode()}
    path = out_dir / "wqp_results.parquet"
    pq.write_table(tab.replace_schema_metadata(meta), path, compression="zstd", compression_level=19)
    spath = out_dir / "wqp_stations.parquet"
    pq.write_table(stab, spath, compression="zstd", compression_level=19)
    reasons = np.bincount(tab.column("reason").to_numpy(), minlength=len(WQP_REASONS))
    stats = {"results": tab.num_rows, "by_source": counts, "months_from_the_recent_pull": sorted(replaced),
             "stations": stab.num_rows,
             "results_placing_their_station_elsewhere": moved,
             "reasons": dict(zip(WQP_REASONS, (int(v) for v in reasons))),
             "first_date": nutrients_dates[0], "last_date": nutrients_dates[1],
             "bytes": path.stat().st_size, "station_bytes": spath.stat().st_size, "temperature": temp_stats}
    log(f"WQP tables: {stats}")
    return stats


wqp_nutrients = wqp_tables          # the earlier name
