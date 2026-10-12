"""Each region's topology on its own holds its lines file's topology columns unchanged (webdata/topo.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webdata import topo  # noqa: E402


def test_names():
    assert topo.logical("lines2_0108.parquet") == "hr_0108.parquet"
    assert topo.logical("v2/lines2_0108.parquet") == "v2_0108.parquet"
    assert topo.logical("cats2_0108.parquet") is None
    assert topo.logical("v2/streamcat2_0108.parquet") is None


def test_same_rows_one_group(tmp_path):
    n = 9000
    rng = np.random.default_rng(2)
    lines = pa.table({"nhdplusid": rng.integers(1, 10 ** 13, n), "gnis_name": ["x"] * n,
                      "hydroseq": rng.integers(1, 10 ** 13, n), "dn_step": rng.integers(-5, 5, n).astype(np.int32),
                      "up_step": rng.integers(-5, 5, n).astype(np.int32), "dn_out": rng.integers(0, 9, n),
                      "up_out": rng.integers(0, 9, n), "totdasqkm": rng.uniform(0, 1000, n),
                      "x": [[1, 2]] * n})
    src = tmp_path / "lines2_0101.parquet"
    pq.write_table(lines, src, row_group_size=4096)
    out = topo.write(src, tmp_path / "cache", "hr_0101.parquet")
    got = pq.read_table(out)
    assert got.column_names == [c for c in topo.COLUMNS if c in lines.column_names]
    assert got.equals(lines.select(got.column_names))
    assert pq.ParquetFile(out).metadata.num_row_groups == 1
