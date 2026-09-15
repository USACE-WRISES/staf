"""StreamCat rows for a chunk: the 21 base names the adapters read, all areas
of interest. From the national cache (``national/streamcat.parquet``, see
``streamcat_national``) when it exists; otherwise by state selector when the
chunk is a state and by COMID list for the rest. Every request lands in a
part file and a ledger, so a pause resumes at the next request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import config, http
from ..paths import DataRoot, atomic_write_text
from ..state import Control, Ledger, Progress, UnitStates, digest
from ..units import Chunk, chunk_comids
from . import common

STAGE = "streamcat"
SOURCE = "streamcat"


def _groups(names: list[str]) -> list[list[str]]:
    size = config.STREAMCAT_NAME_GROUP
    return [names[i:i + size] for i in range(0, len(names), size)]


def _plan_groups(names: list[str], aoi_by_name: Optional[dict[str, str]]) -> list[tuple[list[str], str, str]]:
    """Names, area of interest, and a unique resume suffix per request group."""
    default_aoi = ",".join(config.STREAMCAT_AOIS)
    if not aoi_by_name:
        return [(group, default_aoi, f"g{gi}") for gi, group in enumerate(_groups(names))]
    by_aoi: dict[str, list[str]] = {}
    for name in names:
        by_aoi.setdefault(aoi_by_name.get(name, default_aoi), []).append(name)
    return [(group, aoi, f"{aoi.replace(',', '+')}-g{gi}")
            for aoi, aoi_names in by_aoi.items()
            for gi, group in enumerate(_groups(aoi_names))]


def _response_columns(names: list[str], aoi: str) -> set[str]:
    """Column schema that distinguishes a missing request from a null answer."""
    return {name.lower() + ("" if area == config.STREAMCAT_OTHER_AOI else area.lower())
            for name in names for area in aoi.split(",")}


def _prepare_plan(parts: Path, ledger: Ledger, plan: list[tuple[str, dict]]) -> None:
    """Resume only identical requests, including selectors, names, and AOIs."""
    marker = parts / ".plan"
    fingerprint = digest(plan, 2)
    previous = marker.read_text(encoding="utf-8") if marker.exists() else None
    if previous != fingerprint:
        # Historical unversioned parts cannot prove which columns they fetched.
        common.drop_parts(parts)
        parts.mkdir(parents=True, exist_ok=True)
        ledger.clear()
        atomic_write_text(marker, fingerprint)


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
        out.setdefault(comid, {}).update(row)
    return out


def run_streamcat(root: DataRoot, chunk: Chunk, states: UnitStates, progress: Progress,
                  control: Control, *, force: bool = False, names: Optional[list[str]] = None,
                  aoi_by_name: Optional[dict[str, str]] = None) -> None:
    if names is None:
        names = config.streamcat_names()
        if aoi_by_name is None:
            aoi_by_name = config.streamcat_aoi_by_name()
    inputs = common.chunk_inputs(STAGE, chunk, names, config.STREAMCAT_AOIS, aoi_by_name, 2)
    out = root.chunk_raw(chunk.id, SOURCE)

    def work():
        comids = chunk_comids(root, chunk).column("comid").to_pylist()
        wanted = sorted({int(c) for c in comids})
        parts = common.parts_dir(root, chunk.id, SOURCE)
        ledger = Ledger(root, f"{SOURCE}-{chunk.id}")
        groups = _plan_groups(names, aoi_by_name)
        from .streamcat_national import cached_rows
        cached = cached_rows(root, wanted)
        # 1. state selector (one request per name group) when the chunk is a state
        #    and there is no national cache to read from
        requests_plan: list[tuple[str, dict]] = []
        covered: dict[str, set[int]] = {}
        key_groups: dict[str, str] = {}
        for group, aoi, suffix in groups:
            columns = _response_columns(group, aoi)
            covered[suffix] = {c for c, row in cached.items() if columns <= row.keys()}
            for state in (chunk.states if not cached else []):
                key = f"state-{state}-{suffix}"
                key_groups[key] = suffix
                requests_plan.append((key,
                                      {"name": ",".join(group), "aoi": aoi, "state": state}))
        # 2. COMID batches. With the national cache only the COMIDs it lacks
        #    (a few per cent, batched together: 1,600 requests became about a
        #    hundred on California, and the API rate-limits a long run of
        #    them); without it every COMID, the state pull answering most and
        #    batches whose COMIDs are all covered skipped at run time.
        size = config.STREAMCAT_COMID_CHUNK
        prefix = "missing" if cached else "comid"
        for group, aoi, suffix in groups:
            missing = [c for c in wanted if c not in covered[suffix]]
            batches = [missing[i:i + size] for i in range(0, len(missing), size)]
            for bi, batch in enumerate(batches):
                key = f"{prefix}-b{bi}-{suffix}"
                key_groups[key] = suffix
                requests_plan.append((key,
                                      {"name": ",".join(group), "aoi": aoi,
                                       "comid": ",".join(str(c) for c in batch)}))
        _prepare_plan(parts, ledger, requests_plan)
        total = len(requests_plan)
        progress.begin(chunk.id, STAGE, total=total,
                       message=f"StreamCat: {len(wanted):,} reaches, {len(cached):,} from the national cache, "
                               f"{total} requests")
        for key, payload in common.read_parts(parts):
            if key in key_groups:
                covered[key_groups[key]].update(int(c) for c in payload.get("comids", []))
        done = 0
        for key, payload in requests_plan:
            done += 1
            if key in ledger and (parts / f"{key}.json").exists():
                progress.tick(done=done)
                continue
            suffix = key_groups[key]
            if key.startswith(("comid-", "missing-")):
                batch = [int(c) for c in payload["comid"].split(",")]
                if all(c in covered[suffix] for c in batch):
                    ledger.add(key, skipped=True)
                    progress.tick(done=done)
                    continue
                payload = dict(payload, comid=",".join(
                    str(c) for c in batch if c not in covered[suffix]))
            control.check()
            items = _post(payload)
            rows = _normalize(items)
            for row in rows.values():
                for column in _response_columns(payload["name"].split(","), payload["aoi"]):
                    row.setdefault(column, None)
            common.write_part(parts, key, {"comids": sorted(rows), "rows": rows})
            covered[suffix].update(rows)
            ledger.add(key, n=len(rows))
            progress.tick(done=done, message=f"StreamCat {key}: {len(rows):,} rows")
        # merge (the cache first, then whatever the API answered)
        merged: dict[int, dict] = {c: dict(r) for c, r in cached.items()}
        for key, payload in common.read_parts(parts):
            if key not in key_groups:
                continue
            for comid, row in (payload.get("rows") or {}).items():
                merged.setdefault(int(comid), {}).update(row)
        wanted_set = set(wanted)
        rows = [{"comid": c, **v} for c, v in sorted(merged.items()) if c in wanted_set]
        columns = sorted({k for r in rows for k in r if k != "comid"}
                         | {k for group, aoi, _ in groups for k in _response_columns(group, aoi)})
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
