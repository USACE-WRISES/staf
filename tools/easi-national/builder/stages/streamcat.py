"""StreamCat rows for a chunk: the 21 base names the adapters read, all areas
of interest. From the national cache (``national/streamcat.parquet``, see
``streamcat_national``) when it exists; otherwise by state selector when the
chunk is a state and by COMID list for the rest. Every request lands in a
part file and a ledger, so a pause resumes at the next request.
"""
from __future__ import annotations

from typing import Optional

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress, UnitStates
from ..units import Chunk, chunk_comids
from . import common

STAGE = "streamcat"
SOURCE = "streamcat"


def _groups(names: list[str]) -> list[list[str]]:
    size = config.STREAMCAT_NAME_GROUP
    return [names[i:i + size] for i in range(0, len(names), size)]


def _post(payload: dict) -> list[dict]:
    data = http.post_json(config.STREAMCAT_URL, payload, timeout=config.WQP_TIMEOUT_S)
    items = data.get("items") if isinstance(data, dict) else None
    return list(items or [])


def _normalize(items: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in items:
        row = {str(k).lower(): v for k, v in item.items()}
        comid = row.pop("comid", None)
        try:
            comid = int(comid)
        except (TypeError, ValueError):
            continue
        out.setdefault(comid, {}).update(
            {k: v for k, v in row.items() if v is not None})
    return out


def run_streamcat(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
                  control: Control, *, force: bool = False, names: Optional[list[str]] = None) -> None:
    names = names or config.streamcat_names()
    inputs = common.chunk_inputs(STAGE, chunk, names, config.STREAMCAT_AOIS, 1)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        comids = chunk_comids(root, chunk).column("comid").to_pylist()
        wanted = sorted({int(c) for c in comids})
        parts = common.parts_dir(root, chunk.id, SOURCE)
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        groups = _groups(names)
        aoi = ",".join(config.STREAMCAT_AOIS)
        from .streamcat_national import cached_rows
        cached = cached_rows(root, wanted)
        # 1. state selector (one request per name group) when the chunk is a state
        #    and there is no national cache to read from
        requests_plan: list[tuple[str, dict]] = []
        for gi, group in enumerate(groups):
            for state in (chunk.states if not cached else []):
                requests_plan.append((f"state-{state}-g{gi}",
                                      {"name": ",".join(group), "aoi": aoi, "state": state}))
        # 2. COMID batches for every COMID (the state pull answers most of them;
        #    batches whose COMIDs are all covered are skipped at run time)
        size = config.STREAMCAT_COMID_CHUNK
        batches = [wanted[i:i + size] for i in range(0, len(wanted), size)]
        for gi, group in enumerate(groups):
            for bi, batch in enumerate(batches):
                requests_plan.append((f"comid-b{bi}-g{gi}",
                                      {"name": ",".join(group), "aoi": aoi,
                                       "comid": ",".join(str(c) for c in batch)}))
        total = len(requests_plan)
        progress.begin(chunk.id, STAGE, total=total,
                       message=f"StreamCat: {len(wanted):,} reaches, {len(cached):,} from the national cache, "
                               f"{total} requests")
        covered: dict[int, set[int]] = {}      # group index -> comids answered
        for gi in range(len(groups)):
            covered.setdefault(gi, set()).update(cached)
        for key, payload in common.read_parts(parts):
            gi = int(key.rsplit("-g", 1)[1])
            covered.setdefault(gi, set()).update(int(c) for c in payload.get("comids", []))
        done = 0
        for key, payload in requests_plan:
            done += 1
            if key in ledger:
                progress.tick(done=done)
                continue
            gi = int(key.rsplit("-g", 1)[1])
            if key.startswith("comid-"):
                batch = [int(c) for c in payload["comid"].split(",")]
                if all(c in covered.get(gi, set()) for c in batch):
                    ledger.add(key, skipped=True)
                    progress.tick(done=done)
                    continue
                payload = dict(payload, comid=",".join(
                    str(c) for c in batch if c not in covered.get(gi, set())))
            control.check()
            items = _post(payload)
            rows = _normalize(items)
            common.write_part(parts, key, {"comids": sorted(rows), "rows": rows})
            covered.setdefault(gi, set()).update(rows)
            ledger.add(key, n=len(rows))
            progress.tick(done=done, message=f"StreamCat {key}: {len(rows):,} rows")
        # merge (the cache first, then whatever the API answered)
        merged: dict[int, dict] = {c: dict(r) for c, r in cached.items()}
        for key, payload in common.read_parts(parts):
            for comid, row in (payload.get("rows") or {}).items():
                merged.setdefault(int(comid), {}).update(row)
        wanted_set = set(wanted)
        rows = [{"comid": c, **v} for c, v in sorted(merged.items()) if c in wanted_set]
        columns = sorted({k for r in rows for k in r if k != "comid"})
        import pyarrow as pa
        arrays = {"comid": pa.array([r["comid"] for r in rows], pa.int64())}
        for col in columns:
            values = []
            for r in rows:
                v = r.get(col)
                try:
                    values.append(None if v is None else float(v))
                except (TypeError, ValueError):
                    values.append(None)
            arrays[col] = pa.array(values, pa.float64())
        common.write_parquet(pa.table(arrays), out)
        missing = len(wanted_set) - len(rows)
        progress.say(f"streamcat.parquet: {len(rows):,} reaches, {len(columns)} columns"
                     + (f", {missing:,} without a StreamCat row" if missing else ""))
        common.drop_parts(parts)
        ledger.clear()

    common.run_stage(states, chunk.id, STAGE, inputs, work, progress, force=force)
