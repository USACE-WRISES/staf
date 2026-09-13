"""The COMID to state table: midpoints inside a state polygon, the nearest
fallback for a midpoint outside every polygon, batching, and the lookups."""
from __future__ import annotations

import gzip
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

from builder import state
from builder.paths import DataRoot
from builder.stages import national
from builder.stages import states as st


def polygons_file(path):
    """Two unit squares, AA at x 0..1 and BB at x 2..3, as the Census file's shape."""
    squares = {"AA": ("Alpha", [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]),
               "BB": ("Beta", [[2, 0], [3, 0], [3, 1], [2, 1], [2, 0]])}
    features = [{"type": "Feature", "properties": {"state": abbr, "name": name, "fips": "00"},
                 "geometry": {"type": "Polygon", "coordinates": [ring]}}
                for abbr, (name, ring) in squares.items()]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle)
    return path


def write_comid_states(root, pairs):
    table = pa.table({"comid": pa.array([c for c, _ in pairs], pa.int64()),
                      "state": pa.array([s for _, s in pairs], pa.string()),
                      "by_nearest": pa.array([False] * len(pairs), pa.bool_())})
    pq.write_table(table, st.comid_state_path(root))


def _flowlines(path, lines):
    table = pa.table({"comid": pa.array([c for c, _ in lines], pa.int64()),
                      "geometry": pa.array([shapely.to_wkb(shapely.LineString(coords)) for _, coords in lines],
                                           pa.binary())})
    pq.write_table(table, path)
    return path


def test_midpoints_pick_the_containing_state_and_fall_back_to_the_nearest(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    polygons = polygons_file(tmp_path / "states.geojson.gz")
    lines = [(30, [[0.2, 0.5], [0.8, 0.5]]),            # midpoint (0.5, 0.5): inside AA
             (10, [[2.1, 0.2], [2.9, 0.8]]),            # (2.5, 0.5): inside BB
             (20, [[1.1, 0.5], [1.3, 0.5]]),            # (1.2, 0.5): outside both, nearest AA
             (40, [[0.0, 2.5], [1.0, 2.5]])]            # (0.5, 2.5): outside, nearest AA
    st.run_states(root, state.Progress(root, quiet=True), polygons_path=polygons,
                  flowlines=_flowlines(root.national / "flowlines.parquet", lines), batch=3)
    table = pq.read_table(st.comid_state_path(root))
    assert table.column("comid").to_pylist() == [10, 20, 30, 40]                 # sorted, across two batches
    assert table.column("state").to_pylist() == ["BB", "AA", "AA", "AA"]
    assert table.column("by_nearest").to_pylist() == [False, True, False, True]
    index = st.load_comid_states(root)
    assert st.states_of(index, [30, 99, 10]).tolist() == ["AA", None, "BB"]
    assert st.state_names(polygons) == {"AA": "Alpha", "BB": "Beta"}


def test_the_step_needs_the_flowlines_and_has_its_own_input_digest(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    with pytest.raises(RuntimeError, match="flowlines"):
        st.run_states(root, state.Progress(root, quiet=True), polygons_path=polygons_file(tmp_path / "s.gz"))
    inputs = national._inputs_for("states", root)
    assert inputs and inputs != national._stage_inputs("states")
    assert ("states", "COMID to state (flowline midpoints in the Census state polygons)") in [
        (row[0], row[1]) for row in __import__("builder.inventory", fromlist=["NATIONAL_STEPS"]).NATIONAL_STEPS]
