"""End-to-end version 2 conversion of two tiny fake packages (local GeoPackages
standing in for the downloaded geodatabases): catchments drawn on the EPSG:5070
10 m grid like the USGS ones, a reach in region 0207 draining into region 0206."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pytest
import shapely
from shapely import affinity

from hrbuild import convert2, manifest, source
from hrslim import Dataset, Dataset2, arcs, catchment_features, fmt2
from hrslim.grid import Grid

GRID = Grid(5070, 10.0, 5.0)
IX0, IY0 = 150_000, 180_000                 # cell origin, about 77 W, 39 N
SIDE = 50                                   # catchment side in cells (500 m)
HS = dict((n, 10000400000010 + n) for n in range(1, 6))
ID = dict((n, 10000400000000 + n) for n in range(1, 6))


def cells(*boxes, holes=()):
    shell = shapely.union_all([shapely.box(IX0 + a * SIDE, IY0 + b * SIDE, IX0 + c * SIDE, IY0 + d * SIDE)
                               for a, b, c, d in boxes])
    for a, b, c, d in holes:
        shell = shell.difference(shapely.box(IX0 + a, IY0 + b, IX0 + c, IY0 + d))
    return shell


def deg(geom):
    return shapely.transform(geom, lambda q: np.column_stack(GRID.to_lonlat(q[:, 0], q[:, 1])))


ISLAND = shapely.box(IX0 + 60, IY0 + 10, IX0 + 80, IY0 + 30)          # fills 3's hole

#: reach -> (region, catchment in cells, downstream hydroseq (0 = outlet), uphydroseq)
REACHES = {
    1: ("0206", cells((0, 0, 1, 1)), 0, HS[2]),
    2: ("0206", cells((0, 1, 1, 2), (1, 1, 2, 3)), HS[1], HS[4]),                 # an L
    3: ("0206", cells((1, 0, 2, 1), holes=[(60, 10, 80, 30)]), HS[1], 0),          # with a hole
    4: ("0207", cells((0, 2, 1, 3)), HS[2], 0),                                   # across the line
    5: ("0206", ISLAND, 0, 0),                                                    # its own outlet
}


def projected_cells(geom):
    def fwd(q):
        x, y = GRID.project(q[:, 0], q[:, 1])
        return np.column_stack([(x - GRID.offset) / GRID.cell, (y - GRID.offset) / GRID.cell])
    return shapely.transform(geom, fwd)


def line(n):
    g = REACHES[n][1]
    c = g.representative_point()
    return deg(shapely.LineString([(c.x, c.y), (c.x, c.y - 5)]))


def write_package(path, region, *, off_grid=False):
    rs = [n for n in REACHES if REACHES[n][0] == region]
    ids = [float(ID[n]) for n in rs]
    lines = gpd.GeoDataFrame({
        "NHDPlusID": [float(ID[n]) for n in rs] + [10000400000099.0],
        "GNIS_Name": [f"Run {n}" for n in rs] + ["Canal"],
        "ReachCode": [f"0206000500{n:04d}" for n in rs] + ["02060005000099"],
        "LengthKM": [0.5] * len(rs) + [1.0],
        "FType": [460] * len(rs) + [336], "FCode": [46006] * len(rs) + [33600],
        "InNetwork": [1] * len(rs) + [0], "VPUID": [region] * (len(rs) + 1),
    }, geometry=[line(n) for n in rs] + [deg(shapely.LineString([(IX0, IY0 - 30), (IX0 + 50, IY0 - 30)]))],
        crs=4269)
    pyogrio.write_dataframe(lines, path, layer="NHDFlowline")
    vaa = pd.DataFrame({"NHDPlusID": ids, "StreamOrde": [1] * len(rs),
                        "HydroSeq": [float(HS[n]) for n in rs],
                        "DnHydroSeq": [float(REACHES[n][2]) for n in rs],
                        "UpHydroSeq": [float(REACHES[n][3]) for n in rs],
                        "TotDASqKm": [0.25] * len(rs), "Slope": [0.001] * len(rs)})
    pyogrio.write_dataframe(vaa, path, layer="NHDPlusFlowlineVAA")
    pyogrio.write_dataframe(pd.DataFrame({"NHDPlusID": ids, "QAMA": [1.0] * len(rs)}), path,
                            layer="NHDPlusEROMMA")
    polys = [REACHES[n][1] for n in rs] + [cells((3, 3, 4, 4))]       # plus an off-network catchment
    geoms = [deg(p) for p in polys]
    if off_grid:
        geoms = [affinity.translate(g, 3e-5, 0) for g in geoms]
    cats = gpd.GeoDataFrame({"NHDPlusID": ids + [20000400000001.0]},
                            geometry=[shapely.MultiPolygon([g]) for g in geoms], crs=4269)
    pyogrio.write_dataframe(cats, path, layer="NHDPlusCatchment")
    return path


@pytest.fixture()
def packages(tmp_path, monkeypatch):
    def pkg(vpu, root):
        return {"vpu": vpu, "unit": "HU4", "date": None, "name": f"NHDPLUS_H_{vpu}_HU4_GDB.zip",
                "key": f"test/NHDPLUS_H_{vpu}_HU4_GDB.zip", "url": "file://test", "bytes": 1234,
                "modified": "2026-10-01T00:00:00Z"}
    monkeypatch.setattr(source, "package", pkg)
    return dict((r, write_package(tmp_path / f"{r}.gpkg", r)) for r in ("0206", "0207")), tmp_path / "root"


def test_convert2_writes_an_exact_dataset(packages):
    gpkgs, root = packages
    quiet = lambda msg: None
    part = convert2.convert_vpu2("0206", root, gdb=gpkgs["0206"], log=quiet)
    convert2.convert_vpu2("0207", root, gdb=gpkgs["0207"], log=quiet)
    entry, stats = part["entry"], part["stats"]
    assert entry["grid"] == GRID.to_dict() and entry["ftype_from_fcode"] is True
    assert stats["counts"]["network_lines"] == 4 and stats["counts"]["catchments_all"] == 5
    assert stats["counts"]["network_catchments"] == 4 and stats["counts"]["links_out"] == 0
    assert stats["check"]["unequal"] == 0 and stats["grid_moved_m"] < 0.01
    assert stats["encode"]["shared_edges"] > 0

    m = manifest.build2(root)
    assert m["format"] == 2 and sorted(m["vpus"]) == ["0206", "0207"] and m["links"]["rows"] == 1
    assert set(manifest.manifest_files(m)) == {
        fmt2.lines_file(v) for v in ("0206", "0207")} | {fmt2.cats_file(v) for v in ("0206", "0207")} | {
        fmt2.arcs_file(v) for v in ("0206", "0207")} | {fmt2.steps_file(v) for v in ("0206", "0207")} | {
        fmt2.LINKS_FILE}

    ds = Dataset(root / "data")
    assert isinstance(ds, Dataset2)
    feats = dict((f["properties"]["nhdplusid"], f) for f in catchment_features(ds.catchments_by_ids(list(ID.values()))))
    assert sorted(feats) == sorted(ID.values())
    for n, (_, want, _, _) in REACHES.items():
        got = projected_cells(shapely.geometry.shape(feats[ID[n]]["geometry"]))
        assert shapely.symmetric_difference(got, want).area < 0.002 * want.length, n     # 1e-7 degree rounding

    ws = ds.watershed(ID[1])
    assert ws["status"] == "ok" and ws["nReaches"] == 4 and ws["nHops"] == 3     # 1 -> 2, 3 -> 4 in 0207
    assert ws["areaSqkm"] == 6 * 0.25 - 0.04                                 # six blocks minus the hole
    geom = shapely.geometry.shape(ws["geometry"])
    assert len(geom.interiors) == 1                                         # 3's hole stays open


def test_convert2_refuses_catchments_off_the_grid(packages, tmp_path):
    _, root = packages
    gpkg = write_package(tmp_path / "off.gpkg", "0206", off_grid=True)
    with pytest.raises(arcs.NotGridAligned):
        convert2.convert_vpu2("0206", root, gdb=gpkg, log=lambda msg: None)


def test_simplify_lines_keeps_the_ends_and_the_direction():
    # a wiggly 1 km line: 2 m Douglas-Peucker drops the wiggles everywhere, except
    # within 25 m of each end when asked
    x = np.linspace(0, 1000, 501)
    y = np.where(np.arange(501) % 2, 0.5, 0.0)
    line = shapely.LineString(np.column_stack([x, y]))
    multi = shapely.MultiLineString([line, shapely.LineString([(0, 50), (10, 50.4), (20, 50), (30, 50.4)])])
    plain, kept = convert2.simplify_lines(np.array([line, multi]), 2.0, 0.0), \
        convert2.simplify_lines(np.array([line, multi]), 2.0, 25.0)
    assert shapely.get_num_coordinates(plain[0]) == 2
    coords = shapely.get_coordinates(kept[0])
    near_ends = (x <= 25) | (x >= 975)
    assert len(coords) == near_ends.sum()                         # every end vertex, nothing between
    assert np.array_equal(coords[0], [0, 0]) and np.array_equal(coords[-1], [1000, 0])
    assert np.all(np.diff(coords[:, 0]) > 0)                      # still upstream to downstream
    assert kept[1].geom_type == "MultiLineString" and len(kept[1].geoms) == 2
    assert shapely.get_num_coordinates(kept[1].geoms[1]) == 4     # a 30 m part stays whole
