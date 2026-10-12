"""Files the browser reads a few rows of at a time, written again with a page index.

The releases write these as one row group (the bundle's per-region stream lines, StreamCat rows, V2
attributes, point extras and catchment values) or in groups of 16,384 rows (the cross-section grid),
with no page index, so reading one row of one region took the row group's whole column chunks (1.6 MB
for one StreamCat row in 0108).
Here each is written again, with pyarrow, with the same rows in the same order, the same columns and
the same values, cut into pages of a few hundred rows with a page index (each page's place in the file
and its lowest and highest values), so a read takes the pages holding its rows. The grid's files are
also written in larger row groups, which keeps their footers small (a footer lists every group's
columns).

A copy is kept in the build's cache and written again only when its source or ``LAYOUT`` changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: the layout's version: a cached copy written by another version is written again
LAYOUT = 2
#: zstd's level: small pages compress less well, and this keeps the copies within about a fifth of their
#: sources' size (level 19, the releases' own, saves 2% more at 2.5 times the time)
LEVEL = 12
#: a column's dictionary is kept only while this small: a page read needs its column's dictionary page too
DICTIONARY_LIMIT = 16 * 1024
#: ...and only for a column with this few distinct values: the grid's nearly distinct floats (stages,
#: positions, checksums) wrote 11 to 16 KB dictionaries that every read took and none used (139 KB a
#: row group of 0108's sections, against 21 KB of the pages read)
DICTIONARY_DISTINCT = 256
#: (logical name, rows per page, rows per row group or None to keep the release's groups, dictionaries)
RULES = (
    # the stream lines, read by a box (each row's box columns): a map view reads the pages it shows. In
    # groups of 16,384 (the bundle's 4,096 gave 0512 a 0.87 MB footer, read before any line draws)
    (re.compile(r"^bundle/lines2_\w+\.parquet$"), 128, 16384, False),
    (re.compile(r"^bundle/v2/lines2_\w+\.parquet$"), 128, 16384, False),
    (re.compile(r"^bundle/v2/streamcat2_\w+\.parquet$"), 256, None, False),
    (re.compile(r"^bundle/v2/v2attrs2_\w+\.parquet$"), 256, None, False),
    (re.compile(r"^bundle/values/extras2_\w+\.parquet$"), 512, None, False),
    # the catchments' values, read by blocks of catchment (or line) rows (engine/blocks.ts): pages of 1,024
    # rows keep each column's offset index small (256-row pages made land cover's 98 KB in 0108)
    (re.compile(r"^bundle/values/(lc2|rip2|roads2|soils2|xings2)_\w+\.parquet$"), 1024, None, False),
    (re.compile(r"^xs/xs-\w+\.parquet$"), 1024, 65536, True),
    (re.compile(r"^xs/xs-\w+-sections\.parquet$"), 512, 65536, True),
    (re.compile(r"^xs/xs-\w+-median\.parquet$"), 32, 8192, True),
)


def rule(logical: str):
    """``(rows per page, rows per group or None, dictionaries)`` for a logical name the layout writes again,
    else None."""
    for pattern, page, group, dictionary in RULES:
        if pattern.match(logical):
            return page, group, dictionary
    return None


def dictionary_columns(table) -> list:
    """The columns of ``table`` with at most ``DICTIONARY_DISTINCT`` distinct values (a list column by its
    values), by name and, for a list, its values' path too."""
    import pyarrow as pa
    import pyarrow.compute as pc
    out = []
    for name in table.column_names:
        col = table.column(name)
        nested = pa.types.is_list(col.type) or pa.types.is_large_list(col.type)
        values = pc.list_flatten(col) if nested else col
        if pc.count_distinct(values).as_py() <= DICTIONARY_DISTINCT:
            out += [name, f"{name}.list.element"] if nested else [name]
    return out


def paged(logical: str, src: Path, cache: Path) -> Path:
    """The copy of ``src`` with a page index (``logical`` must have a rule), written into ``cache`` when
    missing or stale; returns its path."""
    import pyarrow.parquet as pq
    page, group, dictionary = rule(logical)
    dst = Path(cache) / logical
    stamp = dst.with_name(dst.name + ".json")
    st = Path(src).stat()
    key = {"layout": LAYOUT, "page": page, "group": group, "dictionary": dictionary, "level": LEVEL,
           "source_bytes": st.st_size, "source_mtime_ns": st.st_mtime_ns}
    if dictionary:
        key["dictionary_distinct"] = DICTIONARY_DISTINCT
    if dst.exists() and stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == key:
        return dst
    f = pq.ParquetFile(src)
    md = f.metadata
    groups = group or max((md.row_group(g).num_rows for g in range(md.num_row_groups)), default=1)
    codec = md.row_group(0).column(0).compression.lower() if md.num_row_groups else "zstd"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    table = f.read()
    # dictionaries only where asked, for the columns of few values, and small (a page read needs its
    # column's dictionary page too)
    pq.write_table(table, tmp, row_group_size=max(1, groups), compression=codec if codec != "uncompressed" else "zstd",
                   compression_level=LEVEL if codec in ("zstd", "uncompressed") else None, write_statistics=True,
                   use_dictionary=dictionary_columns(table) if dictionary else False,
                   dictionary_pagesize_limit=DICTIONARY_LIMIT, write_page_index=True, max_rows_per_page=page)
    tmp.replace(dst)
    stamp.write_text(json.dumps(key), encoding="utf-8")
    return dst
