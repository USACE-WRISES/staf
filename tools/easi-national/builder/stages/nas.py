"""USGS NAS established taxa per HUC12 of the chunk. From the national cache
(``national/nas.parquet``, see ``nas_national``) when it exists, by equality
on the record HUC12 like the app's query; otherwise replicating that query
(``status=established&limit=500``) at a gentle pace, with a ledger so a pause
resumes without repeating requests."""
from __future__ import annotations

import json

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk
from . import common

STAGE = "nas"
SOURCE = "nas"


def fetch_taxa(huc12: str) -> tuple[list[str], int]:
    """``(sorted established scientific names, record count)`` for a HUC12."""
    data = http.get_json(config.NAS_URL, {"status": "established", "limit": "500", "huc12": huc12},
                         min_interval_s=config.NAS_MIN_INTERVAL_S)
    records = data.get("results", data if isinstance(data, list) else [])
    names = {x.get("scientificName") for x in records if x.get("scientificName")}
    return sorted(n for n in names if n), len(records)


def chunk_huc12s(root: DataRoot, chunk: Chunk) -> list[str]:
    import pyarrow.parquet as pq
    path = root.chunk_raw(chunk.id, "huc12")
    if not path.exists():
        raise RuntimeError("the huc12 stage must run first")
    table = pq.read_table(path, columns=["huc_12"])
    return sorted({str(v) for v in table.column("huc_12").to_pylist() if v})


def run_nas(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
            control: Control, *, force: bool = False, fetch=fetch_taxa) -> None:
    hucs = chunk_huc12s(root, chunk)
    inputs = common.chunk_inputs(STAGE, chunk, len(hucs), 1)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        import pyarrow as pa
        from .nas_national import cached_taxa
        cache = cached_taxa(root)
        if cache is not None:
            progress.begin(chunk.id, STAGE, total=len(hucs),
                           message=f"NAS: {len(hucs):,} HUC12s from the national cache")
            rows = []
            for huc12 in hucs:
                hit = cache.get(huc12) or {"taxa": [], "n": 0}
                rows.append({"huc12": huc12, "taxa": json.dumps(hit["taxa"]), "n_records": int(hit["n"]),
                             "truncated": int(hit["n"]) >= 500})
            table = pa.Table.from_pylist(rows, schema=pa.schema([
                ("huc12", pa.string()), ("taxa", pa.string()), ("n_records", pa.int32()),
                ("truncated", pa.bool_())]))
            common.write_parquet(table, out)
            progress.tick(done=len(hucs))
            progress.say(f"nas.parquet: {len(rows):,} HUC12s from the national cache")
            return
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        parts = common.parts_dir(root, chunk.id, SOURCE)
        progress.begin(chunk.id, STAGE, total=len(hucs), message=f"NAS: {len(hucs):,} HUC12s")
        done = 0
        for huc12 in hucs:
            done += 1
            if huc12 in ledger:
                progress.tick(done=done)
                continue
            control.check()
            try:
                taxa, n = fetch(huc12)
            except Exception as exc:  # noqa: BLE001 - leave it pending for a retry
                progress.say(f"NAS {huc12}: {exc}")
                continue
            common.write_part(parts, huc12, {"taxa": taxa, "n": n})
            ledger.add(huc12, n=n)
            progress.tick(done=done, message=f"NAS {done}/{len(hucs)}")
        rows = []
        for huc12, payload in common.read_parts(parts):
            rows.append({"huc12": huc12, "taxa": json.dumps(payload.get("taxa") or []),
                         "n_records": int(payload.get("n") or 0),
                         "truncated": int(payload.get("n") or 0) >= 500})
        missing = len(hucs) - len(rows)
        if missing > 0.02 * len(hucs) + 5:
            raise RuntimeError(f"NAS: {missing:,} of {len(hucs):,} HUC12s still unanswered; rerun")
        table = pa.Table.from_pylist(rows, schema=pa.schema([
            ("huc12", pa.string()), ("taxa", pa.string()), ("n_records", pa.int32()),
            ("truncated", pa.bool_())]))
        common.write_parquet(table, out)
        progress.say(f"nas.parquet: {len(rows):,} HUC12s" + (f", {missing} missing" if missing else ""))
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
