"""Regions with no US data (the Canadian units 0416, 0421, 0422, 0432 and 0433: no gNATSGO window,
no ATTAINS unit) write empty soils and extras values instead of failing (national build, 2026-10-03)."""
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import shapely

from hrbuild import extras as extras_mod
from hrbuild import soils as soils_mod


class _Region:
    entry = None
    n = 3
    ids = np.array([11, 12, 13])
    cells = np.array([100, 200, 300])


def test_soils_with_no_soil_window_reads_every_cell_as_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(soils_mod, "_patch_reader", lambda root: None)
    monkeypatch.setattr(soils_mod, "_mukey_chunks", lambda *a, **k: iter(()))
    monkeypatch.setattr(soils_mod, "RegionCells", lambda d, v: _Region())
    monkeypatch.setattr(soils_mod, "statsgo_mukeys", lambda root: np.array([], dtype=np.int64))
    monkeypatch.setattr(soils_mod, "sda_kwfact", lambda mukeys, root, log: {})
    stats = soils_mod.soils(tmp_path / "data", "0432", sources_root=tmp_path, out_dir=tmp_path, log=lambda m: None)
    assert stats["map_units"] == 0 and stats["cells_outside_windows"] == 600
    t = pq.read_table(tmp_path / "soils2_0432.parquet")
    assert t.column("nhdplusid").to_pylist() == [11, 12, 13]
    for c in ("k_sum", "k_cells", "ssurgo_cells", "statsgo_cells"):
        assert t.column(c).to_numpy().tolist() == [0, 0, 0]
    assert pq.read_table(tmp_path / "soilmu2_0432.parquet").num_rows == 0


def test_extras_with_no_assessment_unit_has_none_at_every_sample(tmp_path, monkeypatch):
    lines = np.array([shapely.LineString([(0, 0), (100, 0)]), shapely.LineString([(0, 50), (60, 80), (100, 80)])],
                     dtype=object)
    n = len(lines) * len(extras_mod.SAMPLES)
    no_units = pd.DataFrame({"layer": pd.Series([], dtype=np.int64), "assessment_unit": pd.Series([], dtype=object),
                             "assessment_name": pd.Series([], dtype=object), "ircategory": pd.Series([], dtype=object)})
    monkeypatch.setattr(extras_mod, "RegionCells", lambda d, v: _Region())
    monkeypatch.setattr(extras_mod, "region_paths", lambda d, e: (None, np.array([21, 22])))
    monkeypatch.setattr(extras_mod, "original_lines", lambda e, ids: lines)
    monkeypatch.setattr(extras_mod, "attains_at",
                        lambda pts, box: (np.full(n, -1), np.full(n, -1), np.full(n, np.nan)))
    monkeypatch.setattr(extras_mod, "attains_units", lambda box: (None, no_units, None, None))
    monkeypatch.setattr(extras_mod, "huc12_at", lambda pts: np.zeros(len(pts), dtype=np.int64))
    monkeypatch.setattr(extras_mod, "nwi_strips", lambda lines_m, vpu, log: {"strip_m2": np.full(len(lines_m), 3e4)})
    stats = extras_mod.extras(tmp_path / "data", "0432", out_dir=tmp_path, log=lambda m: None)
    assert stats["units"] == 0 and stats["lines_with_nearby_unit"] == 0
    t = pq.read_table(tmp_path / "extras2_0432.parquet")
    for tag in ("05", "50", "95"):
        assert t.column(f"au_exact_{tag}").to_pylist() == [-1, -1]
        assert t.column(f"au_near_{tag}").to_pylist() == [-1, -1]
    assert pq.read_table(tmp_path / "au2_0432.parquet").num_rows == 0
