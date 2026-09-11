"""USGS NAS established occurrences for the whole country, pulled once in
pages of 5,000 records (about a hundred requests) into ``national/nas.parquet``.

The chunk stage then answers each HUC12 the way the app's query does, by
equality on the record's HUC12 field, without a request: the API filters on
the same field, and most established records carry one (88 percent in
Virginia). A replica of the per-HUC12 query costs 86,000 requests nationally;
the bulk pull costs about a hundred.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import config, http
from ..paths import DataRoot
from ..state import Control, Ledger, Progress
from . import common

STAGE = "nas"
PAGE = 5000
COLUMNS = ("key", "speciesID", "scientificName", "commonName", "group", "state",
           "huc8", "huc10", "huc12", "year", "status")


def cache_path(root: DataRoot) -> Path:
    return root.national / "nas.parquet"


def fetch_page(offset: int, limit: int = PAGE) -> dict:
    """One page of every established record, any place, any species."""
    return http.get_json(config.NAS_URL, {"status": "established", "limit": str(limit), "offset": str(offset)},
                         timeout=900.0, min_interval_s=config.NAS_MIN_INTERVAL_S)


def _truthy(value) -> bool:
    """The API sends ``endOfRecords`` as the text "true" / "false"."""
    return str(value).strip().lower() in ("true", "1", "yes")


def _slim(record: dict) -> dict:
    out = {}
    for column in COLUMNS:
        value = record.get(column)
        if column == "year":
            try:
                value = int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                value = None
        else:
            value = "" if value is None else str(value)
        out[column] = value
    return out


def run_nas_national(root: DataRoot, progress: Progress, control: Control, *, fetch=fetch_page,
                     page: int = PAGE) -> Path:
    """Page through the established records until the API says it is the end;
    resumable page by page."""
    import pyarrow as pa
    ledger = Ledger(root, "nas-national")
    parts = root.national / "nas_parts"
    parts.mkdir(parents=True, exist_ok=True)
    progress.begin("national", STAGE, total=1, message="NAS national: established records, pages of "
                                                        f"{page:,}")
    offset, pages, records = 0, 0, 0
    while True:
        key = f"o{offset:09d}"
        control.check()
        if key in ledger:
            payload = dict(common.read_parts(parts)).get(key) or {}
            end = bool(payload.get("end"))
            n = len(payload.get("records") or [])
        else:
            data = fetch(offset, page)
            results = data.get("results") or [] if isinstance(data, dict) else []
            end = _truthy(data.get("endOfRecords")) if isinstance(data, dict) else True
            end = end or len(results) < page
            common.write_part(parts, key, {"records": [_slim(r) for r in results], "end": end})
            ledger.add(key, n=len(results), end=end)
            n = len(results)
        pages += 1
        records += n
        progress.tick(done=pages, total=pages + (0 if end else 1),
                      message=f"NAS national: {pages} pages, {records:,} records")
        if end:
            break
        offset += page
    rows, seen = [], set()
    for _key, payload in common.read_parts(parts):
        for r in payload.get("records") or []:
            ident = r.get("key") or (r.get("scientificName"), r.get("huc12"), r.get("year"), r.get("state"))
            if ident in seen:
                continue
            seen.add(ident)
            rows.append(r)
    table = pa.Table.from_pylist(rows, schema=pa.schema(
        [(c, pa.int32() if c == "year" else pa.string()) for c in COLUMNS]))
    common.write_parquet(table, cache_path(root))
    progress.say(f"nas.parquet: {table.num_rows:,} established records, "
                 f"{len(set(table.column('huc12').to_pylist()) - {''}):,} HUC12s")
    common.drop_parts(parts)
    ledger.clear()
    return cache_path(root)


def cached_taxa(root: DataRoot) -> Optional[dict[str, dict]]:
    """``{huc12: {"taxa": sorted names, "n": record count}}`` from the cache,
    None when there is no cache."""
    import pyarrow.parquet as pq
    path = cache_path(root)
    if not path.exists():
        return None
    table = pq.read_table(path, columns=["huc12", "scientificName"])
    names: dict[str, set] = {}
    counts: dict[str, int] = {}
    for huc12, name in zip(table.column("huc12").to_pylist(), table.column("scientificName").to_pylist()):
        if not huc12:
            continue
        counts[huc12] = counts.get(huc12, 0) + 1
        if name:
            names.setdefault(huc12, set()).add(name)
    return {h: {"taxa": sorted(names.get(h, ())), "n": counts[h]} for h in counts}
