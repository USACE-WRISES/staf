"""Steps ``values`` and ``landscape``: the raw metric inputs behind every
score, and the same landscape quantities for every reach in the country.

``analysis/values.parquet`` (one row per scored reach): every evidence record
is re-scored exactly as the score stage scores it (``score.record_for`` +
``easi.national.client.score_record``), and the scoring trace of each of the
20 metrics is flattened: the rating, index, function score, tier, method,
fallback flag, completeness, governing input, combined value, every input
value (numeric ``v__<function>__<input>``, text ``vcat__``), per-input ratings
on worst-of and best-of metrics (``r__``), and the context block (``ctx__``).
The ``value_*`` text of the scores file cannot replace this: it names only the
governing input of a worst-of metric and prints the bank-height cap as
"at least 2.00". Cross-section extras come from the nine transect scalars.
The part per HUC8 also carries the scores file's own index and ECI columns so
scheme A can be asserted byte for byte (``parity_A.json``).

``analysis/landscape.parquet`` (one row per NHDPlus V2 reach): the strata,
the cached StreamCat columns, the candidate variables, the EROM ratios and
the derived quantities the reference screens and curves use, computed with
the adapters' arithmetic and checked against the values table.
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Optional

import numpy as np

from .. import config, pipeline
from ..paths import DataRoot
from ..state import CancelRequested, Control, Ledger, PauseRequested, Progress, digest
from ..stages import common
from ..stages.score import _function_key
from ..units import list_chunks
from . import ANALYSIS_VERSION
from .candidates import attains_attributes_path, candidates_path, erom_path
from .strata import strata_path

XS_EXTRA_COLUMNS = ("xs_n", "dem_res_m", "bhr_median", "bhr_min", "bhr_max", "bhr_p25", "bhr_p75",
                    "bhr_share_capped", "bhr_share_ge_1p5", "bhr_share_ge_1p3", "bhr_mean_uncapped",
                    "er_median", "er_min", "er_max", "er_p25", "er_p75", "er_edge_limited_share",
                    "bankfull_width_cv", "bankfull_depth_cv")
IDENTITY = ("comid", "huc4", "huc8", "huc12", "vpu", "streamorde", "fcode", "totdasqkm", "lengthkm",
            "slope", "sinuosity", "lat", "lon", "hydroseq", "dnhydroseq", "tocomid",
            "nars9", "l3_code", "l3_name", "physio_division")
#: strata columns joined onto the values table (the derived keys win for l3, nars9, physio)
STRATA_JOIN = ("state", "l2", "l1", "slope_class", "slope_class_er", "da_class", "fcode_class",
               "wadeable", "sinuosity_flowline", "huc12_imputed", "region_imputed")
AU_JOIN = ("ecological_use", "overallstatus", "ircategory", "hastmdl", "has4bplan", "hasalternativeplan",
           "aquatic_life_strict", "aquatic_life_cause_screen", "causes", "state", "reportingcycle")
#: inputs the landscape table recomputes; (values column, landscape column)
LANDSCAPE_PARITY = (
    # (harvested input, recomputed quantity, absolute allowance, rows compared)
    # The corridor cover adapters round every class and the total to one
    # decimal (watershed.riparian_woody_breakdown, riparian_veg_breakdown), so
    # those pairs agree only to a few tenths of a percent; the integrity pairs
    # are compared only on the rows whose function scored the fallback (the
    # observed NRSA branch carries a 0 to 100 percentage instead).
    ("v__catchment_hydrology__impervious", "pctimp2019ws", 0.0, None),
    ("v__catchment_hydrology__agriculture", "agriculture_ws", 0.0, None),
    ("c__surface_water_storage", "wetland_ws", 0.0, None),
    ("v__reach_inflow__roadDensity", "rddensws", 0.0, None),
    ("c__streamflow_regime", "dor", 0.0, None),
    ("v__sediment_continuity__kFactor", "kffactws", 0.0, None),
    ("v__habitat_provision__woodyRiparian", "woody_wsrp100", 0.3, None),
    ("v__light_thermal_regime__woodyRiparian", "woody_wsrp100", 0.3, None),
    ("c__carbon_processing", "natural_wsrp100", 0.3, None),
    ("c__low_flow_baseflow_dynamics", "erom__q_cv_monthly", 0.000001,
     ("method_low_flow_baseflow_dynamics", "erom-flow-variability")),
    ("c__bed_composition_bedform_dynamics", "agriculture_ws", 0.01,
     ("method_bed_composition_bedform_dynamics", "watershed-agriculture-share")),
    # Keep legacy-only comparisons while the legacy criteria remain available.
    ("c__low_flow_baseflow_dynamics", "hyd_min", 0.0,
     ("method_low_flow_baseflow_dynamics", "streamcat-hyd-integrity")),
    ("c__bed_composition_bedform_dynamics", "sed_min", 0.0,
     ("method_bed_composition_bedform_dynamics", "streamcat-sed-integrity")),
)


def values_path(root: DataRoot) -> Path:
    return root.analysis / "values.parquet"


def values_parts_dir(root: DataRoot) -> Path:
    return root.analysis / "values_parts"


def values_meta_path(root: DataRoot) -> Path:
    return root.analysis / "values_meta.json"


def parity_a_path(root: DataRoot) -> Path:
    return root.analysis / "parity_A.json"


def landscape_path(root: DataRoot) -> Path:
    return root.analysis / "landscape.parquet"


def landscape_parity_path(root: DataRoot) -> Path:
    return root.analysis / "landscape_parity.json"


# ------------------------------------------------------------ pure helpers
def _number(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _text(value) -> Optional[str]:
    return None if value is None else str(value)


def flatten_trace(report: dict) -> dict:
    """The flat row of one report: the rollup numbers and, per function, the
    row fields and the scoring trace (see the module docstring)."""
    out = {
        "eci_raw": _number(report.get("ecosystemConditionIndexRaw")),
        "phys_raw": _number((report.get("subIndicesRaw") or {}).get("physical")),
        "chem_raw": _number((report.get("subIndicesRaw") or {}).get("chemical")),
        "bio_raw": _number((report.get("subIndicesRaw") or {}).get("biological")),
        "n_rated": report.get("computedCount"),
        "provisional": bool(report.get("provisionalCoverage")),
    }
    for row in report.get("metricRows") or []:
        fk = _function_key(row["functionId"])
        trace = row.get("scoring") or {}
        out[f"rating_{fk}"] = _text(row.get("rating"))
        out[f"index_{fk}"] = _number(row.get("index"))
        out[f"fs_{fk}"] = row.get("functionScore")
        out[f"status_{fk}"] = _text(row.get("status"))
        out[f"tier_{fk}"] = _text(row.get("sourceTier"))
        out[f"method_{fk}"] = _text(trace.get("methodKey"))
        out[f"kind_{fk}"] = _text(trace.get("methodKind"))
        out[f"fallback_{fk}"] = bool(trace.get("usedFallback"))
        out[f"complete_{fk}"] = _text(trace.get("completeness"))
        out[f"governing_{fk}"] = _text(trace.get("governingInput"))
        combined = trace.get("combinedValue")
        number = _number(combined)
        out[f"c__{fk}"] = number
        out[f"ccat__{fk}"] = _text(combined) if number is None and combined is not None else None
        for inp in trace.get("inputs") or []:
            key = inp.get("key")
            if not key:
                continue
            value = inp.get("value")
            number = _number(value)
            out[f"v__{fk}__{key}"] = number
            if number is None and value is not None:
                out[f"vcat__{fk}__{key}"] = _text(value)
            if inp.get("rating") is not None:
                out[f"r__{fk}__{key}"] = _text(inp.get("rating"))
        for ckey, cvalue in (trace.get("context") or {}).items():
            if isinstance(cvalue, dict):
                for k2, v2 in cvalue.items():
                    out[f"ctx__{fk}__{ckey}_{k2}"] = _text(v2)
            else:
                out[f"ctx__{fk}__{ckey}"] = _text(cvalue)
    return out


def _stats(values: list[float]) -> dict:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return {"median": None, "min": None, "max": None, "p25": None, "p75": None}
    return {"median": float(np.median(arr)), "min": float(arr.min()), "max": float(arr.max()),
            "p25": float(np.quantile(arr, 0.25)), "p75": float(np.quantile(arr, 0.75))}


def _cv(values: list[float]) -> Optional[float]:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size < 2 or not np.isfinite(arr).all() or arr.mean() <= 0:
        return None
    return float(arr.std(ddof=0) / arr.mean())


def xs_extras(geomorph) -> dict:
    """The uncensored summaries of the nine transect scalars (share with BHR
    at or above 1.5, capped share, quartiles, edge-limited share, width and
    depth variability), all None for a Tier 1 or empty geomorph block."""
    out: dict = {key: None for key in XS_EXTRA_COLUMNS}
    if not isinstance(geomorph, dict) or not geomorph:
        return out
    scalars = [s for s in (geomorph.get("candidate_scalars") or []) if isinstance(s, dict)]
    out["xs_n"] = geomorph.get("n_transects") or (len(scalars) or None)
    out["dem_res_m"] = _number(geomorph.get("dem_resolution_m"))
    bhr = [_number(s.get("bank_height_ratio")) for s in scalars]
    er = [_number(s.get("entrenchment_ratio")) for s in scalars]
    capped = [bool(s.get("low_bank_capped")) for s in scalars]
    edge = [bool(s.get("edge_limited")) for s in scalars]
    reach = geomorph.get("reach") or {}
    bhr_stats = _stats(bhr)
    er_stats = _stats(er)
    out["bhr_median"] = _number((reach.get("bank_height_ratio") or {}).get("median")) if reach else bhr_stats["median"]
    if out["bhr_median"] is None:
        out["bhr_median"] = _number(geomorph.get("bank_height_ratio"))
    out["er_median"] = _number((reach.get("entrenchment_ratio") or {}).get("median")) if reach else er_stats["median"]
    if out["er_median"] is None:
        out["er_median"] = _number(geomorph.get("entrenchment_ratio"))
    for name, stats in (("bhr", bhr_stats), ("er", er_stats)):
        for key in ("min", "max", "p25", "p75"):
            out[f"{name}_{key}"] = stats[key]
    have_bhr = [v for v in bhr if v is not None]
    if have_bhr:
        out["bhr_share_ge_1p5"] = float(np.mean([v >= 1.5 for v in have_bhr]))
        out["bhr_share_ge_1p3"] = float(np.mean([v >= 1.3 for v in have_bhr]))
        uncapped = [v for v, c in zip(bhr, capped) if v is not None and not c]
        out["bhr_mean_uncapped"] = float(np.mean(uncapped)) if uncapped else None
    if scalars:
        out["bhr_share_capped"] = float(np.mean(capped))
        out["er_edge_limited_share"] = float(np.mean(edge))
    out["bankfull_width_cv"] = _cv([_number(s.get("bankfull_width_m")) for s in scalars])
    out["bankfull_depth_cv"] = _cv([_number(s.get("bankfull_depth_m")) for s in scalars])
    return out


def record_extras(record: dict) -> dict:
    """Evidence facts the trace does not carry: the assessment units, the
    dam count and distance, the NAS count, the WQP station counts, the NRSA tier."""
    exact = record.get("attains_exact") or {}
    nearby = record.get("attains_nearby") or {}
    dams = record.get("nid_dams")
    taxa = record.get("nas_taxa")
    out = {
        "attains_exact_au": _text(exact.get("assessment_unit")) if isinstance(exact, dict) else None,
        "attains_exact_cat": _text(exact.get("ircategory")) if isinstance(exact, dict) else None,
        "attains_nearby_au": _text(nearby.get("assessment_unit")) if isinstance(nearby, dict) else None,
        "attains_nearby_cat": _text(nearby.get("ircategory")) if isinstance(nearby, dict) else None,
        "attains_nearby_distance_m": _number(nearby.get("distance_m")) if isinstance(nearby, dict) else None,
        "nid_dam_count": len(dams) if isinstance(dams, list) else None,
        "nid_nearest_m": (min((_number(d.get("distance_m")) or math.inf) for d in dams)
                          if isinstance(dams, list) and dams else None),
        "nas_taxa_count": len(taxa) if isinstance(taxa, list) else None,
        "nas_scope": _text(record.get("nas_scope")),
        "nrsa_present": isinstance(record.get("nrsa"), dict) and bool(record.get("nrsa")),
    }
    if out["nid_nearest_m"] is not None and not math.isfinite(out["nid_nearest_m"]):
        out["nid_nearest_m"] = None
    for param in ("tn", "tp"):
        summary = record.get(f"wqp_{param}")
        summary = summary if isinstance(summary, dict) else {}
        out[f"wqp_{param}_value"] = _number(summary.get("value"))
        out[f"wqp_{param}_stations"] = summary.get("station_count")
        out[f"wqp_{param}_observations"] = summary.get("observation_count")
    au = out["attains_exact_au"] or out["attains_nearby_au"]
    out["attains_au"] = au
    out["attains_match"] = "exact" if out["attains_exact_au"] else ("nearby" if out["attains_nearby_au"] else None)
    return out


# ------------------------------------------------------------ the harvest
def chunk_of_huc8(root: DataRoot) -> dict[str, str]:
    """HUC8 -> a chunk that carries its raw StreamCat rows (a chunk whose
    StreamCat stage has not run yet, such as a queued state, is skipped; a
    state chunk wins over a HUC8 chunk that is a subset of it)."""
    out: dict[str, str] = {}
    chunks = [c for c in list_chunks(root) if root.chunk_raw(c.id, "streamcat").exists()]
    for chunk in sorted(chunks, key=lambda c: (c.kind != "state", -len(c.huc8s), c.id)):
        for huc8 in chunk.huc8s:
            out.setdefault(huc8, chunk.id)
    return out


def target_states(root: DataRoot) -> list[str]:
    """The states whose whole chunk is in the values table (every HUC8 of the
    state chunk has harvested reaches). State chunks include border HUC8s, so
    the table also holds spill reaches of neighbouring states; the per-state
    tables report the target states and the national rows pool everything."""
    import pyarrow.parquet as pq
    if not values_path(root).exists():
        return []
    schema = pq.read_schema(values_path(root)).names
    if "huc8" not in schema:
        return []
    present = set(pq.read_table(values_path(root), columns=["huc8"]).column("huc8").unique().to_pylist())
    out: set[str] = set()
    for chunk in list_chunks(root):
        if chunk.kind == "state" and chunk.huc8s and all(huc8 in present for huc8 in chunk.huc8s):
            out.update(chunk.states or [])
    return sorted(out)


def harvest_huc8(root_path: str, huc8: str, chunk_id: str) -> str:
    """Re-score one HUC8's records and write ``values_parts/<huc8>.parquet``;
    returns the part path. Runs in a pool child (string arguments only)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from easi.national import client
    from ..stages import score as score_stage
    root = DataRoot(Path(root_path))
    derived = pq.read_table(root.huc8_file(huc8, "derived")).to_pylist()
    erom = score_stage.erom_rows(root, (row["comid"] for row in derived))
    joins = {int(r["comid"]): r for r in pq.read_table(root.huc8_file(huc8, "joins")).to_pylist()}
    comids = pa.array([int(d["comid"]) for d in derived], pa.int64())
    sc_table = pq.read_table(root.chunk_raw(chunk_id, "streamcat"))
    sc_table = sc_table.filter(pc.is_in(sc_table.column("comid"), value_set=comids))
    sc_rows = {int(r["comid"]): {k: v for k, v in r.items() if k != "comid" and v is not None}
               for r in sc_table.to_pylist()}
    xs_path = root.huc8_file(huc8, "xsections")
    xsections = ({int(r["comid"]): r for r in pq.read_table(xs_path).to_pylist()} if xs_path.exists() else {})
    scores_path = root.huc8_file(huc8, "scores")
    scores: dict[int, dict] = {}
    if scores_path.exists():
        table = pq.read_table(scores_path)
        keep = [c for c in table.column_names if c == "comid" or c.startswith(("index_", "fs_")) or c == "eci_raw"]
        scores = {int(r["comid"]): r for r in table.select(keep).to_pylist()}
    rows = []
    for d in derived:
        comid = int(d["comid"])
        j = joins.get(comid) or {}
        xrow = xsections.get(comid)
        geomorph = score_stage.geomorph_for(xrow)
        record = score_stage.record_for(d, j, geomorph, sc_rows.get(comid) or {}, erom.get(comid))
        report = client.score_record(record, cross_section=False)
        row = {key: d.get(key) for key in IDENTITY}
        row["tier"] = config.XS_TIER if geomorph is not None else config.BASE_TIER
        row["xs_status"] = (xrow or {}).get("status") or "none"
        row.update(flatten_trace(report))
        row.update(xs_extras(geomorph))
        row.update(record_extras(record))
        baked = scores.get(comid) or {}
        for key, value in baked.items():
            if key != "comid":
                row[f"scores_{key}"] = value
        rows.append(row)
    table = pa.Table.from_pylist(rows)
    out = values_parts_dir(root) / f"{huc8}.parquet"
    common.write_parquet(table, out)
    return str(out)


def part_key(root: DataRoot, huc8: str) -> str:
    """The HUC8 part depends on its scores, current method and raw EROM cache."""
    path = root.huc8_file(huc8, "scores")
    stat = path.stat() if path.exists() else None
    from easi.national import method_version
    from ..stages.score import erom_stamp
    return f"{huc8}@" + digest(stat.st_size if stat else 0, stat.st_mtime_ns if stat else 0,
                              method_version(), erom_stamp(root))


def inputs_values(root: DataRoot, options: Optional[dict] = None) -> str:
    from easi.national import method_version
    huc8s = sorted(p.parent.name for p in root.huc8.glob("*/scores.parquet")) if root.huc8.exists() else []
    return digest("values", ANALYSIS_VERSION, method_version(), [part_key(root, h) for h in huc8s], 3)


def run_values(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    """Harvest every scored HUC8 in a process pool (resumable per part), then
    assemble ``values.parquet`` with the strata, candidate, EROM and ATTAINS
    columns, and assert scheme A parity."""
    options = dict(options or {})
    huc8s = sorted(p.parent.name for p in root.huc8.glob("*/scores.parquet"))
    if not huc8s:
        raise RuntimeError("no scored HUC8 (huc8/*/scores.parquet) to harvest")
    chunk_map = chunk_of_huc8(root)
    missing = [h for h in huc8s if h not in chunk_map]
    if missing:
        raise RuntimeError(f"{len(missing)} scored HUC8s belong to no chunk (first {missing[:5]})")
    parts = values_parts_dir(root)
    parts.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(root, "analysis-values")
    # a part is reused only while its HUC8's scores file is the one it was harvested from
    stamp_of = {h: part_key(root, h) for h in huc8s}
    pending = [h for h in huc8s if stamp_of[h] not in ledger or not (parts / f"{h}.parquet").exists()]
    order = [h for h in pipeline.largest_first(root, pending)] if pending else []
    workers = max(1, int(options.get("workers") or config.ANALYSIS_WORKERS))
    total = len(huc8s)
    done = total - len(order)
    progress.begin("analysis", "values", total=total,
                   message=f"values: {total} HUC8s, {len(order)} to harvest on {workers} processes")
    progress.tick(done=done)
    root_path = str(root.root)
    if order:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            queue, running = list(order), {}
            try:
                while queue or running:
                    while queue and len(running) < workers:
                        control.check()
                        huc8 = queue.pop(0)
                        running[pool.submit(harvest_huc8, root_path, huc8, chunk_map[huc8])] = huc8
                    finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                    for future in finished:
                        huc8 = running.pop(future)
                        future.result()
                        ledger.add(stamp_of[huc8], huc8=huc8)
                        done += 1
                        progress.tick(done=done, message=f"values {huc8} harvested")
            except (PauseRequested, CancelRequested):
                pool.shutdown(wait=True, cancel_futures=True)
                raise
    table = assemble_values(root, huc8s, progress)
    path = common.write_parquet(table, values_path(root))
    parity = parity_a(table)
    parity_a_path(root).write_text(json.dumps(parity, indent=1), encoding="utf-8")
    from easi.national import method_version
    values_meta_path(root).write_text(json.dumps({
        "method_version": method_version(), "analysis_version": ANALYSIS_VERSION,
        "n_reaches": table.num_rows, "huc8s": len(huc8s), "columns": table.column_names,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=1), encoding="utf-8")
    progress.say(f"values.parquet: {table.num_rows:,} reaches, {len(table.column_names)} columns; "
                 f"scheme A parity: {parity['mismatched_reaches']:,} mismatched reaches of {parity['compared']:,}")
    if parity["mismatched_reaches"]:
        raise RuntimeError(f"scheme A parity failed: {parity['mismatched_reaches']:,} reaches differ from scores.parquet "
                           f"(functions {[k for k, v in parity['functions'].items() if v]})")
    if landscape_path(root).exists():
        import pyarrow.parquet as pq
        schema = pq.read_schema(landscape_path(root)).names
        needed = list(dict.fromkeys(["comid"] + [p[1] for p in LANDSCAPE_PARITY if p[1] in schema]))
        report = landscape_parity(root, pq.read_table(landscape_path(root), columns=needed))
        landscape_parity_path(root).write_text(json.dumps(report, indent=1), encoding="utf-8")
        progress.say(f"landscape parity: {report.get('pairs_checked', 0)} pairs, worst absolute difference "
                     f"{report.get('worst_max_abs_diff')}, failed {report.get('failed')}")
    return path


def _prefixed(table, prefix: str, keep: Optional[tuple] = None):
    """A table with every non-key column renamed ``<prefix><name>`` (and
    restricted to ``keep`` when given)."""
    names = [c for c in table.column_names if c != "comid" and (keep is None or c in keep)]
    sub = table.select(["comid"] + names)
    return sub.rename_columns(["comid"] + [f"{prefix}{c}" for c in names])


TEXT_PREFIXES = ("ccat__", "vcat__", "r__", "ctx__", "rating_", "status_", "tier_", "method_", "kind_",
                 "complete_", "governing_", "attains_", "nas_scope", "xs_status", "huc", "vpu", "gnis_name",
                 "nars9", "l3_", "l2", "l1", "physio", "state", "station_", "desktop_source", "protocol",
                 "comid_source", "au__", "t__", "cycle", "site_id", "visit_no", "rt_nrsa", "strah_cat", "weight_")


def concrete(table):
    """A copy of ``table`` with every null-typed column (a field that was None
    on every row) cast to a concrete type: text for the trace's categorical
    columns, float64 otherwise; and every ``large_string`` column cast to
    ``string``. pyarrow joins refuse null-typed columns and refuse a key that
    is ``string`` on one side and ``large_string`` on the other (pandas-written
    parquet files carry the latter), and a concatenation would otherwise
    promote null columns at random."""
    import pyarrow as pa
    columns = []
    changed = False
    for field in table.schema:
        column = table.column(field.name)
        if pa.types.is_null(field.type):
            target = pa.string() if field.name.startswith(TEXT_PREFIXES) else pa.float64()
            column = pa.nulls(table.num_rows, target)
            changed = True
        elif pa.types.is_large_string(field.type):
            column = column.cast(pa.string())
            changed = True
        columns.append(column)
    return pa.table(dict(zip(table.column_names, columns))) if changed else table


def join_caches(root: DataRoot, table, *, strata_columns: tuple = STRATA_JOIN):
    """Join the strata, candidate (``sc__``), EROM (``erom__``) and ATTAINS
    unit (``au__``) columns onto a table keyed by ``comid`` (and ``attains_au``),
    each only when its file exists. Shared by the values table and the NRSA frame."""
    import pyarrow.parquet as pq
    table = concrete(table)
    if strata_path(root).exists():
        schema = pq.read_schema(strata_path(root)).names
        wanted = [c for c in strata_columns if c in schema and c not in table.column_names]
        if wanted:
            strata = pq.read_table(strata_path(root), columns=["comid", *wanted])
            table = table.join(strata, keys="comid", join_type="left outer")
    if candidates_path(root).exists():
        table = table.join(_prefixed(pq.read_table(candidates_path(root)), "sc__"), keys="comid", join_type="left outer")
    if erom_path(root).exists():
        table = table.join(_prefixed(pq.read_table(erom_path(root)), "erom__"), keys="comid", join_type="left outer")
    if attains_attributes_path(root).exists() and "attains_au" in table.column_names:
        import pyarrow as pa
        au = pq.read_table(attains_attributes_path(root))
        keep = ["assessmentunitidentifier"] + [c for c in AU_JOIN if c in au.column_names]
        au = au.select(keep).rename_columns(["attains_au"] + [f"au__{c}" for c in keep[1:]])
        # pandas-written files carry large_string keys; pyarrow joins need one key type on both sides
        au = au.set_column(0, "attains_au", au.column("attains_au").cast(pa.string()))
        table = table.set_column(table.schema.get_field_index("attains_au"), "attains_au",
                                 table.column("attains_au").cast(pa.string()))
        table = table.join(au, keys="attains_au", join_type="left outer")
    return table.sort_by("comid")


def assemble_values(root: DataRoot, huc8s: list[str], progress: Progress):
    """Concatenate the parts and join the strata, candidate, EROM and ATTAINS
    columns (each only when its file exists)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    parts = values_parts_dir(root)
    tables = [pq.read_table(parts / f"{h}.parquet") for h in huc8s]
    table = pa.concat_tables(tables, promote_options="default")
    table = join_caches(root, table.combine_chunks().sort_by("comid"))
    progress.say(f"values: assembled {table.num_rows:,} rows from {len(huc8s)} parts")
    return table


def parity_a(table) -> dict:
    """Scheme A: the harvested index of every function equals the baked one
    and the ECI agrees to 1e-9."""
    import pyarrow.compute as pc
    names = table.column_names
    functions = {}
    mismatch = np.zeros(table.num_rows, dtype=bool)
    compared = 0
    for name in names:
        if not name.startswith("index_") or f"scores_{name}" not in names:
            continue
        mine = np.asarray(table.column(name).to_pandas(), dtype=float)
        theirs = np.asarray(table.column(f"scores_{name}").to_pandas(), dtype=float)
        both_null = np.isnan(mine) & np.isnan(theirs)
        differ = ~both_null & ~np.isclose(mine, theirs, atol=1e-12, equal_nan=False)
        functions[name.replace("index_", "", 1)] = int(differ.sum())
        mismatch |= differ
        compared = table.num_rows
    if "scores_eci_raw" in names and "eci_raw" in names:
        mine = np.asarray(table.column("eci_raw").to_pandas(), dtype=float)
        theirs = np.asarray(table.column("scores_eci_raw").to_pandas(), dtype=float)
        both_null = np.isnan(mine) & np.isnan(theirs)
        differ = ~both_null & ~np.isclose(mine, theirs, atol=1e-9, equal_nan=False)
        functions["eci_raw"] = int(differ.sum())
        mismatch |= differ
    return {"compared": int(compared), "mismatched_reaches": int(mismatch.sum()), "functions": functions}


# ------------------------------------------------------------ landscape
def _col(table, name: str, n: int) -> np.ndarray:
    if name in table.column_names:
        return np.asarray(table.column(name).to_pandas(), dtype=float)
    return np.full(n, np.nan)


def _sum(*arrays) -> np.ndarray:
    return np.sum(np.stack(arrays), axis=0)


def derived_landscape(table) -> dict[str, np.ndarray]:
    """The derived quantities from a table carrying the cached StreamCat
    columns (and, when present, the ``sc__`` candidates and ``erom__`` ratios)."""
    n = table.num_rows
    col = lambda name: _col(table, name, n)  # noqa: E731
    out: dict[str, np.ndarray] = {}
    out["agriculture_ws"] = _sum(col("pctcrop2019ws"), col("pcthay2019ws"))
    runoff = col("runoffws")
    with np.errstate(divide="ignore", invalid="ignore"):
        out["dor"] = np.where(runoff > 0, 100.0 * col("damnrmstorws") / (1000.0 * np.where(runoff > 0, runoff, 1.0)), np.nan)
    out["wetland_ws"] = _sum(col("pctwdwet2019ws"), col("pcthbwet2019ws"))
    out["woody_wsrp100"] = _sum(col("pctconif2019wsrp100"), col("pctdecid2019wsrp100"), col("pctmxfst2019wsrp100"),
                                col("pctshrb2019wsrp100"), col("pctwdwet2019wsrp100"))
    out["natural_wsrp100"] = _sum(out["woody_wsrp100"], col("pctgrs2019wsrp100"), col("pcthbwet2019wsrp100"))
    out["corridor_conversion_wsrp100"] = _sum(col("pctcrop2019wsrp100"), col("pcthay2019wsrp100"), col("pctimp2019wsrp100"))
    out["woody_catrp100"] = _sum(col("sc__pctconif2019catrp100"), col("sc__pctdecid2019catrp100"),
                                 col("sc__pctmxfst2019catrp100"), col("sc__pctshrb2019catrp100"),
                                 col("sc__pctwdwet2019catrp100"))
    out["natural_catrp100"] = _sum(out["woody_catrp100"], col("sc__pctgrs2019catrp100"), col("sc__pcthbwet2019catrp100"))
    out["corridor_conversion_catrp100"] = _sum(col("sc__pctcrop2019catrp100"), col("sc__pcthay2019catrp100"),
                                               col("sc__pctimp2019catrp100"))
    hydric = col("sc__pcthydricws")
    with np.errstate(divide="ignore", invalid="ignore"):
        out["wetland_retention"] = np.where(hydric > 0, out["wetland_ws"] / np.where(hydric > 0, hydric, 1.0), np.nan)
    out["mines_ws"] = _sum(col("sc__minedensws"), col("sc__coalminedensws"))
    for name in ("hyd", "sed", "chem", "conn", "temp", "habt"):
        out[f"{name}_min"] = np.fmin(col(f"{name}cat"), col(f"{name}ws"))
    return out


def inputs_landscape(root: DataRoot, options: Optional[dict] = None) -> str:
    stamps = []
    for path in (strata_path(root), root.national / "streamcat.parquet", candidates_path(root), erom_path(root)):
        stamps.append((path.name, path.stat().st_size, int(path.stat().st_mtime)) if path.exists() else None)
    return digest("landscape", ANALYSIS_VERSION, stamps, 1)


def run_landscape(root: DataRoot, progress: Progress, control: Control, options: Optional[dict] = None) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    if not strata_path(root).exists():
        raise RuntimeError("strata.parquet not found: run the strata step first")
    streamcat = root.national / "streamcat.parquet"
    if not streamcat.exists():
        raise RuntimeError("national/streamcat.parquet not found: run the national streamcat step first")
    progress.begin("analysis", "landscape", total=4, message="landscape: joining the national tables")
    table = pq.read_table(strata_path(root))
    table = table.join(pq.read_table(streamcat), keys="comid", join_type="left outer")
    progress.tick(done=1)
    if candidates_path(root).exists():
        table = table.join(_prefixed(pq.read_table(candidates_path(root)), "sc__"), keys="comid", join_type="left outer")
    progress.tick(done=2)
    if erom_path(root).exists():
        table = table.join(_prefixed(pq.read_table(erom_path(root)), "erom__"), keys="comid", join_type="left outer")
    table = table.sort_by("comid")
    progress.tick(done=3, message="landscape: derived quantities")
    derived = derived_landscape(table)
    for name, values in derived.items():
        table = table.append_column(name, pa.array(values.astype(np.float32), pa.float32(), mask=~np.isfinite(values)))
    scored = scored_comids(root)
    in_set = np.isin(np.asarray(table.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64), scored)
    table = table.append_column("in_scored_set", pa.array(in_set, pa.bool_()))
    # float32 for every value column keeps the 2.65M-row table small
    columns = []
    for field in table.schema:
        column = table.column(field.name)
        if pa.types.is_float64(field.type):
            column = column.cast(pa.float32())
        columns.append(column)
    table = pa.table(dict(zip(table.column_names, columns)))
    path = common.write_parquet(table, landscape_path(root))
    progress.tick(done=4)
    report = landscape_parity(root, table)
    landscape_parity_path(root).write_text(json.dumps(report, indent=1), encoding="utf-8")
    progress.say(f"landscape.parquet: {table.num_rows:,} reaches, {len(table.column_names)} columns, "
                 f"{int(in_set.sum()):,} in the scored set; parity pairs checked: {report.get('pairs_checked', 0)}, "
                 f"worst max difference {report.get('worst_max_abs_diff')}")
    if report.get("failed"):
        raise RuntimeError(f"landscape arithmetic disagrees with the values table on {report['failed']}")
    return path


def scored_comids(root: DataRoot) -> np.ndarray:
    import pyarrow.parquet as pq
    if values_path(root).exists():
        return np.asarray(pq.read_table(values_path(root), columns=["comid"]).column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    parts = []
    for path in sorted(root.huc8.glob("*/scores.parquet")) if root.huc8.exists() else []:
        parts.append(np.asarray(pq.read_table(path, columns=["comid"]).column("comid").to_numpy(zero_copy_only=False), dtype=np.int64))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int64)


def landscape_parity(root: DataRoot, landscape, *, sample: int = 10_000, tolerance: float = 1e-4) -> dict:
    """Compare the recomputed quantities with the harvested inputs on a
    sample of scored reaches. A pair fails when more than 0.1 % of its
    compared rows differ by more than the pair's absolute allowance plus
    ``tolerance`` relative (float32 storage bounds the agreement); a pair
    with a method restriction compares only the rows that scored it."""
    import pyarrow.parquet as pq
    if not values_path(root).exists():
        return {"pairs_checked": 0, "note": "values.parquet absent"}
    names = set(pq.read_schema(values_path(root)).names)
    present = [p for p in LANDSCAPE_PARITY
               if p[0] in names and p[1] in landscape.column_names and (p[3] is None or p[3][0] in names)]
    if not present:
        return {"pairs_checked": 0}
    columns = sorted({p[0] for p in present} | {p[3][0] for p in present if p[3] is not None})
    vt = pq.read_table(values_path(root), columns=["comid"] + columns).slice(0, sample)
    comids = np.asarray(vt.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    land_comids = np.asarray(landscape.column("comid").to_numpy(zero_copy_only=False), dtype=np.int64)
    pos = np.searchsorted(land_comids, comids)
    pos = np.minimum(pos, max(len(land_comids) - 1, 0))
    found = land_comids[pos] == comids
    report: dict = {"pairs_checked": len(present), "sample": int(found.sum()), "pairs": {}, "failed": []}
    worst = 0.0
    for vcol, lcol, allowance, restrict in present:
        mine = np.asarray(vt.column(vcol).to_pandas(), dtype=float)[found]
        theirs = np.asarray(landscape.column(lcol).to_pandas(), dtype=float)[pos[found]]
        both = np.isfinite(mine) & np.isfinite(theirs)
        if restrict is not None:
            method = np.asarray(vt.column(restrict[0]).to_pylist(), dtype=object)[found]
            both &= method == restrict[1]
        diff = np.abs(mine[both] - theirs[both])
        limit = allowance + tolerance * np.maximum(1.0, np.abs(theirs[both]))
        over = diff > limit
        max_diff = float(diff.max()) if diff.size else 0.0
        report["pairs"][f"{vcol} vs {lcol}"] = {
            "compared": int(both.sum()), "max_abs_diff": max_diff, "allowance": allowance,
            "share_over": float(over.mean()) if over.size else 0.0,
            "restricted_to": None if restrict is None else restrict[1]}
        worst = max(worst, max_diff)
        if over.size and float(over.mean()) > 0.001:
            report["failed"].append(f"{vcol} vs {lcol}")
    report["worst_max_abs_diff"] = worst
    return report
