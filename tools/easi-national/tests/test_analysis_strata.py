"""The analysis ``strata`` step: the Level III to II to I crosswalk from the
NRSA site files, and the national strata table (anchor point, regions with
the nearest fallback, HUC12, state, classes)."""
from __future__ import annotations

import csv
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

from builder import state
from builder.analysis import strata
from builder.paths import DataRoot
from test_states_national import write_comid_states


def _geojson(path, features, gz=False):
    fc = {"type": "FeatureCollection", "features": features}
    if gz:
        import gzip
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(fc, handle)
    else:
        path.write_text(json.dumps(fc), encoding="utf-8")
    return path


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _feature(props, geometry):
    return {"type": "Feature", "properties": props, "geometry": geometry}


def _site_csv(path, rows, bom=False, extra_cols=()):
    header = ["SITE_ID", "US_L3CODE", "US_L3NAME", "NA_L2CODE", "NA_L2NAME", "NA_L1CODE", "NA_L1NAME", *extra_cols]
    import io
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    data = buffer.getvalue().encode("latin-1")
    if bom:
        data = b"\xef\xbb\xbf" + data          # the UTF-8 byte-order mark EPA's 2018-19 file carries
    path.write_bytes(data)
    return path


def test_crosswalk_takes_the_mode_per_level_iii_and_flags_low_shares(tmp_path):
    a = _site_csv(tmp_path / "a.csv", [
        ["s1", "1.0", "One", "8.1", "Mixed Wood", "8", "Eastern Temperate Forests"],
        ["s2", "1", "One", "8.1", "Mixed Wood", "8", "Eastern Temperate Forests"],
        ["s3", "1", "One", "8.2", "Central Plains", "8", "Eastern Temperate Forests"],
        ["s4", "1", "One", "8.2", "Central Plains", "8", "Eastern Temperate Forests"],
        ["s5", "2", "Two", "9.4", "South Central Semi-Arid Prairies", "", ""],
    ])
    b = _site_csv(tmp_path / "b.csv", [["s6", "2", "Two", "9.4", "South Central Semi-Arid Prairies", "9", "Great Plains"]],
                  bom=True)
    rows = strata.build_crosswalk([a, b], l3_codes=["1", "2"])
    by = {r["l3"]: r for r in rows}
    assert list(by) == ["1", "2"]
    assert by["1"]["l2"] == "8.1" and by["1"]["l1"] == "8" and by["1"]["n_rows"] == 4
    assert by["1"]["mode_share_l2"] == 0.5 and by["1"]["needs_check"] is True
    assert by["1"]["minority_l2"] == "8.2:2"
    assert by["2"]["l2"] == "9.4" and by["2"]["l1"] == "9"          # the missing L1 falls back to the L2 prefix
    assert by["2"]["mode_share_l2"] == 1.0 and by["2"]["needs_check"] is False
    assert by["2"]["l1_name"] == "Great Plains"
    with pytest.raises(ValueError, match="Level III codes"):
        strata.build_crosswalk([a, b], l3_codes=["1", "2", "3"])
    out = strata.write_crosswalk(rows, tmp_path / "x.csv")
    assert strata.read_crosswalk(out)["1"]["l2"] == "8.1"


def test_norm_code_and_classes():
    assert strata.norm_code("45.0", "l3") == "45" and strata.norm_code(" 8.3 ", "l2") == "8.3"
    assert strata.norm_code("NA", "l1") is None and strata.norm_code(None, "l3") is None
    assert strata.classify([0.001, 0.005, 0.03, float("nan")], strata.SLOPE_BREAKS, strata.SLOPE_LABELS).tolist() == [
        "lt_0.5", "0.5_to_2", "ge_2", None]
    assert strata.classify([5, 10, 50, 100.0, 500], strata.DA_BREAKS, strata.DA_LABELS, right=True).tolist() == [
        "le_10", "le_10", "10_to_100", "10_to_100", "gt_100"]
    assert strata.fcode_classes([46006, 33600, 12345, None]).tolist() == ["perennial", "canal", "other", None]


def test_strata_table_anchors_regions_huc12_state_and_classes(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    l3 = _geojson(tmp_path / "l3.geojson", [
        _feature({"US_L3CODE": 1, "US_L3NAME": "One"}, _square(-90, 40, -89, 41)),
        _feature({"US_L3CODE": "2", "US_L3NAME": "Two"}, _square(-88, 40, -87, 41))])
    nars9 = _geojson(tmp_path / "n9.geojson.gz", [_feature({"WSA_9": "AA"}, _square(-91, 39, -86, 42))], gz=True)
    physio = _geojson(tmp_path / "physio.geojson", [_feature({"DIVISION": "Interior Plains"}, _square(-90, 40, -89, 41))])
    huc12 = tmp_path / "huc12.parquet"
    pq.write_table(pa.table({"huc_12": ["120000000001", "120000000002"],
                             "geometry": pa.array([shapely.to_wkb(shapely.box(-90, 40, -89, 41)),
                                                   shapely.to_wkb(shapely.box(-88, 40, -87, 41))], pa.binary())}), huc12)
    lines = {
        30: (shapely.LineString([(-89.8, 40.5), (-89.2, 40.5)]), "With Digitized", 46006, 1, 0.001, 5.0, "02080204000001"),
        10: (shapely.LineString([(-87.8, 40.5), (-87.2, 40.5)]), "Against Digitized", 46003, 6, 0.01, 50.0, "05010001000002"),
        20: (shapely.LineString([(-85.5, 40.5), (-85.4, 40.5)]), "With Digitized", 33600, 2, 0.03, 500.0, "05010001000003"),
        40: (shapely.MultiLineString([[(-89.9, 40.2), (-89.7, 40.2)], [(-89.7, 40.2), (-89.5, 40.3)]]),
             None, 55800, 3, -9998.0, None, None),
    }
    flowlines = tmp_path / "flowlines.parquet"
    pq.write_table(pa.table({
        "comid": pa.array(list(lines), pa.int64()),
        "geometry": pa.array([shapely.to_wkb(v[0]) for v in lines.values()], pa.binary()),
        "flowdir": pa.array([v[1] for v in lines.values()], pa.string()),
        "fcode": pa.array([v[2] for v in lines.values()], pa.int64()),
        "streamorde": pa.array([v[3] for v in lines.values()], pa.int64()),
        "slope": pa.array([v[4] for v in lines.values()], pa.float64()),
        "totdasqkm": pa.array([v[5] for v in lines.values()], pa.float64()),
        "lengthkm": pa.array([1.0, 2.0, 3.0, 4.0], pa.float64()),
        "reachcode": pa.array([v[6] for v in lines.values()], pa.string()),
    }), flowlines)
    write_comid_states(root, [(30, "AA"), (10, "BB"), (20, "AA")])
    sites = [_site_csv(tmp_path / "sites.csv", [["s", "1", "One", "8.1", "Mixed", "8", "ETF"],
                                                 ["t", "2", "Two", "9.4", "Prairie", "9", "GP"]])]
    progress = state.Progress(root, quiet=True)
    path = strata.run_strata(root, progress, state.Control(root), batch=3, flowlines=flowlines, l3_path=l3,
                             nars9_path=nars9, physio_path=physio, huc12=huc12, sites=sites, parity=True)
    table = pq.read_table(path).to_pylist()
    rows = {r["comid"]: r for r in table}
    assert [r["comid"] for r in table] == [10, 20, 30, 40]                       # sorted across two batches
    # anchor 10 m upstream of the downstream node: near the digitised end, or the start when "Against"
    assert abs(rows[30]["lon"] - (-89.2)) < 0.001 and rows[30]["lon"] < -89.2 and abs(rows[30]["lat"] - 40.5) < 1e-6
    assert abs(rows[10]["lon"] - (-87.8)) < 0.001 and rows[10]["lon"] > -87.8
    assert rows[30]["sinuosity_flowline"] == 1.0 and rows[40]["sinuosity_flowline"] > 1.0
    assert rows[30]["l3"] == "1" and rows[10]["l3"] == "2" and rows[40]["l3"] == "1"
    assert rows[20]["l3"] == "2" and rows[20]["region_imputed"] is True            # outside both: nearest
    assert rows[30]["region_imputed"] is False
    assert rows[30]["l2"] == "8.1" and rows[30]["l1"] == "8" and rows[10]["l2"] == "9.4" and rows[10]["l1"] == "9"
    assert all(r["nars9"] == "AA" for r in table)
    assert rows[30]["physio"] == "IPL" and rows[10]["physio"] == "IPL"           # Interior Plains; 10 by nearest
    assert rows[30]["huc12"] == "120000000001" and rows[10]["huc12"] == "120000000002"
    assert rows[30]["huc12_imputed"] is False and rows[20]["huc12_imputed"] is True
    assert rows[30]["state"] == "AA" and rows[10]["state"] == "BB" and rows[40]["state"] is None
    assert rows[30]["huc8"] == "02080204" and rows[40]["huc8"] is None
    assert [rows[c]["slope_class"] for c in (30, 10, 20, 40)] == ["lt_0.5", "0.5_to_2", "ge_2", None]
    assert rows[40]["slope"] is None                                             # -9998 is unknown
    assert [rows[c]["slope_class_er"] for c in (30, 10, 20)] == ["lt_2", "lt_2", "2_to_4"]
    assert [rows[c]["da_class"] for c in (30, 10, 20, 40)] == ["le_10", "10_to_100", "gt_100", None]
    assert [rows[c]["fcode_class"] for c in (30, 10, 20, 40)] == ["perennial", "intermittent", "canal", "artificial_path"]
    assert [rows[c]["wadeable"] for c in (30, 10, 20, 40)] == [True, False, True, True]
    assert strata.read_crosswalk(strata.crosswalk_path(root))["2"]["l1"] == "9"
    assert json.loads(strata.parity_path(root).read_text())["huc8s"] == 0
