import json

import pytest
import shapely

import gdalpoc


def test_parse_log_counts_range_requests_per_step():
    log = "\n".join([
        "VSICURL: GetFileSize(https://x/a.zip)=1000  response_code=200",
        "VSICURL: Downloading 900-999 (https://x/a.zip)...",
        "VSICURL: Got response_code=206",
        gdalpoc.STEP_MARK + json.dumps({"label": "open", "seconds": 1.5}),
        "OpenFileGDB: FileGDB v10 or later",
        "VSICURL: Downloading 0-16383 (https://x/a.zip)...",
        "VSICURL: Downloading 16384-81919 (https://x/a.zip)...",
        gdalpoc.STEP_MARK + json.dumps({"label": "click 1: flowlines in the box", "seconds": 9.0, "features": 7}),
        gdalpoc.STEP_MARK + json.dumps({"label": "click 1: catchment by NHDPlusID", "seconds": 0.5}),
    ])
    steps = gdalpoc.parse_log(log)
    assert [s["label"] for s in steps] == ["open", "click 1: flowlines in the box", "click 1: catchment by NHDPlusID"]
    assert (steps[0]["requests"], steps[0]["bytes"]) == (1, 100)
    assert (steps[1]["requests"], steps[1]["bytes"]) == (2, 16384 + 65536)
    assert steps[1]["features"] == 7
    assert (steps[2]["requests"], steps[2]["bytes"]) == (0, 0)


@pytest.mark.parametrize("name, vpu", [
    ("NHDPLUS_H_0710_HU4_GDB.zip", "0710"),
    ("NHDPLUS_H_0108_HU4_20220324_GDB.zip", "0108"),
    ("NHDPLUS_H_0418i_HU4_GDB.zip", "0418i"),
    ("NHDPLUS_H_19020401_HU8_GDB.zip", "19020401"),
])
def test_package_names(name, vpu):
    assert gdalpoc._NAME.match(name).group(1) == vpu


def test_remote_path_keeps_the_dated_folder():
    pkg = {"name": "NHDPLUS_H_0108_HU4_20220324_GDB.zip", "url": "https://x/NHDPLUS_H_0108_HU4_20220324_GDB.zip"}
    assert gdalpoc.remote_path(pkg) == ("/vsizip//vsicurl/https://x/NHDPLUS_H_0108_HU4_20220324_GDB.zip"
                                        "/NHDPLUS_H_0108_HU4_20220324_GDB.gdb")


@pytest.fixture()
def tiny_index(tmp_path):
    feats = [{"type": "Feature", "properties": {"vpu": code},
              "geometry": json.loads(shapely.to_geojson(shapely.box(*box)))}
             for code, box in [("0101", (0, 0, 1, 1)), ("0102", (1, 0, 2, 1)), ("0102i", (1, 0, 2, 1))]]
    path = tmp_path / "index.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return gdalpoc.load_index(path)


def test_vpu_lookup(tiny_index):
    assert gdalpoc.vpus_at(0.5, 0.5, tiny_index) == ["0101"]
    assert gdalpoc.vpus_at(1.5, 0.5, tiny_index) == ["0102", "0102i"]
    assert gdalpoc.vpus_at(1.0, 0.5, tiny_index) == ["0101", "0102", "0102i"]   # on the shared edge
    assert gdalpoc.vpus_at(2.005, 0.5, tiny_index) == ["0102", "0102i"]        # just outside: nearest
    assert gdalpoc.vpus_at(5.0, 5.0, tiny_index) == []


def test_bundled_index_finds_the_pilot_points():
    index = gdalpoc.load_index()
    assert "0710" in gdalpoc.vpus_at(-93.76691, 41.016806, index)          # White Breast Creek, Iowa
    assert "1402" in gdalpoc.vpus_at(-107.9, 38.9, index)                  # Gunnison basin, Colorado
    assert "19020401" in gdalpoc.vpus_at(*_ak_point(index), index)


def _ak_point(index):
    codes, geoms, _ = index
    p = shapely.point_on_surface(geoms[codes.index("19020401")])
    return p.x, p.y
