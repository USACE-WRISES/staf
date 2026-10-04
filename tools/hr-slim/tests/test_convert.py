"""End-to-end conversion of a tiny fake package (a local GeoPackage standing in
for the zipped geodatabase on S3), with CamelCase field names like the older
USGS packages."""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pyogrio
import pytest
import shapely

from hrbuild import convert, manifest, report, source
from hrslim import Dataset, fmt

X0, Y0, D = -77.0, 39.0, 0.01


@pytest.fixture()
def fake_package(tmp_path, monkeypatch):
    gpkg = tmp_path / "pkg.gpkg"
    ids = [10000400000001.0, 10000400000002.0, 10000400000003.0]
    lines = gpd.GeoDataFrame({
        "NHDPlusID": ids + [10000400000099.0],
        "GNIS_Name": ["Outlet Run", "  ", None, "Canal"],
        "ReachCode": ["02060005000001", "02060005000002", "02060005000003", "02060005000099"],
        "LengthKM": [1.0, 1.1, 1.2, 1.3],
        "FType": [460, 460, 460, 336],
        "FCode": [46006, 46006, 46003, 33600],
        "InNetwork": [1, 1, 1, 0],
        "VPUID": ["0206"] * 4,
    }, geometry=[
        shapely.LineString([(X0 + 0.005, Y0 + 0.005), (X0 + 0.005, Y0)]),
        shapely.LineString([(X0 + 0.005, Y0 + 0.015), (X0 + 0.005123456, Y0 + 0.012), (X0 + 0.005, Y0 + 0.01)]),
        shapely.LineString([(X0 + 0.015, Y0 + 0.005), (X0 + 0.01, Y0 + 0.005)]),
        shapely.LineString([(X0 + 0.03, Y0 + 0.03), (X0 + 0.04, Y0 + 0.03)]),
    ], crs=4269)
    pyogrio.write_dataframe(lines, gpkg, layer="NHDFlowline")
    vaa = pd.DataFrame({"NHDPlusID": ids, "StreamOrde": [2, 1, 1],
                        "HydroSeq": [10000400000010.0, 10000400000011.0, 10000400000012.0],
                        "UpHydroSeq": [10000400000011.0, 0.0, 0.0],
                        "DnHydroSeq": [0.0, 10000400000010.0, 10000400000010.0],
                        "TotDASqKm": [3.0, 1.0, 1.0], "Slope": [0.001, -9998.0, 0.002]})
    pyogrio.write_dataframe(vaa, gpkg, layer="NHDPlusFlowlineVAA")
    pyogrio.write_dataframe(pd.DataFrame({"NHDPlusID": ids[:2], "QAMA": [2.5, 0.8]}), gpkg,
                            layer="NHDPlusEROMMA")
    cells = [shapely.box(X0, Y0, X0 + D, Y0 + D), shapely.box(X0, Y0 + D, X0 + D, Y0 + 2 * D),
             shapely.box(X0 + D, Y0, X0 + 2 * D, Y0 + D), shapely.box(X0 + D, Y0 + D, X0 + 2 * D, Y0 + 2 * D)]
    cats = gpd.GeoDataFrame({"NHDPlusID": ids + [20000400000001.0]}, geometry=cells, crs=4269)
    pyogrio.write_dataframe(cats, gpkg, layer="NHDPlusCatchment")

    pkg = {"vpu": "0206", "unit": "HU4", "date": None, "name": "NHDPLUS_H_0206_HU4_GDB.zip",
           "key": "test/NHDPLUS_H_0206_HU4_GDB.zip", "url": "file://test", "bytes": 1234,
           "modified": "2026-10-01T00:00:00Z"}
    monkeypatch.setattr(source, "package", lambda vpu, root: pkg)
    monkeypatch.setattr(source, "gdb_path", lambda p: str(gpkg))
    return tmp_path / "root"


def test_convert_writes_a_readable_dataset(fake_package):
    root = fake_package
    part = convert.convert_vpu("0206", root, line_tol=5.0, cat_tols=(10.0, 20.0), qa_boxes=1,
                               log=lambda msg: None)
    entry, stats = part["entry"], part["stats"]
    assert stats["counts"]["network_lines"] == 3
    assert stats["counts"]["catchments_all"] == 4 and stats["counts"]["network_catchments"] == 3
    assert entry["id_range"] == [10000400000001, 10000400000003]
    assert entry["hydroseq_range"] == [10000400000010, 10000400000012]
    assert entry["dnhydroseq_range"] == [10000400000010, 10000400000010]
    assert set(entry["catchments"]) == {"10", "20"}
    assert entry["qa"]["rows"] >= 1

    m = manifest.build(root)
    assert list(m["vpus"]) == ["0206"]
    ds = Dataset(root / "data")
    t = pq.read_table(root / "data" / fmt.lines_file("0206"))
    assert sorted(t.column("nhdplusid").to_pylist()) == [10000400000001, 10000400000002, 10000400000003]
    names = dict(zip(t.column("nhdplusid").to_pylist(), t.column("gnis_name").to_pylist()))
    assert names[10000400000002] is None and names[10000400000001] == "Outlet Run"
    slopes = dict(zip(t.column("nhdplusid").to_pylist(), t.column("slope").to_pylist()))
    assert slopes[10000400000002] == -9998.0
    qama = dict(zip(t.column("nhdplusid").to_pylist(), t.column("qama").to_pylist()))
    assert qama[10000400000003] is None

    tree = ds.upstream_tree(10000400000001)
    assert tree["status"] == "ok" and tree["nReaches"] == 3
    ws = ds.watershed(10000400000001, 10)
    assert ws["status"] == "ok" and ws["nCatchments"] == 3

    # rounding moves vertices by at most half a grid step
    reach = ds.reach(nhdplusid=10000400000002)
    xy = shapely.get_coordinates(fmt.decode_lines(reach))
    assert np.abs(xy[0] - [X0 + 0.005, Y0 + 0.015]).max() <= 0.5e-5 + 1e-12


def test_report_and_pack(fake_package, tmp_path):
    root = fake_package
    convert.convert_vpu("0206", root, line_tol=5.0, cat_tols=(10.0, 20.0), qa_boxes=1,
                        log=lambda msg: None)
    manifest.build(root)
    text = report.report(root)
    assert "National estimate" in text and "0206" in text
    dest = tmp_path / "packed"
    res = manifest.pack(root, dest, tolerance=20.0)
    packed = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert packed["recipe"]["catchment_tolerances_m"] == [20.0]
    assert list(packed["vpus"]["0206"]["catchments"]) == ["20"]
    assert not (dest / fmt.catchments_file("0206", 10.0)).exists()
    assert res["bytes"] > 0


@pytest.mark.parametrize("name, vpu", [
    ("NHDPLUS_H_0710_HU4_GDB.zip", "0710"),
    ("NHDPLUS_H_0108_HU4_20220324_GDB.zip", "0108"),
    ("NHDPLUS_H_0418i_HU4_GDB.zip", "0418i"),
    ("NHDPLUS_H_19020401_HU8_GDB.zip", "19020401"),
])
def test_package_names(name, vpu):
    m = source._NAME.match(name)
    assert m and m.group(1) == vpu


def test_gdb_folder_follows_the_zip_name():
    pkg = {"url": "https://x/NHDPLUS_H_0108_HU4_20220324_GDB.zip", "name": "NHDPLUS_H_0108_HU4_20220324_GDB.zip"}
    assert source.gdb_path(pkg).endswith("/NHDPLUS_H_0108_HU4_20220324_GDB.zip/NHDPLUS_H_0108_HU4_20220324_GDB.gdb")
