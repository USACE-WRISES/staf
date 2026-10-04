"""The ``ensure`` hooks a delivered bundle uses (the site engine fetches a region or a national
table the first time it is read): the reader asks for a region before it opens the region's files
and only then, the value tables likewise, the point tables for each table; a hook that fails makes
the read fail (the engine then asks the service)."""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import fixture2 as fx
from hrslim import points
from hrslim.reader2 import Dataset2
from hrslim.values import ValueTables


@pytest.fixture()
def root(tmp_path):
    folder = fx.build(tmp_path / "data")
    fx.write_lookups(folder)
    return folder


def test_a_region_is_ensured_before_it_is_read_and_only_once(root):
    asked = []
    ds = Dataset2(root, ensure=asked.append)
    assert asked == []                                             # opening reads the manifest only
    west, south, east, north = fx.box_deg(0, 0, 100, 100)
    assert ds.lines_in_bbox(west, south, east, north) is not None
    assert asked == ["9901"]
    ws = ds.watershed(fx.ID[1])                                    # the walk crosses into 9902
    assert ws["status"] == "ok" and ws["areaSqkm"] == pytest.approx(5.0, abs=1e-6)
    assert asked == ["9901", "9902"]
    ds.watershed(fx.ID[1])
    assert asked == ["9901", "9902"]


def test_a_failing_hook_fails_the_read_and_is_asked_again(root):
    calls = []

    def refuse(vpu):
        calls.append(vpu)
        raise OSError("not available")
    ds = Dataset2(root, ensure=refuse)
    with pytest.raises(OSError):
        ds.reach(nhdplusid=fx.ID[1])
    with pytest.raises(OSError):
        ds.reach(nhdplusid=fx.ID[1])
    assert calls == ["9901", "9901"]


def test_value_tables_ensure_their_region(root):
    asked = []
    tables = ValueTables(root / "values", ensure=asked.append)
    assert tables.present("extras", "9901") and not tables.present("lc", "9901")
    assert tables._table("extras", "9902").num_rows == 1
    assert asked == ["9901", "9902"]

    def refuse(vpu):
        raise OSError("not available")
    assert not ValueTables(root / "values", ensure=refuse).present("extras", "9901")


def test_point_tables_ensure_each_table(tmp_path):
    folder = tmp_path / "tables"
    folder.mkdir()
    pq.write_table(pa.table({"name": ["Dam"], "lat": [40.0], "lon": [-100.0], "nid_id": ["X1"],
                             "normal_storage": [10.0], "nid_storage": [12.0]}), folder / "nid_points.parquet")
    asked = []
    tables = points.PointTables(folder, ensure=asked.append)
    assert tables.available() and asked == []
    assert tables._path("nid_points.parquet").exists() and asked == ["nid_points.parquet"]
