"""Step ``nrsa``: the NRSA validation frame, the field truth every desktop
metric is judged against.

``analysis/nrsa/nrsa_targets.parquet``: one row per NRSA site visit (index
visit) with the published condition classes recoded to Good/Fair/Poor
(``t__<name>``), the continuous indicators of the raw files (``x__<name>``),
the design weight, the Strahler category and, per station, EPA's 2013-14
reference class ``rt_nrsa`` (R, In, Im).

``analysis/nrsa/nrsa_desktop.parquet``: one row per station with a COMID:
the desktop metrics scored by the app's own evaluator. Stations inside the
scored extent take their stored evidence record (the only rows with
cross-section metrics); the others get a synthetic record from the national
caches through the builder's own lookups (StreamCat cache, ATTAINS
geodatabase rows, the ten-year WQP parquet, NID, NAS), so the values come
from one code path. Raw StreamCat columns and the derived landscape
quantities ride along so the desktop screen can be evaluated per station.

``analysis/nrsa/nrsa_frame.parquet``: station x cycle rows joining the
targets, the archive's field metrics (``apps/stream-curves/data/nrsa``) and
the desktop row. ``screen_check.csv``: how EPA's reference designations pass
the desktop screens (decision 2's external check).
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np

from .. import REPO_ROOT, config
from ..paths import DataRoot
from ..state import Control, Progress, digest
from ..stages import common, joins, local_gdb, nas_national, streamcat_national, wqp_local
from ..units import load_huc4_vpu
from . import ANALYSIS_VERSION, screens, stats
from .strata import NRSA_RAW, strata_path
from .values import (IDENTITY, concrete, derived_landscape, flatten_trace, join_caches, record_extras,
                     values_path, xs_extras)

NRSA_DIR = REPO_ROOT / "apps" / "stream-curves" / "data" / "nrsa"
CYCLES = ("1314", "1819", "2324")
RAW_FILES = {
    "1314": ("1314/nrsa1314_allcond_05312019_0.csv",),
    "1819": ("1819/nrsa1819_data_for_populationestimates.csv",),
    "2324": ("2324/nrsa2324_benthicmmi.csv", "2324/nrsa2324_fishmmi.csv"),
}
SITE_FILE_1314 = "1314/nrsa1314_siteinformation_wide_04292019.csv"
#: raw class column -> target key
CLASS_TARGETS = {
    "ANC_COND": "anc", "ACID_COND": "acid", "SAL_COND": "sal", "NTL_COND": "ntl", "PTL_COND": "ptl",
    "BENT_MMI_COND": "bent_mmi", "OE_COND": "oe", "INSTRMCVR_COND": "instrmcvr", "BEDSED_COND": "bedsed",
    "RIPDIST_COND": "ripdist", "RIPVEG_COND": "ripveg", "FISH_MMI_COND": "fish_mmi",
    "ENT_1X_STV_COND": "ent", "MICX_COND": "micx", "MICX_EPA_COND": "micx", "HG_COND": "hg",
}
#: raw continuous column -> target key
CONTINUOUS_TARGETS = {
    "MMI_BENT": "mmi_bent", "OE_SCORE": "oe_score", "L_XFC_NAT": "l_xfc_nat", "LRBS_USE": "lrbs_use",
    "W1_HALL": "w1_hall", "L_XCMGW": "l_xcmgw", "MMI_FISH": "mmi_fish", "NTL_UG_L": "ntl_ug_l",
    "PTL": "ptl_ug_l", "COND": "cond", "ANC": "anc_ueq", "DOC": "doc", "PH": "ph",
}
WEIGHT_COLUMNS = ("WGT_EXT_SP", "WGT_TP", "WGT_TP_EXTENT")
#: archive columns (apps/stream-curves/data/nrsa/values.parquet) carried as field indicators
ARCHIVE_METRICS = (
    "phab_XCMGW", "phab_XCDENMID", "phab_XPCMG", "phab_LRBS_use", "phab_LRBS_BW5", "phab_PCT_SAFN",
    "phab_XEMBED", "phab_LSUB_DMM", "phab_XFC_NAT", "phab_XFC_LWD", "phab_XFC_ALL", "phab_RP100",
    "phab_XINC_H", "phab_XBKF_H", "phab_XBKF_W", "phab_BFWD_RAT", "phab_XWD_RAT", "phab_XWIDTH",
    "phab_XBKA", "phab_XUN", "phab_PCT_DR", "phab_PCT_FAST", "phab_SINU", "phab_XSLOPE_use",
    "phab_W1_HALL", "phab_W1_HAG", "phab_RDIST1", "phab_C1WM100", "phab_CVWIDTH", "phab_SDWIDTH",
    "phab_LWDeqVolM100", "bent_EPT_NTAX", "bent_TOTLNTAX", "bent_TOLRPIND", "fish_ALIENPTAX", "fish_ALIENPIND",
    "fish_MIGRPTAX", "fish_NAT_MIGRPTAX", "fish_RHEOPTAX", "fish_NAT_TOTLNTAX", "chem_NTL", "chem_PTL",
    "chem_COND", "chem_TURB", "chem_TSS", "chem_DOC", "chem_ANC", "chem_CHLA",
)
CONUS_BBOX = [-125.5, 24.0, -66.5, 50.0]
CELL_DEG = 1.0
ATTAINS_PAD_DEG = 0.05


def nrsa_dir(root: DataRoot) -> Path:
    return root.analysis / "nrsa"


def targets_path(root: DataRoot) -> Path:
    return nrsa_dir(root) / "nrsa_targets.parquet"


def desktop_path(root: DataRoot) -> Path:
    return nrsa_dir(root) / "nrsa_desktop.parquet"


def desktop_meta_path(root: DataRoot) -> Path:
    return nrsa_dir(root) / "nrsa_desktop_meta.json"


def frame_path(root: DataRoot) -> Path:
    return nrsa_dir(root) / "nrsa_frame.parquet"


def screen_check_path(root: DataRoot) -> Path:
    return nrsa_dir(root) / "screen_check.csv"


# ------------------------------------------------------------ targets
def _number(value) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clean(value) -> str:
    return str(value if value is not None else "").strip()


def recode_class(column: str, value) -> Optional[str]:
    """The raw class text -> Good/Fair/Poor (None for not assessed)."""
    text = _clean(value)
    if not text or text.lower() in ("not assessed", "na", "nan", "none", "null"):
        return None
    if column == "OE_COND":
        token = text.replace(" ", "").upper()
        if token == "O/E>=0.9":
            return "Good"
        if token == "O/E<0.9":
            return "Fair"
        if token in ("O/E<0.8", "OE<0.5", "O/E<0.5"):
            return "Poor"
        return None
    if column == "RIPDIST_COND":
        return {"LOW": "Good", "MODERATE": "Fair", "HIGH": "Poor"}.get(text.upper())
    title = text.capitalize()
    return title if title in ("Good", "Fair", "Poor") else None


def _read_csv(path: Path):
    with open(path, encoding="latin-1", newline="") as handle:
        reader = csv.reader(handle)
        header = [h.strip().lstrip("﻿").replace("ï»¿", "").strip().upper()
                  for h in next(reader, [])]
        for row in reader:
            yield {header[i]: row[i] for i in range(min(len(header), len(row)))}


def load_targets(raw_dir: Optional[Path] = None) -> list[dict]:
    """One row per (cycle, site, visit) with the recoded targets; the
    2013-14 reference class rides on every visit of its site."""
    raw_dir = Path(raw_dir) if raw_dir is not None else NRSA_RAW
    rows: dict[tuple, dict] = {}
    for cycle, relatives in RAW_FILES.items():
        for relative in relatives:
            path = raw_dir / relative
            if not path.exists():
                continue
            for record in _read_csv(path):
                site = _clean(record.get("SITE_ID"))
                visit = _clean(record.get("VISIT_NO")) or "1"
                if not site:
                    continue
                out = rows.setdefault((cycle, site, visit), {"cycle": cycle, "site_id": site, "visit_no": visit})
                for column, name in CLASS_TARGETS.items():
                    if column in record:
                        value = recode_class(column, record[column])
                        if value is not None or f"t__{name}" not in out:
                            out[f"t__{name}"] = value
                for column, name in CONTINUOUS_TARGETS.items():
                    if column in record:
                        out[f"x__{name}"] = _number(record[column])
                for column in WEIGHT_COLUMNS:
                    if _number(record.get(column)) is not None:
                        out["weight"] = _number(record[column])
                if "STRAH_CAT" in record:
                    out["strah_cat"] = _clean(record["STRAH_CAT"]) or None
                if "AG_ECO9" in record:
                    out["ag_eco9_raw"] = _clean(record["AG_ECO9"]) or None
    site_file = raw_dir / SITE_FILE_1314
    rt_by_site: dict[str, str] = {}
    if site_file.exists():
        for record in _read_csv(site_file):
            site = _clean(record.get("SITE_ID"))
            rt = _clean(record.get("RT_NRSA"))
            if site and rt in ("R", "In", "Im"):
                rt_by_site.setdefault(site, rt)
    for (cycle, site, visit), out in rows.items():
        out["rt_nrsa"] = rt_by_site.get(site)
    return list(rows.values())


def _archive_visits():
    """cycle, site_id, visit_no -> station_key from the archive's visit table."""
    import pyarrow.parquet as pq
    table = pq.read_table(NRSA_DIR / "site_visits.parquet", columns=["cycle", "site_id", "visit_no", "station_key"])
    return {(str(c), str(s), str(v)): str(k) for c, s, v, k in zip(
        table.column("cycle").to_pylist(), table.column("site_id").to_pylist(),
        table.column("visit_no").to_pylist(), table.column("station_key").to_pylist())}


def build_targets(root: DataRoot, progress: Progress, *, raw_dir: Optional[Path] = None) -> Path:
    import pyarrow as pa
    rows = load_targets(raw_dir if raw_dir is not None else NRSA_RAW)
    visits = _archive_visits()
    for row in rows:
        row["station_key"] = visits.get((row["cycle"], row["site_id"], row["visit_no"]))
    # a station's reference class comes from any of its visits' site ids
    rt_by_station: dict[str, str] = {}
    for row in rows:
        if row.get("station_key") and row.get("rt_nrsa"):
            rt_by_station.setdefault(row["station_key"], row["rt_nrsa"])
    for row in rows:
        if row.get("station_key") and not row.get("rt_nrsa"):
            row["rt_nrsa"] = rt_by_station.get(row["station_key"])
    table = pa.Table.from_pylist(rows)
    path = common.write_parquet(table, targets_path(root))
    matched = sum(1 for r in rows if r.get("station_key"))
    progress.say(f"nrsa_targets.parquet: {len(rows):,} site visits, {matched:,} matched to archive stations, "
                 f"{len(rt_by_station):,} stations with a 2013-14 reference class")
    return path


# ------------------------------------------------------------ desktop records
def _stations():
    import pyarrow.parquet as pq
    table = pq.read_table(NRSA_DIR / "stations.parquet")
    rows = [r for r in table.to_pylist() if r.get("comid") not in (None, 0)]
    for r in rows:
        r["comid"] = int(r["comid"])
    return rows


def _identity_from_vaa(root: DataRoot, comids: list[int]) -> dict[int, dict]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    schema = pq.read_schema(root.vaa).names
    wanted = [c for c in ("comid", "reachcode", "gnis_name", "streamorde", "fcode", "totdasqkm", "lengthkm",
                          "slope", "hydroseq", "dnhydroseq", "levelpathi", "tocomid", "huc8", "huc4", "vpuid")
              if c in schema]
    table = pq.read_table(root.vaa, columns=wanted)
    table = table.filter(pc.is_in(table.column("comid"), value_set=pa.array(sorted(set(comids)), pa.int64())))
    vpu_of = load_huc4_vpu(root)
    out = {}
    for r in table.to_pylist():
        reach = str(r.get("reachcode") or "")
        huc8 = r.get("huc8") or (reach[:8] if reach else None)
        huc4 = r.get("huc4") or (reach[:4] if reach else None)
        slope = r.get("slope")
        out[int(r["comid"])] = {
            "comid": int(r["comid"]), "huc4": huc4, "huc8": huc8, "vpu": vpu_of.get(huc4) or r.get("vpuid"),
            "gnis_name": r.get("gnis_name"), "streamorde": r.get("streamorde"), "fcode": r.get("fcode"),
            "totdasqkm": r.get("totdasqkm"), "lengthkm": r.get("lengthkm"),
            "slope": (slope if slope is not None and float(slope) >= 0 else None),
            "hydroseq": r.get("hydroseq"), "dnhydroseq": r.get("dnhydroseq"),
            "levelpathi": r.get("levelpathi"), "tocomid": r.get("tocomid"),
        }
    return out


def _strata_rows(root: DataRoot, comids: list[int]) -> dict[int, dict]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    if not strata_path(root).exists():
        return {}
    table = pq.read_table(strata_path(root), columns=["comid", "huc12", "lat", "lon", "sinuosity_flowline",
                                                      "l3", "nars9", "physio"])
    table = table.filter(pc.is_in(table.column("comid"), value_set=pa.array(sorted(set(comids)), pa.int64())))
    return {int(r["comid"]): r for r in table.to_pylist()}


class _WqpNational:
    """The ten-year national WQP results as the station index per parameter."""

    def __init__(self, root: DataRoot, progress: Progress):
        self.index: dict[str, object] = {}
        path = wqp_local.national_path(root)
        if not path.exists():
            progress.say("nrsa: no national WQP parquet, nutrient inputs will be absent")
            return
        start = wqp_local.start_iso(config.NRSA_AS_OF)
        progress.say("nrsa: normalizing the national WQP results ...")
        rows = wqp_local.normalize(wqp_local.select_rows(path, CONUS_BBOX, start))
        for param in wqp_local.PARAMS:
            self.index[param] = joins.wqp_index_from_rows([r for r in rows if r.get("param") == param])
        progress.say(f"nrsa: WQP index ready ({len(rows):,} normalized results)")

    def summary(self, param: str, lat: float, lon: float, x: float, y: float):
        index = self.index.get(param)
        return joins.wqp_summary(index, param, lat, lon, x, y) if index is not None else None


class _AttainsCells:
    """ATTAINS rows from the national parquet, read once per 1-degree cell."""

    def __init__(self, root: DataRoot):
        self.root = root
        self.available = local_gdb.attains_path(root).exists()
        self.cache: dict[tuple, object] = {}

    def lookup(self, lat: float, lon: float) -> tuple[dict, dict]:
        if not self.available:
            return {}, {}
        key = (math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG))
        if key not in self.cache:
            south, west = key[0] * CELL_DEG, key[1] * CELL_DEG
            rows = local_gdb.attains_for(self.root, [west - ATTAINS_PAD_DEG, south - ATTAINS_PAD_DEG,
                                                     west + CELL_DEG + ATTAINS_PAD_DEG, south + CELL_DEG + ATTAINS_PAD_DEG])
            self.cache[key] = joins.attains_index_from_rows(rows or [])
            if len(self.cache) > 6:
                oldest = next(iter(self.cache))
                if oldest != key:
                    del self.cache[oldest]
        return joins.attains_lookup(self.cache[key], lat, lon)


def synthetic_record(comid: int, identity: dict, strata_row: dict, streamcat: dict, *,
                     attains: tuple[dict, dict], wqp_tn, wqp_tp, nid_dams, nas_taxa) -> dict:
    """An evidence record for a reach outside the scored extent, from the
    national caches (no NRSA evidence, no cross-sections, bankfull computed)."""
    from easi.national import SCHEMA_VERSION
    exact, nearby = attains
    return {
        **identity,
        "huc12": strata_row.get("huc12"), "sinuosity": strata_row.get("sinuosity_flowline"),
        "lat": strata_row.get("lat"), "lon": strata_row.get("lon"),
        "streamcat": streamcat or {}, "nrsa": None, "bankfull": None,
        "attains_exact": exact or {}, "attains_nearby": nearby or {},
        "wqp_tn": wqp_tn, "wqp_tp": wqp_tp, "nid_dams": nid_dams,
        "nas_taxa": nas_taxa, "nas_scope": "huc12" if nas_taxa is not None else None,
        "geomorph": None, "schema_version": SCHEMA_VERSION,
    }


def _evidence_records(root: DataRoot, comid_huc8: dict[int, str]) -> dict[int, dict]:
    """Stored evidence records for the in-extent stations, one HUC8 file read per HUC8."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from easi.national import records
    by_huc8: dict[str, list[int]] = defaultdict(list)
    for comid, huc8 in comid_huc8.items():
        by_huc8[huc8].append(comid)
    out: dict[int, dict] = {}
    for huc8, comids in by_huc8.items():
        path = root.huc8_file(huc8, "evidence")
        if not path.exists():
            continue
        table = pq.read_table(path)
        table = table.filter(pc.is_in(table.column("comid"), value_set=pa.array(sorted(comids), pa.int64())))
        for row in table.to_pylist():
            out[int(row["comid"])] = records.from_row(row)
    return out


def _scored_huc8(root: DataRoot) -> dict[int, str]:
    import pyarrow.parquet as pq
    if not values_path(root).exists():
        return {}
    table = pq.read_table(values_path(root), columns=["comid", "huc8"])
    return {int(c): str(h) for c, h in zip(table.column("comid").to_pylist(), table.column("huc8").to_pylist())}


def build_desktop(root: DataRoot, progress: Progress, control: Control) -> Path:
    """Score every station with a COMID and write ``nrsa_desktop.parquet``."""
    import pyarrow as pa
    from easi.national import client
    stations = _stations()
    comids = sorted({s["comid"] for s in stations})
    scored = _scored_huc8(root)
    in_set = {c: scored[c] for c in comids if c in scored}
    progress.begin("analysis", "nrsa", total=len(stations),
                   message=f"nrsa: {len(stations):,} stations with a COMID, {len(in_set):,} inside the scored extent")
    evidence = _evidence_records(root, in_set)
    outside = [c for c in comids if c not in evidence]
    identity = _identity_from_vaa(root, outside) if outside else {}
    strata_rows = _strata_rows(root, outside) if outside else {}
    streamcat = streamcat_national.cached_rows(root, outside) if outside else {}
    wqp = _WqpNational(root, progress) if outside else None
    attains = _AttainsCells(root)
    nid_index = joins._nid_index(root)
    taxa = nas_national.cached_taxa(root) or {}
    rows = []
    for i, station in enumerate(stations):
        if i % 50 == 0:
            control.check()
            progress.tick(done=i)
        comid = station["comid"]
        record = evidence.get(comid)
        source = "evidence"
        if record is None:
            ident = identity.get(comid)
            strata_row = strata_rows.get(comid) or {}
            if ident is None or strata_row.get("lat") is None:
                rows.append({"station_key": station["station_key"], "comid": comid,
                             "comid_source": station.get("comid_source"), "in_set": False,
                             "desktop_source": "missing", "protocol": station.get("protocol"),
                             "station_state": station.get("state"), "station_l3": station.get("us_l3code"),
                             "station_nars9": station.get("ag_eco9")})
                continue
            lat, lon = float(strata_row["lat"]), float(strata_row["lon"])
            (x, y), = joins._project([lat], [lon])
            record = synthetic_record(
                comid, ident, strata_row, streamcat.get(comid) or {},
                attains=attains.lookup(lat, lon),
                wqp_tn=wqp.summary("tn", lat, lon, x, y) if wqp else None,
                wqp_tp=wqp.summary("tp", lat, lon, x, y) if wqp else None,
                nid_dams=joins.nid_lookup(nid_index, lat, lon, x, y),
                nas_taxa=(taxa.get(str(strata_row.get("huc12")), {}).get("taxa")
                          if strata_row.get("huc12") else None))
            source = "synthetic"
        report = client.score_record(record, cross_section=False)
        row = {"station_key": station["station_key"], "comid": comid, "comid_source": station.get("comid_source"),
               "in_set": source == "evidence", "desktop_source": source, "protocol": station.get("protocol"),
               "station_state": station.get("state"), "station_l3": station.get("us_l3code"),
               "station_nars9": station.get("ag_eco9")}
        row.update({key: record.get(key) for key in IDENTITY if key in record})
        row["tier"] = config.XS_TIER if isinstance(record.get("geomorph"), dict) and record.get("geomorph") else config.BASE_TIER
        row.update(flatten_trace(report))
        row.update(xs_extras(record.get("geomorph")))
        row.update(record_extras(record))
        for key, value in (record.get("streamcat") or {}).items():
            number = _number(value)
            if number is not None:
                row[str(key).lower()] = number
        rows.append(row)
    progress.tick(done=len(stations))
    # the caches first: mines_ws, the catrp100 covers and wetland_retention derive from the sc__ candidates
    table = join_caches(root, concrete(pa.Table.from_pylist(rows)))
    derived = derived_landscape(table)
    for name, values in derived.items():
        table = table.append_column(name, pa.array(values, pa.float64(), mask=~np.isfinite(values)))
    path = common.write_parquet(table, desktop_path(root))
    desktop_meta_path(root).write_text(json.dumps({"inputs": inputs(root), "stations": len(rows)}, indent=1),
                                       encoding="utf-8")
    n_synth = sum(1 for r in rows if r.get("desktop_source") == "synthetic")
    n_missing = sum(1 for r in rows if r.get("desktop_source") == "missing")
    progress.say(f"nrsa_desktop.parquet: {len(rows):,} stations ({len(in_set):,} from stored evidence, "
                 f"{n_synth:,} synthetic, {n_missing:,} without identity)")
    return path


# ------------------------------------------------------------ the frame
def build_frame(root: DataRoot, progress: Progress) -> Path:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    targets = concrete(pq.read_table(targets_path(root)))
    targets = targets.filter(pc.equal(targets.column("visit_no"), "1"))
    targets = targets.filter(pc.is_valid(targets.column("station_key")))
    archive_schema = pq.read_schema(NRSA_DIR / "values.parquet").names
    metrics = [c for c in ARCHIVE_METRICS if c in archive_schema]
    land = [c for c in archive_schema if c.startswith("land_")]
    archive = pq.read_table(NRSA_DIR / "values.parquet", columns=["station_key", "cycle", "visit_no", *metrics, *land])
    archive = archive.filter(pc.equal(pc.cast(archive.column("visit_no"), pa.string()), "1")).drop_columns(["visit_no"])
    archive = archive.rename_columns(["station_key", "cycle"] + [f"a__{c}" for c in metrics] + [f"{c}" for c in land])
    frame = concrete(targets).join(concrete(archive), keys=["station_key", "cycle"], join_type="left outer")
    desktop = concrete(pq.read_table(desktop_path(root)))
    frame = concrete(frame).join(desktop, keys="station_key", join_type="left outer")
    cycle_rank = pa.array([CYCLES[::-1].index(c) if c in CYCLES else 9 for c in frame.column("cycle").to_pylist()], pa.int8())
    frame = frame.append_column("cycle_rank", cycle_rank).sort_by([("station_key", "ascending"), ("cycle_rank", "ascending")])
    path = common.write_parquet(frame, frame_path(root))
    progress.say(f"nrsa_frame.parquet: {frame.num_rows:,} station visits, {len(metrics)} archive metrics, "
                 f"{len(land)} landscape predictors")
    return path


def screen_check(root: DataRoot, progress: Progress) -> Path:
    """Pass rates of EPA's 2013-14 reference (R), intermediate (In) and most
    disturbed (Im) stations under each desktop screen, nationally and per
    NARS-9, plus the composite-pressure AUC of R against Im."""
    import pyarrow.parquet as pq
    desktop = pq.read_table(desktop_path(root))
    targets = pq.read_table(targets_path(root), columns=["station_key", "rt_nrsa"])
    rt_by_station = {k: v for k, v in zip(targets.column("station_key").to_pylist(), targets.column("rt_nrsa").to_pylist()) if k and v}
    keys = desktop.column("station_key").to_pylist()
    rt = np.asarray([rt_by_station.get(k) for k in keys], dtype=object)
    region = np.asarray(desktop.column("station_nars9").to_pylist(), dtype=object)
    columns = {name: desktop.column(name).to_pandas().to_numpy() for name in desktop.column_names
               if name in screens.PRESSURE_VARIABLES or any(name in rules for rules in screens.SCREENS.values())
               or name in screens.FRAME_RULES}
    pressure, used = screens.composite_pressure(columns)
    rows = []
    for screen_name, rules in screens.SCREENS.items():
        mask, skipped = screens.evaluate(columns, {**rules, **screens.FRAME_RULES})
        for group in ["US"] + sorted({r for r in region if r}):
            sel = np.ones(len(keys), dtype=bool) if group == "US" else (region == group)
            row = {"screen": screen_name, "region": group, "skipped_rules": ";".join(skipped),
                   "pressure_variables": ";".join(used)}
            for cls in ("R", "In", "Im"):
                members = sel & (rt == cls)
                row[f"n_{cls}"] = int(members.sum())
                row[f"pass_{cls}"] = float(mask[members].mean()) if members.any() else None
            positives = sel & (rt == "R")
            negatives = sel & (rt == "Im")
            both = positives | negatives
            row["auc_pressure_R_vs_Im"] = stats.auc(-pressure[both], positives[both]) if both.any() else None
            rows.append(row)
    path = screen_check_path(root)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    national = next(r for r in rows if r["screen"] == "strict" and r["region"] == "US")
    progress.say(f"screen_check.csv: strict screen passes {national['pass_R']!r} of reference and "
                 f"{national['pass_Im']!r} of most-disturbed stations nationally")
    return path


def inputs(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = [(p.name, p.stat().st_size, int(p.stat().st_mtime)) if p.exists() else None
              for p in (values_path(root), strata_path(root), NRSA_DIR / "stations.parquet", NRSA_DIR / "values.parquet")]
    return digest("nrsa", ANALYSIS_VERSION, stamps, sorted(CLASS_TARGETS), 1)


def desktop_fresh(root: DataRoot) -> bool:
    """True when ``nrsa_desktop.parquet`` was built from the current inputs:
    scoring the stations is the slow part of the step, and a failure further
    down must not rebuild it."""
    try:
        meta = json.loads(desktop_meta_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return desktop_path(root).exists() and meta.get("inputs") == inputs(root)


def run(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    nrsa_dir(root).mkdir(parents=True, exist_ok=True)
    build_targets(root, progress)
    if desktop_fresh(root):
        progress.say("nrsa_desktop.parquet: reused, inputs unchanged")
    else:
        build_desktop(root, progress, control)
    path = build_frame(root, progress)
    screen_check(root, progress)
    return path
