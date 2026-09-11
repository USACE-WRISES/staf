"""StreamCat for the whole country, pulled once by hydro-region and cached as
``national/streamcat.parquet`` (comid plus every column the adapters read,
all areas of interest). Chunks then take their rows from the cache instead of
asking the API, which turns the per-chunk StreamCat stage into a local filter.

The per-region zip files EPA used to publish are gone (404). The API's
``region`` selector (values such as ``Region02`` and ``Region03N``, listed by
the API root) answers a region's rows for a group of metrics in seconds, so
the national pull is about 21 regions times a handful of metric groups.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Optional

from .. import config, http
from ..paths import DataRoot
from ..state import CancelRequested, Control, Ledger, PauseRequested, Progress
from . import common
from .streamcat import _groups, _normalize, _post

STAGE = "streamcat"
#: the 21 CONUS hydro-regions, used when the API root does not list them
FALLBACK_REGIONS = ("Region01", "Region02", "Region03N", "Region03S", "Region03W", "Region04",
                    "Region05", "Region06", "Region07", "Region08", "Region09", "Region10L",
                    "Region10U", "Region11", "Region12", "Region13", "Region14", "Region15",
                    "Region16", "Region17", "Region18")


def cache_path(root: DataRoot) -> Path:
    return root.national / "streamcat.parquet"


def regions() -> list[str]:
    """The API's own region list; the CONUS list when it cannot be read."""
    try:
        data = http.get_json(config.STREAMCAT_URL, {})
        items = data.get("items") or [] if isinstance(data, dict) else []
        options = items[0].get("region_options") if items else None
        found = [str(o.get("regionid")) for o in (options or []) if o.get("regionid")]
        if found:
            return found
    except Exception:  # noqa: BLE001 - the fallback list is the same set
        pass
    return list(FALLBACK_REGIONS)


def _parts_dir(root: DataRoot) -> Path:
    path = root.national / "streamcat_parts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def table_of(rows: dict[int, dict]):
    """``comid`` (int64) plus one float64 column per metric column, sorted by comid."""
    import pyarrow as pa
    comids = sorted(rows)
    columns = sorted({k for r in rows.values() for k in r})
    arrays = {"comid": pa.array(comids, pa.int64())}
    for col in columns:
        values = []
        for c in comids:
            v = rows[c].get(col)
            try:
                values.append(None if v is None else float(v))
            except (TypeError, ValueError):
                values.append(None)
        arrays[col] = pa.array(values, pa.float64())
    return pa.table(arrays)


def run_streamcat_national(root: DataRoot, progress: Progress, control: Control, *,
                           names: Optional[list[str]] = None, post=_post,
                           region_list: Optional[list[str]] = None,
                           workers: Optional[int] = None) -> Path:
    """Pull every region for every metric group (resumable by request) and
    merge into the cache parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    names = names or config.streamcat_names()
    groups = _groups(names)
    aoi = ",".join(config.STREAMCAT_AOIS)
    region_list = list(region_list or regions())
    plan = [(f"{region}-g{gi}", {"name": ",".join(group), "aoi": aoi, "region": region})
            for region in region_list for gi, group in enumerate(groups)]
    ledger = Ledger(root, "streamcat-national")
    parts = _parts_dir(root)
    pending = [(key, payload) for key, payload in plan if key not in ledger]
    done_n = len(plan) - len(pending)
    workers = max(1, workers or config.STREAMCAT_CONCURRENCY)
    progress.begin("national", STAGE, total=len(plan),
                   message=f"StreamCat national: {len(region_list)} regions x {len(groups)} metric groups, "
                           f"{len(pending)} requests to go, {workers} at a time")
    progress.tick(done=done_n)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        queue, running = list(pending), {}
        try:
            while queue or running:
                while queue and len(running) < workers:
                    control.check()
                    key, payload = queue.pop(0)
                    running[pool.submit(post, payload)] = key
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    key = running.pop(future)
                    rows = _normalize(future.result())            # re-raises Pause/Cancel/failures
                    common.write_parquet(table_of(rows), parts / f"{key}.parquet")
                    ledger.add(key, n=len(rows))
                    done_n += 1
                    progress.tick(done=done_n, message=f"StreamCat {key}: {len(rows):,} reaches")
        except (PauseRequested, CancelRequested):
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    region_tables = []
    for region in region_list:
        table = None
        for gi in range(len(groups)):
            part = pq.read_table(parts / f"{region}-g{gi}.parquet")
            table = part if table is None else table.join(part, keys="comid", join_type="full outer")
        if table is not None and table.num_rows:
            region_tables.append(table)
    merged = (pa.concat_tables(region_tables, promote_options="default").sort_by("comid")
              if region_tables else table_of({}))
    common.write_parquet(merged, cache_path(root))
    progress.say(f"streamcat.parquet: {merged.num_rows:,} reaches, {len(merged.column_names) - 1} columns")
    common.drop_parts(parts)
    ledger.clear()
    return cache_path(root)


def cached_rows(root: DataRoot, wanted: list[int]) -> dict[int, dict]:
    """Rows for ``wanted`` from the national cache; empty when there is none."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    path = cache_path(root)
    if not path.exists() or not wanted:
        return {}
    table = pq.read_table(path)
    sub = table.filter(pc.is_in(table.column("comid"), value_set=pa.array(sorted(set(wanted)), pa.int64())))
    columns = [c for c in sub.column_names if c != "comid"]
    data = {c: sub.column(c).to_pylist() for c in columns}
    out: dict[int, dict] = {}
    for i, comid in enumerate(sub.column("comid").to_pylist()):
        out[int(comid)] = {c: data[c][i] for c in columns if data[c][i] is not None}
    return out
