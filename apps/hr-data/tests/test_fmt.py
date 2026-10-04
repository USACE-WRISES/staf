import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

from hrslim import fmt


def _roundtrip_lines(geoms):
    table = pa.table(fmt.encode_lines(geoms))
    return table, fmt.decode_lines(table)


def test_lines_round_trip_to_the_1e5_degree_grid():
    a = shapely.LineString([(-77.123456789, 39.000004), (-77.1, 39.01)])
    b = shapely.MultiLineString([[(-77.0, 39.0), (-77.0, 39.1)], [(-77.0, 39.1), (-76.9, 39.1)]])
    table, out = _roundtrip_lines([a, b])
    assert shapely.get_num_geometries(out).tolist() == [1, 2]
    xy = shapely.get_coordinates(out[0])
    assert xy[0].tolist() == pytest.approx([-77.12346, 39.0], abs=1e-12)
    assert shapely.equals(out[1], b)
    assert table["xmin"].to_pylist()[0] == round(-77.123456789 * 1e5)
    assert table["ymax"].to_pylist()[1] == round(39.1 * 1e5)


def test_polygons_round_trip_with_holes_and_parts():
    shell = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    hole = [(0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.2)]
    p = shapely.Polygon(shell, [hole])
    mp = shapely.MultiPolygon([shapely.box(2, 2, 3, 3), shapely.box(4, 4, 5, 5)])
    table = pa.table(fmt.encode_polygons([p, mp]))
    out = fmt.decode_polygons(table)
    assert shapely.equals(out[0], p) and shapely.equals(out[1], mp)
    geo = fmt.polygon_geojson(table)
    assert geo[0]["type"] == "Polygon" and len(geo[0]["coordinates"]) == 2
    assert geo[1]["type"] == "MultiPolygon" and len(geo[1]["coordinates"]) == 2


def test_geojson_single_part_lines_are_linestrings():
    table, _ = _roundtrip_lines([shapely.LineString([(0, 0), (1, 1)]),
                                 shapely.MultiLineString([[(0, 0), (1, 1)], [(1, 1), (2, 2)]])])
    geo = fmt.line_geojson(table)
    assert geo[0] == {"type": "LineString", "coordinates": [[0.0, 0.0], [1.0, 1.0]]}
    assert geo[1]["type"] == "MultiLineString"


def test_sliced_tables_decode_the_right_rows():
    lines = [shapely.LineString([(i, 0), (i, 1), (i, 2)]) for i in range(5)]
    table = pa.table(fmt.encode_lines(lines))
    part = table.slice(2, 2)
    out = fmt.decode_lines(part)
    assert [shapely.get_coordinates(g)[0][0] for g in out] == [2.0, 3.0]


def test_write_table_uses_delta_coordinates_and_statistics(tmp_path):
    table = pa.table(fmt.encode_lines([shapely.LineString([(0, 0), (1, 1)])] * 10))
    path = tmp_path / "t.parquet"
    fmt.write_table(table, path, 4)
    md = pq.read_metadata(path)
    assert md.num_row_groups == 3
    cols = dict((md.row_group(0).column(j).path_in_schema, md.row_group(0).column(j))
                for j in range(md.row_group(0).num_columns))
    assert "DELTA_BINARY_PACKED" in cols["x.list.element"].encodings
    assert cols["xmin"].statistics.min == 0 and cols["xmax"].statistics.max == 100000


def test_empty_geometries_are_refused():
    with pytest.raises(ValueError):
        fmt.encode_lines([shapely.LineString()])


def test_quantize_rejects_out_of_range():
    with pytest.raises(ValueError):
        fmt.quantize(np.array([[30000.0, 0.0]]))
