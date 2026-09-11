"""The local geodatabase readers: ESRI JSON the app can parse, and the three
chunk stages reading the national files instead of the services."""
from __future__ import annotations

import json

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from builder import state
from builder.paths import DataRoot
from builder.stages import attains as attains_stage
from builder.stages import geometry as geometry_stage
from builder.stages import huc12 as huc12_stage
from builder.stages import local_gdb
from builder.units import Chunk


def test_esri_json_matches_the_app_parser():
    from easi.datasources import attains as app_attains
    hole = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (2, 1), (2, 2), (1, 2)]])
    for geom in (Point(1.5, 2.5), LineString([(0, 0), (1, 1), (2, 0)]), hole,
                 MultiPolygon([hole, Polygon([(10, 10), (11, 10), (11, 11)])])):
        parsed = app_attains._shape(local_gdb.esri_json(geom))
        assert parsed is not None and not parsed.is_empty
    assert app_attains._shape(local_gdb.esri_json(hole)).covers(Point(3, 3))
    assert not app_attains._shape(local_gdb.esri_json(hole)).covers(Point(1.5, 1.5))   # inside the hole


def _never(*args, **kwargs):
    raise AssertionError("the service must not be called when the national file exists")


def _root(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    pq.write_table(pa.table({"comid": pa.array([1, 2, 3], pa.int64()), "huc8": ["02080204", "02080204", "02080205"]}),
                   root.vaa)
    chunk = Chunk(id="huc8-test", kind="huc8", label="t", huc8s=["02080204"])
    chunk.save(root)
    return root, chunk, state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)


def test_geometry_stage_reads_the_national_flowlines(tmp_path):
    root, chunk, states, progress, control = _root(tmp_path)
    lines = gpd.GeoDataFrame({
        "comid": [1, 2, 3], "gnis_name": ["A", None, "C"], "reachcode": ["02080204000001"] * 3,
        "lengthkm": [1.0, 2.0, 3.0], "fcode": [46006] * 3, "streamorde": [1, 2, 3], "slope": [0.01, 0.02, 0.03],
        "totdasqkm": [5.0, 6.0, 7.0], "flowdir": ["With Digitized"] * 3},
        geometry=[LineString([(-78.5, 38.0), (-78.4, 38.1)]), LineString([(-78.3, 38.2), (-78.2, 38.3)]),
                  LineString([(-77.0, 37.0), (-76.9, 37.1)])], crs="EPSG:4326")
    local_gdb.write_flowlines(root, lines)
    geometry_stage.run_geometry(root, chunk, states, progress, control, fetch=_never)
    out = gpd.read_parquet(root.chunk_raw("huc8-test", "flowlines"))
    assert out["comid"].tolist() == [1, 2] and list(out.columns[:9]) == list(local_gdb.FLOWLINE_COLUMNS)
    saved = Chunk.load(root, "huc8-test")
    assert saved.bbox and saved.bbox[0] < -78.5 and saved.bbox[2] > -78.2      # buffered around the two lines


def test_huc12_stage_reads_the_national_polygons(tmp_path):
    root, chunk, states, progress, control = _root(tmp_path)
    chunk.bbox = [-79.0, 37.5, -78.0, 38.5]
    chunk.save(root)
    polys = gpd.GeoDataFrame({"huc_12": ["020802040101", "020802040102", "020802050101"],
                              "huc_8": ["02080204", "02080204", "02080205"], "hu_12_name": ["a", "b", "c"],
                              "states": ["VA"] * 3},
                             geometry=[Polygon([(-78.5, 38), (-78.4, 38), (-78.4, 38.1)])] * 3, crs="EPSG:4326")
    local_gdb.write_huc12(root, polys)
    huc12_stage.run_huc12(root, chunk, states, progress, control, fetch=_never)
    out = gpd.read_parquet(root.chunk_raw("huc8-test", "huc12"))
    assert out["huc_12"].tolist() == ["020802040101", "020802040102"]


def test_attains_stage_reads_the_national_segments_by_bbox(tmp_path):
    from easi.datasources import attains as app_attains
    root, chunk, states, progress, control = _root(tmp_path)
    chunk.bbox = [-79.0, 37.5, -78.0, 38.5]
    chunk.save(root)
    lines = gpd.GeoDataFrame({"assessmentunitidentifier": ["VAN-A", "VAN-B"], "assessmentunitname": ["A", "B"],
                              "overallstatus": ["Not Supporting", "Fully Supporting"],
                              "isimpaired": [True, False], "ircategory": ["5", "1"]},
                             geometry=[LineString([(-78.5, 38.0), (-78.4, 38.1)]),
                                       LineString([(-70.0, 45.0), (-69.9, 45.1)])], crs="EPSG:4326")
    rows = local_gdb.attains_rows_from(lines, 1)
    assert rows[0]["isimpaired"] == "Y" and rows[1]["isimpaired"] == "N"
    local_gdb.write_attains_rows(root, rows)
    attains_stage.run_attains(root, chunk, states, progress, control, fetch=_never, count=_never)
    out = pq.read_table(root.chunk_raw("huc8-test", "attains")).to_pylist()
    assert [r["assessment_unit"] for r in out] == ["VAN-A"] and out[0]["layer"] == 1
    geometry = json.loads(out[0]["geometry"])
    assert app_attains._distance_to_geometry_m(38.0, -78.5, geometry) == pytest.approx(0.0, abs=1.0)
