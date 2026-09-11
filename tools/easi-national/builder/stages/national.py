"""The one-time national pulls: the NHDPlus V2 value-added attributes (with
the ENHD ``tocomid``), the COMID index, the dam inventory, the HUC4 polygons,
the StreamCat cache (every region, every metric the adapters read) and the
NAS cache (every established nonindigenous record).

About 1.5 GB in total, fetched once and reused by every chunk.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from .. import config, http
from ..paths import DataRoot, atomic_write_text
from ..state import Control, Progress, UnitStates, digest

UNIT = "national"

#: The VAA columns the pipeline keeps (whatever subset the parquet carries).
VAA_COLUMNS = ("comid", "reachcode", "vpuid", "rpuid", "gnis_name", "gnis_id", "ftype",
               "fcode", "streamorde", "streamcalc", "slope", "lengthkm", "areasqkm",
               "totdasqkm", "divdasqkm", "hydroseq", "uphydroseq", "dnhydroseq",
               "levelpathi", "dnlevelpat", "terminalpa", "startflag", "terminalfl",
               "divergence", "fromnode", "tonode", "pathlength", "arbolatesu")


def _stage_inputs(stage: str) -> str:
    return digest(stage, 1)


def _streamcat_inputs() -> str:
    return digest("streamcat", config.streamcat_names(), config.STREAMCAT_AOIS, 1)


def _gdb_steps(root: DataRoot, progress: Progress) -> tuple:
    """Conversions of the bulk geodatabases, only when they are on disk."""
    from . import local_gdb
    steps: list = []
    if local_gdb.nhdplus_gdb(root) is not None:
        steps.append(("flowlines", lambda: local_gdb.convert_flowlines(root, progress)))
        steps.append(("huc12", lambda: local_gdb.convert_huc12(root, progress)))
    if local_gdb.attains_gdb(root) is not None:
        steps.append(("attains", lambda: local_gdb.convert_attains(root, progress)))
    return tuple(steps)


def _inputs_for(stage: str, root: DataRoot) -> str:
    from . import local_gdb
    if stage == "streamcat":
        return _streamcat_inputs()
    if stage in ("flowlines", "huc12"):
        gdb = local_gdb.nhdplus_gdb(root)
        return digest(stage, gdb.name if gdb else "", 1)
    if stage == "attains":
        gdb = local_gdb.attains_gdb(root)
        return digest(stage, gdb.name if gdb else "", 1)
    if stage == "wqp_monthly":
        from . import wqp_national
        return digest(stage, config.WQP_MONTHLY_START, wqp_national.months()[-1], 1)
    if stage == "dem1m_index":
        return digest(stage, "s3-listings", 2)
    if stage == "dem19_index":
        return digest(stage, "s3-listings", 1)
    return _stage_inputs(stage)


def _dem_catalog(root: DataRoot, progress: Progress, control: Control):
    """The 3DEP 1 m tile catalog (bucket listings, about half an hour)."""
    from .. import dem
    return dem.build_catalog(root, progress, control, workers=config.DEM_CATALOG_WORKERS)


def _dem_catalog19(root: DataRoot, progress: Progress, control: Control):
    """The 3DEP 1/9 arc-second quad catalog (one bucket listing)."""
    from .. import dem
    return dem.build_catalog19(root, progress, control)


def _nas_cache(root: DataRoot, progress: Progress, control: Control):
    """The national NAS cache (every established record, about a hundred pages)."""
    from .nas_national import run_nas_national
    return run_nas_national(root, progress, control)


def _streamcat_cache(root: DataRoot, progress: Progress, control: Control):
    """The national StreamCat cache (~105 region requests, about half an hour)."""
    from .streamcat_national import run_streamcat_national
    return run_streamcat_national(root, progress, control)


# ------------------------------------------------------------------ VAA
def fetch_vaa(root: DataRoot, progress: Progress) -> Path:
    """Download the 245 MB VAA parquet through pynhd (cached by pynhd itself)."""
    if root.vaa_raw.exists():
        return root.vaa_raw
    progress.say("downloading NHDPlus V2 VAA (245 MB) ...")
    import pynhd
    pynhd.nhdplus_vaa(parquet_path=root.vaa_raw)
    return root.vaa_raw


def fetch_enhd(root: DataRoot, progress: Progress) -> Optional[Path]:
    """Download the 160 MB ENHD attributes (``tocomid``) through pynhd."""
    if root.enhd_raw.exists():
        return root.enhd_raw
    progress.say("downloading ENHD attributes (160 MB) ...")
    try:
        import pynhd
        pynhd.enhd_attrs(parquet_path=root.enhd_raw)
    except Exception as exc:  # noqa: BLE001 - tocomid is informational
        progress.say(f"ENHD download failed ({exc}); tocomid will be absent")
        return None
    return root.enhd_raw


def build_slim(root: DataRoot, progress: Progress) -> Path:
    """``vaa_slim.parquet``: the kept VAA columns + huc8/huc4 + tocomid, sorted by comid."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    progress.say("building vaa_slim.parquet ...")
    schema = pq.read_schema(root.vaa_raw)
    present = [c for c in VAA_COLUMNS if c in schema.names]
    table = pq.read_table(root.vaa_raw, columns=present)
    comid = pc.cast(table.column("comid"), pa.int64())
    table = table.set_column(table.schema.get_field_index("comid"), "comid", comid)
    reach = pc.cast(table.column("reachcode"), pa.string())
    huc8 = pc.utf8_slice_codeunits(reach, 0, 8)
    huc4 = pc.utf8_slice_codeunits(reach, 0, 4)
    table = table.append_column("huc8", huc8).append_column("huc4", huc4)
    if root.enhd_raw.exists():
        enhd_schema = pq.read_schema(root.enhd_raw)
        if "tocomid" in enhd_schema.names:
            enhd = pq.read_table(root.enhd_raw, columns=["comid", "tocomid"])
            enhd = enhd.set_column(0, "comid", pc.cast(enhd.column("comid"), pa.int64()))
            enhd = enhd.set_column(1, "tocomid", pc.cast(enhd.column("tocomid"), pa.int64()))
            table = table.join(enhd, keys="comid", join_type="left outer")
    table = table.sort_by("comid")
    tmp = root.vaa.with_suffix(".parquet.part")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(root.vaa)
    progress.say(f"vaa_slim.parquet: {table.num_rows:,} reaches, {len(table.column_names)} columns")
    return root.vaa


def build_index(root: DataRoot, progress: Progress) -> None:
    """``comid_huc4.parquet`` (published), ``huc8_index.json`` and ``huc4_vpu.json``."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    progress.say("building the COMID index ...")
    table = pq.read_table(root.vaa, columns=["comid", "huc8", "huc4", "vpuid"])
    index = pa.table({"comid": table.column("comid"), "huc4": table.column("huc4")})
    tmp = root.index.with_suffix(".parquet.part")
    pq.write_table(index, tmp, compression="zstd")
    tmp.replace(root.index)

    counts: dict[str, int] = Counter()
    vpu_votes: dict[str, Counter] = defaultdict(Counter)
    huc4_votes: dict[str, Counter] = defaultdict(Counter)
    for huc8, huc4, vpu in zip(table.column("huc8").to_pylist(),
                               table.column("huc4").to_pylist(),
                               table.column("vpuid").to_pylist()):
        if not huc8 or len(huc8) < 8:
            continue
        counts[huc8] += 1
        vpu_votes[huc8][str(vpu or "")] += 1
        huc4_votes[huc4][str(vpu or "")] += 1
    huc8_index = {h: {"n": n, "vpu": vpu_votes[h].most_common(1)[0][0], "huc4": h[:4]}
                  for h, n in counts.items()}
    huc4_vpu = {h: votes.most_common(1)[0][0] for h, votes in huc4_votes.items() if h}
    atomic_write_text(root.huc8_index, json.dumps(huc8_index, sort_keys=True))
    atomic_write_text(root.huc4_vpu, json.dumps(huc4_vpu, sort_keys=True, indent=1))
    progress.say(f"index: {index.num_rows:,} COMIDs, {len(huc8_index):,} HUC8s, "
                 f"{len(huc4_vpu)} HUC4s")


# ------------------------------------------------------------------ NID
def fetch_nid(root: DataRoot, progress: Progress, control: Control) -> Path:
    """Every mapped NID dam (the app's FeatureServer, paged) -> ``nid.parquet``."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if root.nid.exists():
        return root.nid
    rows: list[dict] = []
    offset = 0
    progress.begin(UNIT, "nid", total=0, message="paging the National Inventory of Dams")
    while True:
        control.check()
        params = {"where": "1=1", "outFields": "NAME,NID_STORAGE,DAM_HEIGHT,NIDID",
                  "returnGeometry": "true", "outSR": "4326", "f": "json",
                  "resultOffset": offset, "resultRecordCount": config.NID_PAGE}
        data = http.get_json(config.NID_URL, params)
        if data.get("error"):
            raise RuntimeError(f"NID query failed: {data['error']}")
        features = data.get("features") or []
        for feature in features:
            geom = feature.get("geometry") or {}
            attrs = feature.get("attributes") or {}
            if geom.get("x") is None or geom.get("y") is None:
                continue
            rows.append({"name": attrs.get("NAME"), "storage": attrs.get("NID_STORAGE"),
                         "height": attrs.get("DAM_HEIGHT"), "nid_id": attrs.get("NIDID"),
                         "lon": float(geom["x"]), "lat": float(geom["y"])})
        offset += len(features)
        progress.tick(done=offset, message=f"{offset:,} dams")
        if len(features) < config.NID_PAGE and not data.get("exceededTransferLimit"):
            break
    table = pa.Table.from_pylist(rows)
    tmp = root.nid.with_suffix(".parquet.part")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(root.nid)
    progress.say(f"nid.parquet: {len(rows):,} dams")
    return root.nid


# ------------------------------------------------------------------ HUC4
def fetch_huc4_polygons(root: DataRoot, progress: Progress) -> Path:
    """The ~222 CONUS HUC4 polygons (fabric HUC04 collection), simplified, for
    the coverage map."""
    if root.huc4_geojson.exists():
        return root.huc4_geojson
    from shapely.geometry import mapping, shape
    progress.say("fetching HUC4 polygons ...")
    url = f"{config.FABRIC_BASE}/nhdplusv2-huc04/items"
    features: list[dict] = []
    offset, page = 0, 25            # whole-CONUS polygons are heavy: small pages
    while True:
        data = http.get_json(url, {"f": "json", "limit": page, "offset": offset},
                             timeout=300.0)
        batch = data.get("features") or []
        features.extend(batch)
        progress.tick(done=len(features), message=f"HUC4 polygons: {len(features)}")
        if len(batch) < page:
            break
        offset += len(batch)
    out = []
    for feature in features:
        props = feature.get("properties") or {}
        code = str(props.get("04") or props.get("huc4") or props.get("huc_4") or "").strip()
        if not code:
            continue
        try:
            geom = shape(feature["geometry"]).simplify(0.005, preserve_topology=True)
        except Exception:  # noqa: BLE001
            continue
        out.append({"type": "Feature", "properties": {"huc4": code},
                    "geometry": mapping(geom)})
    atomic_write_text(root.huc4_geojson,
                      json.dumps({"type": "FeatureCollection", "features": out}))
    progress.say(f"huc4.geojson: {len(out)} polygons")
    return root.huc4_geojson


def _wqp_monthly(root: DataRoot, progress: Progress, control: Control) -> None:
    """Every month of national TN and TP results through the WQX3 service,
    then one parquet (``wqp_national``)."""
    from . import wqp_national
    result = wqp_national.run_wqp_monthly(root, progress, control, workers=config.WQP_MONTHLY_WORKERS)
    if result["done"] != result["total"] or not result["combined"]:
        raise RuntimeError(f"WQP monthly: {result['done']} of {result['total']} months done")


# ------------------------------------------------------------------ driver
def run_national(root: DataRoot, states: UnitStates, progress: Progress,
                 control: Control, steps: Optional[list[str]] = None) -> None:
    """All one-time pulls (or the named ``steps``), each skipped when its done
    marker matches."""
    root.ensure()
    all_steps = (
        ("vaa", lambda: (fetch_vaa(root, progress), fetch_enhd(root, progress),
                         build_slim(root, progress))),
        ("index", lambda: build_index(root, progress)),
        ("nid", lambda: fetch_nid(root, progress, control)),
        ("huc4", lambda: fetch_huc4_polygons(root, progress)),
        ("streamcat", lambda: _streamcat_cache(root, progress, control)),
        ("nas", lambda: _nas_cache(root, progress, control)),
        ("dem1m_index", lambda: _dem_catalog(root, progress, control)),
        ("dem19_index", lambda: _dem_catalog19(root, progress, control)),
        ("wqp_monthly", lambda: _wqp_monthly(root, progress, control)),
    ) + _gdb_steps(root, progress)
    wanted = {str(s) for s in steps} if steps else None
    for stage, fn in all_steps:
        if wanted is not None and stage not in wanted:
            continue
        inputs = _inputs_for(stage, root)
        if states.is_done(UNIT, stage, inputs):
            continue
        control.check()
        states.set(UNIT, stage, "running", inputs=inputs)
        started = time.monotonic()
        progress.begin(UNIT, stage, message=f"national {stage}")
        try:
            fn()
        except Exception as exc:
            states.set(UNIT, stage, "failed", inputs=inputs, note=str(exc)[:300])
            raise
        states.set(UNIT, stage, "done", inputs=inputs,
                   note=f"{time.monotonic() - started:.0f} s")
