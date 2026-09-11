"""Per-HUC8 scoring: merge derived + joins into evidence records, run the
app's ``assess_preloaded`` over each (about 1.5 ms), and write the evidence
file the app reads plus the flat scores file analysts and the tiles read."""
from __future__ import annotations

import json
from typing import Optional

from .. import config
from ..paths import DataRoot
from ..state import Control, Progress, UnitStates, digest, now_iso
from ..units import Chunk
from . import common, xs_derive

STAGE = "score"

SCORE_COLUMNS = ("rating", "index", "fscore", "status", "tier", "source")


def _function_key(function_id: str) -> str:
    return function_id.replace("-", "_")


def flatten_report(report: dict, meta_by_function: dict) -> dict:
    """The flat scores row: rollup fields plus per-function columns."""
    row = {
        "eci_raw": report.get("ecosystemConditionIndexRaw"),
        "eci": report.get("ecosystemConditionIndex"),
        "band": _band(report.get("ecosystemConditionIndexRaw")),
        "phys_raw": (report.get("subIndicesRaw") or {}).get("physical"),
        "chem_raw": (report.get("subIndicesRaw") or {}).get("chemical"),
        "bio_raw": (report.get("subIndicesRaw") or {}).get("biological"),
        "phys": (report.get("subIndices") or {}).get("physical"),
        "chem": (report.get("subIndices") or {}).get("chemical"),
        "bio": (report.get("subIndices") or {}).get("biological"),
        "provisional": bool(report.get("provisionalCoverage")),
        "coverage_overall": ((report.get("coverage") or {}).get("overall") or {}).get("fraction"),
        "coverage_physical": (((report.get("coverage") or {}).get("outcomes") or {}).get("physical") or {}).get("fraction"),
        "n_rated": report.get("computedCount"),
        "n_selected": report.get("selectedCount"),
    }
    worst = None
    for r in report.get("metricRows") or []:
        key = _function_key(r["functionId"])
        row[f"fs_{key}"] = r.get("functionScore")
        row[f"rating_{key}"] = r.get("rating")
        row[f"index_{key}"] = r.get("index")
        row[f"status_{key}"] = r.get("status")
        row[f"tier_{key}"] = r.get("sourceTier")
        row[f"source_{key}"] = r.get("source")
        row[f"value_{key}"] = r.get("valueText")
        fs = r.get("functionScore")
        if fs is not None and (worst is None or fs < worst[0]):
            worst = (fs, r["functionId"])
    row["worst_function"] = worst[1] if worst else None
    return row


def _band(eci: Optional[float]) -> Optional[str]:
    if eci is None:
        return None
    from easi import scoring
    return scoring.index_band_label(eci)


def run_score(root: DataRoot, chunk: Chunk, huc8: str, states: UnitStates,
              progress: Progress, control: Control, *, force: bool = False) -> None:
    from easi.national import method_version
    inputs = digest(STAGE, huc8, chunk.id, method_version(), xs_derive.xsections_stamp(root, huc8), 2)

    def work():
        import pyarrow as pa
        import pyarrow.parquet as pq
        from easi import config as easi_config
        from easi.national import SCHEMA_VERSION, client, records
        derived = pq.read_table(root.huc8_file(huc8, "derived")).to_pylist()
        joins = {int(r["comid"]): r for r in pq.read_table(root.huc8_file(huc8, "joins")).to_pylist()}
        streamcat_table = pq.read_table(root.chunk_raw(chunk.id, "streamcat"))
        sc_rows = {int(r["comid"]): {k: v for k, v in r.items() if k != "comid" and v is not None}
                   for r in streamcat_table.to_pylist()}
        meta_by_function = {m["functionId"]: m for m in easi_config.metrics_by_id().values()}
        xs_path = root.huc8_file(huc8, "xsections")
        xsections = ({int(r["comid"]): r for r in pq.read_table(xs_path).to_pylist()}
                     if xs_path.exists() else {})
        stamp, version = now_iso(), method_version()
        evidence_rows, score_rows = [], []
        total = len(derived)
        progress.begin(huc8, STAGE, total=total, message=f"score {huc8}: {total:,} reaches")
        for i, d in enumerate(derived):
            if i % 200 == 0:
                control.check()
                progress.tick(done=i)
            comid = int(d["comid"])
            j = joins.get(comid) or {}
            xrow = xsections.get(comid)
            geomorph = geomorph_for(xrow)
            record = {
                **{k: d.get(k) for k in records.IDENTITY_FIELDS},
                "streamcat": sc_rows.get(comid) or {},
                "nrsa": _load(d.get("nrsa")), "bankfull": _load(d.get("bankfull")),
                "attains_exact": _load(j.get("attains_exact")) or {},
                "attains_nearby": _load(j.get("attains_nearby")) or {},
                "wqp_tn": _load(j.get("wqp_tn")), "wqp_tp": _load(j.get("wqp_tp")),
                "nid_dams": _load(j.get("nid_dams")), "nas_taxa": _load(j.get("nas_taxa")),
                "nas_scope": j.get("nas_scope"), "geomorph": geomorph,
                "schema_version": SCHEMA_VERSION,
            }
            report = client.score_record(record, cross_section=False)
            evidence_rows.append(records.to_row(record))
            score_rows.append({"comid": comid, "huc4": d["huc4"], "huc8": huc8, "vpu": d.get("vpu"),
                               "gnis_name": d.get("gnis_name"), "streamorde": d.get("streamorde"),
                               "totdasqkm": d.get("totdasqkm"),
                               **flatten_report(report, meta_by_function),
                               "tier": config.XS_TIER if geomorph is not None else config.BASE_TIER,
                               "xs_status": (xrow or {}).get("status") or "none",
                               "dem_res_m": (xrow or {}).get("dem_res_m"),
                               "xs_n": (geomorph or {}).get("n_transects"),
                               "xs_er": (geomorph or {}).get("entrenchment_ratio"),
                               "xs_bhr": (geomorph or {}).get("bank_height_ratio"),
                               "method_version": version, "computed_at": stamp})
        evidence = pa.Table.from_pylist(evidence_rows)
        scores = pa.Table.from_pylist(score_rows)
        common.write_parquet(evidence, root.huc8_file(huc8, "evidence"))
        common.write_parquet(scores, root.huc8_file(huc8, "scores"))
        bands = {}
        for r in score_rows:
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        progress.say(f"{huc8} scores.parquet: {len(score_rows):,} reaches, bands {bands}")

    common.run_stage(states, huc8, STAGE, inputs, work, progress, force=force)


def geomorph_for(xrow) -> object:
    """The record's ``geomorph``: the published slim dict for a derived reach,
    ``{}`` when sampling found nothing usable (the live app's own outcome),
    None when the cross-section stages have not run for it."""
    if xrow is None:
        return None
    if xrow.get("status") == "ok":
        return xs_derive.to_published(xrow.get("geomorph"), xrow.get("profile_stations"), xrow.get("profile_elevs"))
    if xrow.get("status") == "empty":
        return {}
    return None


def _load(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
