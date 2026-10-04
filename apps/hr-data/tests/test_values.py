"""Watershed values from precomputed tables (hrslim.values) on the version 2 fixture.

Every fixture catchment is 1 km2 (10,000 cells). Region 9901 holds reaches 1, 2, 3, 4, 6 (catchment
and line rows in that order), region 9902 holds reach 5; the walk from reach 1 is
{1} -> {2, 3} -> {4} -> {5}, so reach 2 sits one level above reach 1.
"""
import importlib
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import fixture2
from hrslim.values import LC_COLUMNS, ValueTables, flowline_extras, watershed_values

ID = fixture2.ID
CELLS = 10_000
#: reach -> NLCD class filling its catchment
COVER = {1: 82, 2: 41, 3: 81, 4: 71, 5: 90, 6: 11}


def _lc_row(code, cells=CELLS):
    row = dict((c, 0) for c in LC_COLUMNS)
    row[f"lc{code}"] = cells
    return row


def _write(folder, name, cols):
    pq.write_table(pa.table(cols), folder / name)


@pytest.fixture(scope="module")
def values_dir(data_dir2, tmp_path_factory):
    folder = tmp_path_factory.mktemp("values")
    for vpu, reaches in fixture2.REGIONS.items():
        rows = [_lc_row(COVER[n]) for n in reaches]
        cols = dict((c, pa.array([r[c] for r in rows], type=pa.int32())) for c in LC_COLUMNS)
        imp = [20 * CELLS if n == 4 else 0 for n in reaches]              # reach 4: 20 percent impervious
        cols.update({"imp21_sum": pa.array(imp, type=pa.int64()), "imp21_n": pa.array([CELLS] * len(reaches)),
                     "imp01_sum": pa.array([0] * len(reaches), type=pa.int64()),
                     "imp01_n": pa.array([CELLS] * len(reaches))})
        _write(folder, f"lc2_{vpu}.parquet", cols)
        _write(folder, f"roads2_{vpu}.parquet", {"road_m": pa.array([1000.0 * (i + 1) for i in range(len(reaches))],
                                                                     type=pa.float32())})
        _write(folder, f"dams2_{vpu}.parquet", {
            "dams": pa.array([1 if n == 3 else 0 for n in reaches], type=pa.int16()),
            "normal_acft": pa.array([50.0 if n == 3 else 0.0 for n in reaches]),
            "normal_missing": pa.array([0] * len(reaches), type=pa.int16()),
            "nid_acft": pa.array([80.0 if n == 3 else 0.0 for n in reaches])})
        _write(folder, f"soils2_{vpu}.parquet", {
            "k_sum": pa.array([0.3 * CELLS] * len(reaches)), "k_cells": pa.array([CELLS] * len(reaches)),
            "ssurgo_cells": pa.array([CELLS] * len(reaches)), "statsgo_cells": pa.array([0] * len(reaches))})
    # riparian pieces in 9901: reach 2 (row 1) has its own strip (k 0, forest) and a piece of the
    # outlet reach's buffer (k 1, crop), which counts once the outlet is a level below reach 2
    rip = [(1, 0, 41, 300), (1, 1, 82, 100), (0, 0, 82, 200)]
    rcols = {"row": pa.array([r for r, _, _, _ in rip], type=pa.int32()),
             "k": pa.array([k for _, k, _, _ in rip], type=pa.int32())}
    for c in LC_COLUMNS:
        rcols[c] = pa.array([n if c == f"lc{code}" else 0 for _, _, code, n in rip], type=pa.int32())
    rcols.update({"imp21_sum": pa.array([0, 0, 0], type=pa.int64()), "imp21_n": pa.array([300, 100, 200]),
                  "imp01_sum": pa.array([0, 0, 0], type=pa.int64()), "imp01_n": pa.array([300, 100, 200])})
    _write(folder, "rip2_9901.parquet", rcols)
    empty = dict((c, pa.array([], type=pa.int32())) for c in ["row", "k"] + list(LC_COLUMNS))
    empty.update(dict((c, pa.array([], type=pa.int64())) for c in ("imp21_sum", "imp21_n", "imp01_sum", "imp01_n")))
    _write(folder, "rip2_9902.parquet", empty)
    # crossings: one road meets reaches 1 and 2 at their shared point (counted once), another
    # crosses reach 3
    _write(folder, "xings2_9901.parquet", {"line_row": pa.array([0, 1, 2], type=pa.int32()),
                                           "road": pa.array([7, 7, 8], type=pa.int32()),
                                           "x_dm": pa.array([10, 10, 99], type=pa.int32()),
                                           "y_dm": pa.array([20, 20, 99], type=pa.int32())})
    _write(folder, "xings2_9902.parquet", dict((c, pa.array([], type=pa.int32())) for c in
                                               ("line_row", "road", "x_dm", "y_dm")))
    # extras: reach 2 carries an assessment unit at its middle
    n = len(fixture2.REGIONS["9901"])
    ex = {"sinuosity": pa.array([1.0, 1.234, 1.0, 1.0, 1.0], type=pa.float32())}
    for tag in ("05", "50", "95"):
        ex[f"au_exact_{tag}"] = pa.array([0 if (i == 1 and tag == "50") else -1 for i in range(n)], type=pa.int32())
        ex[f"au_near_{tag}"] = pa.array([0 if i == 1 else -1 for i in range(n)], type=pa.int32())
        ex[f"au_near_m_{tag}"] = pa.array([0.0 if i == 1 else None for i in range(n)], type=pa.float32())
        ex[f"huc12_{tag}"] = pa.array([20600050101] * n, type=pa.int64())
    for c in ("nwi_r_m2", "nwi_p_m2", "nwi_l_m2", "nwi_e_m2", "nwi_m_m2", "strip_m2"):
        ex[c] = pa.array([0.0] * n, type=pa.float32())
    _write(folder, "extras2_9901.parquet", ex)
    _write(folder, "au2_9901.parquet", {"layer": pa.array([1]), "assessment_unit": pa.array(["XX-001"]),
                                        "assessment_name": pa.array(["Upper Run"]), "ircategory": pa.array(["5"]),
                                        "overallstatus": pa.array(["Not Supporting"]), "isimpaired": pa.array(["Y"])})
    return folder


def test_watershed_values_sum_the_catchments(dataset2, values_dir):
    v = watershed_values(dataset2, ValueTables(values_dir), ID[1])
    assert v["status"] == "ok" and v["areaSqkm"] == 5.0
    for stem in ("crop", "forest", "hayPasture", "grassland", "woodyWetland"):
        assert v[f"{stem}PctWatershed"] == 20.0
    assert v["imperviousPctWatershed"] == 4.0                          # 20 percent of one catchment in five
    assert v["roadLengthKm"] == 1 + 2 + 3 + 4 + 1 and v["roadDensity"] == 2.2
    assert v["damCount"] == 1 and v["damStorageAcreFt"] == 50.0 and v["damNidStorageAcreFt"] == 80.0
    assert v["roadCrossings"] == 2                                     # the shared point counts once
    assert v["soilKFactor"] == 0.3 and v["soilKCoverage"] == 1.0


def test_riparian_pieces_follow_the_outlet(dataset2, values_dir):
    tables = ValueTables(values_dir)
    at_2 = watershed_values(dataset2, tables, ID[2])                    # reach 2 is the outlet: level 0
    assert at_2["cellsRiparian"] == 300 and at_2["forestPctRiparian"] == 100.0
    at_1 = watershed_values(dataset2, tables, ID[1])                    # reach 2 one level up: k 1 counts
    assert at_1["cellsRiparian"] == 300 + 100 + 200
    assert at_1["cropPctRiparian"] == 50.0 and at_1["forestPctRiparian"] == 50.0


def test_flowline_extras(dataset2, values_dir):
    tables = ValueTables(values_dir)
    mid = flowline_extras(dataset2, tables, ID[2], 0.5)
    assert mid["sinuosity"] == 1.234 and mid["huc12"] == "020600050101"
    assert mid["attains_exact"]["assessment_unit"] == "XX-001" and mid["attains_exact"]["ircategory"] == "5"
    end = flowline_extras(dataset2, tables, ID[2], 0.97)
    assert end["attains_exact"] == {} and end["attains_nearby"]["match_type"] == "nearby"


def test_api_values_and_extras(data_dir2, values_dir, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("HR_DATA_DIR", str(data_dir2))
    mp.setenv("HR_DATA_VALUES", str(values_dir))
    mp.setenv("HR_DATA_JOBS", str(tmp_path_factory.mktemp("jobs_values")))
    import app as app_module
    app_module = importlib.reload(app_module)
    client = app_module.server.test_client()
    try:
        body = json.loads(client.get(f"/api/values?nhdplusid={ID[1]}").get_data())
        assert body["status"] == "ok" and body["cropPctWatershed"] == 20.0 and "ms" in body
        body = json.loads(client.get(f"/api/extras?nhdplusid={ID[2]}&fraction=0.5").get_data())
        assert body["status"] == "ok" and body["attains_exact"]["assessment_unit"] == "XX-001"
        assert client.get("/api/values").status_code == 400
        assert client.get("/api/extras?nhdplusid=1").status_code == 404
    finally:
        app_module._stop_workers()
        mp.undo()


@pytest.fixture(scope="module")
def lean_dir(values_dir, tmp_path_factory):
    """The fixture's tables in the lean encoding (crossings and units copied as they are)."""
    from hrslim import lean
    folder = tmp_path_factory.mktemp("values_lean")
    for path in values_dir.glob("*.parquet"):
        prefix = path.name.split("2_")[0]
        t = pq.read_table(path)
        if prefix in lean.ENCODERS:
            t = lean.ENCODERS[prefix](t)
        pq.write_table(t, folder / path.name)
    return folder


def test_lean_answers_equal_exact(dataset2, values_dir, lean_dir):
    exact, small = ValueTables(values_dir), ValueTables(lean_dir)
    for reach in (ID[1], ID[2]):
        assert watershed_values(dataset2, small, reach) == watershed_values(dataset2, exact, reach)
        for fraction in (0.05, 0.5, 0.97):
            assert flowline_extras(dataset2, small, reach, fraction) == flowline_extras(dataset2, exact, reach, fraction)


def test_lean_keeps_cell_totals():
    from hrslim import lean
    counts = np.array([[3333, 3333, 3334] + [0] * 14, [1, 2, 9997] + [0] * 14, [0] * 16 + [500]])
    cols = dict((c, pa.array(counts[:, i].astype(np.int32))) for i, c in enumerate(LC_COLUMNS))
    cols.update({"imp21_sum": pa.array([0, 3 * 9997, 0]), "imp21_n": pa.array([10000, 10000, 0]),
                 "imp01_sum": pa.array([0, 0, 0]), "imp01_n": pa.array([10000, 10000, 0])})
    got, imp = lean.decode_lc(lean.encode_lc(pa.table(cols)))
    assert np.allclose(got.sum(axis=1), counts.sum(axis=1))
    assert np.all(np.abs(100 * got / counts.sum(axis=1)[:, None] - 100 * counts / counts.sum(axis=1)[:, None]) <= 0.1 + 1e-9)
    assert lean.encode_lc(pa.table(cols)).column("p11").to_pylist() == [333, 0, 0]   # 33.33 33.33 33.34 % -> 333 333 334
    assert imp[2, 1] == 0 and imp[1, 0] / imp[1, 1] == 3.0          # no impervious value; 3 percent
