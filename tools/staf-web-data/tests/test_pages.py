"""The files written again with a page index hold their source's rows unchanged (webdata/pages.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webdata import pages  # noqa: E402


def _source(path: Path, n: int = 20000, group: int = 16384) -> pa.Table:
    rng = np.random.default_rng(5)
    t = pa.table({"nhdplusid": np.sort(rng.integers(10 ** 13, 10 ** 13 + 10 ** 6, n)).astype(np.int64),
                  "k": rng.integers(0, 60, n).astype(np.uint16), "er": rng.integers(0, 900, n).astype(np.uint32),
                  "division": rng.choice(["Appalachian Highlands", "Atlantic Plain", None], n),
                  "z": [list(rng.integers(-5, 5, 7).astype(np.int32)) for _ in range(n)]},
                 metadata={"note": "kept"})
    pq.write_table(t, path, row_group_size=group, compression="zstd")
    return t


def test_rules():
    assert pages.rule("bundle/v2/streamcat2_0108.parquet") == (256, None, False)
    assert pages.rule("xs/xs-0108.parquet") == (1024, 65536, True)
    assert pages.rule("xs/xs-0108-sections.parquet") == (512, 65536, True)
    assert pages.rule("xs/xs-0108-median.parquet") == (32, 8192, True)
    assert pages.rule("bundle/lines2_0108.parquet") == (128, 16384, False)
    assert pages.rule("bundle/v2/lines2_0108.parquet") == (128, 16384, False)
    assert pages.rule("bundle/cats2_0108.parquet") is None
    assert pages.rule("bundle/values/lc2_0108.parquet") == (1024, None, False)
    assert pages.rule("bundle/values/xings2_0108.parquet") == (1024, None, False)
    assert pages.rule("bundle/values/dams2_0108.parquet") is None
    assert pages.rule("bundle/values/au2_0108.parquet") is None


def test_same_rows_with_a_page_index(tmp_path):
    src = tmp_path / "xs-0101.parquet"
    orig = _source(src)
    out = pages.paged("xs/xs-0101.parquet", src, tmp_path / "cache")
    got = pq.read_table(out)
    assert got.equals(orig)                                    # rows, order, types, values
    assert got.schema.metadata[b"note"] == b"kept"
    md = pq.ParquetFile(out).metadata
    assert md.num_row_groups == 1                              # 20,000 rows in groups of 65,536
    for c in range(md.row_group(0).num_columns):
        col = md.row_group(0).column(c)
        assert col.has_offset_index and col.has_column_index


def test_no_dictionaries_where_the_rule_says(tmp_path):
    src = tmp_path / "streamcat2_0101.parquet"
    orig = _source(src)
    out = pages.paged("bundle/v2/streamcat2_0101.parquet", src, tmp_path / "cache")
    assert pq.read_table(out).equals(orig)
    md = pq.ParquetFile(out).metadata
    assert md.num_row_groups == 2                              # the source's groups kept (16,384 rows)
    for c in range(md.row_group(0).num_columns):
        assert md.row_group(0).column(c).dictionary_page_offset is None   # no dictionary page to read with each page


def test_written_again_only_when_stale(tmp_path):
    src = tmp_path / "xs-0101.parquet"
    _source(src)
    out = pages.paged("xs/xs-0101.parquet", src, tmp_path / "cache")
    first = out.stat().st_mtime_ns
    assert pages.paged("xs/xs-0101.parquet", src, tmp_path / "cache").stat().st_mtime_ns == first
    _source(src, n=20001)                                      # the source changed
    out = pages.paged("xs/xs-0101.parquet", src, tmp_path / "cache")
    assert pq.ParquetFile(out).metadata.num_rows == 20001


def test_dictionaries_only_for_columns_of_few_values(tmp_path):
    src = tmp_path / "xs-0101-sections.parquet"
    n = 5000
    rng = np.random.default_rng(9)
    t = pa.table({"nhdplusid": np.sort(rng.integers(10 ** 13, 10 ** 13 + 10 ** 9, n)).astype(np.int64),
                  "division": rng.choice(["Appalachian Highlands", "Atlantic Plain"], n),
                  "thalweg": rng.uniform(0, 500, n).astype(np.float32),
                  "grid_epsg": [[26918, 26919][: 1 + i % 2] for i in range(n)]})
    pq.write_table(t, src, compression="zstd")
    assert pages.dictionary_columns(t) == ["division", "grid_epsg", "grid_epsg.list.element"]
    out = pages.paged("xs/xs-0101-sections.parquet", src, tmp_path / "cache")
    rg = pq.ParquetFile(out).metadata.row_group(0)
    has = dict((rg.column(i).path_in_schema, rg.column(i).has_dictionary_page) for i in range(rg.num_columns))
    assert has == {"nhdplusid": False, "division": True, "thalweg": False, "grid_epsg.list.element": True}
    assert pq.read_table(out).equals(t)
