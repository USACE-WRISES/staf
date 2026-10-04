"""WQP nutrient back-fill and recent re-pull (bundle v2, Phase 3).

SFARI #58 reads TN and TP results since 2015-01-01; the national builder's pull
(``D:\\Data\\easi-national\\national\\wqp\\wqp_results.parquet``) starts in 2016-09. ``pull`` fetches
the missing months with the builder's own month downloader (same WQX3 request: every stream site,
the five TN/TP characteristic names the apps query, NWIS and STORET; a cut month is halved and
retried) into ``<sources>/wqp/monthly/``, and ``combine`` joins them into
``<sources>/wqp/wqp_backfill.parquet`` (text columns, deduplicated on the result identifier).

Results reach WQP weeks to months after sampling, so a month pulled early is incomplete. ``RECENT``
re-pulls the latest twelve months and the current one into ``<sources>/wqp/recent/`` and combines
them into ``<sources>/wqp/wqp_recent.parquet``; the bundle's table takes those months from that file
instead of the national pull. The national builder's files are left as they are.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from . import REPO_ROOT, sources

MONTHS = [f"{y}-{m:02d}" for y in (2015, 2016) for m in range(1, 13) if (y, m) <= (2016, 8)]
ID_COLUMN = "Result_MeasureIdentifier"
#: (folder under <sources>/wqp, combined file, sources.json key) per pull
BACKFILL = ("monthly", "wqp_backfill.parquet", "wqp_backfill")
RECENT = ("recent", "wqp_recent.parquet", "wqp_recent")


def recent_months(today: date | None = None, back: int = 12) -> list[str]:
    """The current month and the ``back`` months before it, oldest first."""
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(back + 1):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def _builder():
    path = str(REPO_ROOT / "tools" / "easi-national")
    if path not in sys.path:
        sys.path.insert(0, path)
    from builder.stages import wqp_national
    return wqp_national


def pull(months=MONTHS, workers: int = 3, log=print, kind=BACKFILL) -> dict:
    wq = _builder()
    folder = sources.SOURCES_DIR / "wqp" / kind[0]
    folder.mkdir(parents=True, exist_ok=True)
    results = {}

    def one(month):
        dest = folder / f"wqx3_{month}.csv"
        if dest.exists():
            return month, {"status": "done", "rows": None}
        for attempt, wait in enumerate((0, 60, 180, 600, 1200)):
            time.sleep(wait)
            r = wq.download_month(month, dest)
            if r["status"] == "done":
                return month, r
            log(f"wqp {month}: attempt {attempt + 1} {r['status']} {r.get('error')}")
        return month, r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, m) for m in months]):
            month, r = fut.result()
            results[month] = r
            log(f"wqp {month}: {r['status']} {r.get('rows')} rows")
    return results


def combine(log=print, kind=BACKFILL, months=MONTHS) -> Path:
    import pyarrow as pa
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq
    folder = sources.SOURCES_DIR / "wqp" / kind[0]
    tables = []
    for month in months:
        csv_path = folder / f"wqx3_{month}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"month {month} was not pulled: {csv_path}")
        t = pcsv.read_csv(csv_path, convert_options=pcsv.ConvertOptions(column_types={}, strings_can_be_null=True,
                                                                          auto_dict_encode=False),
                          read_options=pcsv.ReadOptions(block_size=1 << 26))
        t = t.cast(pa.schema([pa.field(f.name, pa.string()) for f in t.schema]))
        tables.append(t.append_column("source_month", pa.array([month] * t.num_rows, pa.string())))
    table = pa.concat_tables(tables, promote_options="default")
    if ID_COLUMN in table.column_names:
        df = table.to_pandas().drop_duplicates(ID_COLUMN)
        table = pa.Table.from_pandas(df, preserve_index=False)
    out = sources.SOURCES_DIR / "wqp" / kind[1]
    pq.write_table(table, out, compression="zstd")
    log(f"wqp {kind[2]}: {table.num_rows} results, {out.stat().st_size / 1e6:.1f} MB")
    sources.record(kind[2], {"months": list(months), "rows": table.num_rows, "bytes": out.stat().st_size,
                             "service": "WQP WQX3 Result/search (the national builder's request)"})
    return out


def pull_recent(log=print) -> Path:
    months = recent_months()
    results = pull(months, log=log, kind=RECENT)
    failed = sorted(m for m, r in results.items() if r["status"] != "done")
    if failed:
        raise RuntimeError(f"WQP months not pulled: {failed}")
    return combine(log=log, kind=RECENT, months=months)
