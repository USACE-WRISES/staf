"""The 3DEP elevation module: tile names and years, catalog rows from fake
listings, the resumable catalog build, catalog queries, byte estimates, and
1 m window reads over synthetic local tiles with the finite-fraction fallback."""
from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest

from builder import config, dem, state
from builder.paths import DataRoot


def test_tile_names_years_and_cell_bounds():
    assert dem.parse_tile_name("USGS_1m_x27y430_VA_Fairfax_County_2018.tif") == (27, 430)
    assert dem.parse_tile_name("USGS_one_meter_x22y420_VA_Central_Seismic_2013.tif") == (22, 420)
    assert dem.parse_tile_name("USGS_1m_weird.tif") is None
    assert dem.project_year("VA_Fairfax_County_2018") == 2018
    assert dem.project_year("2014_New_York_Clinton_Essex_Lake_Champlain_QL2_LiDAR") == 2014
    assert dem.project_year("NoYearProject", "2025-03-05T00:41:10.000Z") == 2025
    assert dem.cell_bounds((27, 430), 6.0) == (269994.0, 4289994.0, 280006.0, 4300006.0)


def _fake_listing(project, names):
    prefix = f"{dem.ONE_M_PREFIX}{project}/TIFF/"
    return [{"key": prefix + n, "bytes": 1000 + i, "last_modified": "2026-02-14T10:09:48.000Z"}
            for i, n in enumerate(names)]


def _probe(url):
    cell = dem.parse_tile_name(url.rsplit("/", 1)[-1]) or (27, 430)
    minx, miny, maxx, maxy = dem.cell_bounds(cell, 6.0)
    return {"epsg": 26918, "width": 10012, "height": 10012, "res_m": 1.0, "left": minx, "bottom": miny,
            "right": maxx, "top": maxy, "nodata": -3.4e38, "block": 256}


def test_project_rows_use_the_name_rule_when_the_sample_header_confirms_it(monkeypatch):
    names = ["USGS_1m_x27y430_P.tif", "USGS_1m_x28y430_P.tif", "notes.txt"]
    monkeypatch.setattr(dem, "s3_list", lambda prefix, **kw: (_fake_listing("P_2020", names), []))
    rows, note = dem.project_rows("P_2020", probe=_probe)
    assert note == "name rule" and [r["name"] for r in rows] == names[:2]
    assert rows[1]["minx"] == 279994.0 and rows[1]["epsg"] == 26918 and rows[1]["year"] == 2020
    assert rows[0]["west"] < rows[0]["east"] and -78 < rows[0]["west"] < -77
    assert rows[0]["collar_m"] == 6.0 and rows[0]["bytes"] == 1000

    def shifted(url):                                       # the header disagrees with the name rule
        header = _probe(url)
        header["left"] += 500.0
        return header

    calls = []

    def counting(url):
        calls.append(url)
        return shifted(url)

    rows, note = dem.project_rows("P_2020", probe=counting)
    assert note == "headers" and len(calls) == 2 and rows[0]["minx"] == 270494.0


def test_build_catalog_is_resumable_per_project(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    listed = []

    def rows_for(project, session=None):
        listed.append(project)
        if project == "B_2019":
            return [], "no tif"
        return [{"project": project, "name": f"USGS_1m_x1y1_{project}.tif", "url": "u", "epsg": 26918,
                 "res_m": 1.0, "collar_m": 6.0, "width": 10012, "height": 10012, "block": 256, "nodata": -3.4e38,
                 "minx": 0.0, "miny": 0.0, "maxx": 1.0, "maxy": 1.0, "west": -78.0, "south": 38.0,
                 "east": -77.9, "north": 38.1, "bytes": 5, "last_modified": "2026-01-01T00:00:00.000Z",
                 "year": 2018}], "name rule"

    monkeypatch.setattr(dem, "seamless_last_modified", lambda session=None: "Thu, 03 Sep 2026 20:41:14 GMT")
    ledger = state.Ledger(root, "dem1m-catalog")
    ledger.add("A_2018", n=1)                                  # already listed: not asked again
    (root.dem1m / "parts").mkdir(parents=True)
    import pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pylist([rows_for("A_2018")[0][0]], schema=dem._catalog_schema()),
                   root.dem1m / "parts" / "A_2018.parquet")
    listed.clear()
    path = dem.build_catalog(root, progress, control, workers=2, projects=["A_2018", "B_2019", "C_2021"],
                             rows_for=rows_for)
    assert sorted(listed) == ["B_2019", "C_2021"]
    table = pq.read_table(path)
    assert table.column("project").to_pylist() == ["A_2018", "C_2021"]
    meta = json.loads(root.dem1m_meta.read_text())
    assert meta["tiles"] == 2 and meta["skipped"] == {"B_2019": "no tif"} and meta["seamless_last_modified"]
    assert len(state.Ledger(root, "dem1m-catalog")) == 0 and not (root.dem1m / "parts").exists()


def _catalog_table(rows):
    return pa.Table.from_pylist(rows, schema=dem._catalog_schema())


def _row(project, name, year, minx, maxx, west, east, url="u", last="2026-01-01T00:00:00.000Z", block=256,
         bytes_=10012 * 10012):
    return {"project": project, "name": name, "url": url, "epsg": 26918, "res_m": 1.0, "collar_m": 6.0,
            "width": 10012, "height": 10012, "block": block, "nodata": -3.4e38, "minx": minx, "miny": 4289994.0,
            "maxx": maxx, "maxy": 4300006.0, "west": west, "south": 38.7, "east": east, "north": 38.8,
            "bytes": bytes_, "last_modified": last, "year": year}


def test_catalog_queries_prefer_the_newest_project_and_estimate_window_bytes():
    rows = [_row("OLD_2013", "a", 2013, 269994.0, 280006.0, -77.66, -77.54),
            _row("NEW_2018", "b", 2018, 269994.0, 280006.0, -77.66, -77.54),
            _row("NEW_2018", "c", 2018, 279994.0, 290006.0, -77.55, -77.43),
            _row("FAR_2020", "d", 2020, 400000.0, 410006.0, -76.0, -75.9)]
    cat = dem.Catalog(_catalog_table(rows))
    box = (-77.60, 38.72, -77.50, 38.78)
    assert sorted(cat.name[cat.tiles_for(box)].tolist()) == ["a", "b", "c"]
    assert cat.projects_for(box) == ["NEW_2018", "OLD_2013"]
    assert cat.name[cat.tiles_for(box, project="NEW_2018")].tolist() == ["b", "c"]
    # a 300 x 300 m window straddling two tiles at one byte per pixel: whole 256-px blocks are pulled
    est = dem.window_bytes(cat, cat.tiles_for(box, project="NEW_2018"), (279900.0, 4295000.0, 280200.0, 4295300.0))
    assert est == pytest.approx((2 * 2 + 1 * 2) * 256 * 256 * dem.WIRE_FACTOR, rel=0.01)


def _write_tile(path, west, north, values, *, nodata=-3.4e38):
    import rasterio
    from rasterio.transform import from_origin
    height, width = values.shape
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32",
                       crs="EPSG:26918", transform=from_origin(west, north, 1.0, 1.0), nodata=nodata,
                       tiled=True, blockxsize=64, blockysize=64, compress="lzw") as ds:
        ds.write(values.astype("float32"), 1)


def _valley(width, height, west, x_thalweg):
    xs = west + np.arange(width) + 0.5
    profile = 100.0 + np.abs(xs - x_thalweg) * 0.05            # a V valley across x, flat along y
    return np.tile(profile, (height, 1))


def _buffer4326(cx, cy, radius_m):
    from pyproj import Transformer
    from shapely.geometry import Point
    from shapely.ops import transform
    to4326 = Transformer.from_crs("EPSG:26918", "EPSG:4326", always_xy=True)
    return transform(to4326.transform, Point(cx, cy).buffer(radius_m))


def test_onemetre_dem_merges_local_tiles_and_falls_back_when_mostly_nodata(tmp_path):
    west, north = 270000.0, 4290200.0                           # two 200 x 200 m tiles side by side
    left = tmp_path / "left.tif"
    right = tmp_path / "right.tif"
    _write_tile(left, west, north, _valley(200, 200, west, west + 200))
    _write_tile(right, west + 200, north, _valley(200, 200, west + 200, west + 200))
    rows = [_row("P_2020", "left", 2020, west, west + 200, -77.66, -77.54, url=str(left), bytes_=200 * 200),
            _row("P_2020", "right", 2020, west + 200, west + 400, -77.66, -77.54, url=str(right), bytes_=200 * 200)]
    for r in rows:
        r.update({"miny": north - 200, "maxy": north, "width": 200, "height": 200, "block": 64,
                  "south": 38.0, "north": 39.0, "west": -78.0, "east": -77.0})
    cat = dem.Catalog(_catalog_table(rows))
    seen = []
    buf = _buffer4326(west + 200, north - 100, 40.0)             # straddles the seam
    hit = dem.onemetre_dem(cat, buf, accounting=seen.append)
    assert hit is not None
    da, provenance = hit
    assert provenance["project"] == "P_2020" and sorted(provenance["tiles"]) == ["left", "right"]
    values = np.asarray(da.values, dtype=float)
    assert np.isfinite(values).mean() > 0.5 and np.nanmin(values) == pytest.approx(100.0, abs=0.06)
    assert seen and seen[0] > 0
    dem5070, res, prov = dem.best_available_dem(buf, catalog=cat)
    assert res == 1 and dem5070.rio.crs.to_epsg() == 5070 and prov["source"] == "1m"

    hole = np.full((200, 200), -3.4e38, dtype="float32")        # a tile that is nodata almost everywhere
    hole[:5, :] = 100.0
    _write_tile(left, west, north, hole)
    _write_tile(right, west + 200, north, hole)
    dem.close_tiles()
    assert dem.onemetre_dem(cat, buf) is None


def test_quad_names_bounds_and_the_ninth_arcsecond_catalog(tmp_path):
    assert dem.parse_quad_name("ned19_n38x00_w075x25_va_eastpeninsula_2010") == (38.0, -75.25)
    assert dem.parse_quad_name("ned19_n13x50_e144x75_gu_guam_2012") == (13.5, 144.75)
    west, south, east, north = dem.quad_bounds("ned19_n38x00_w075x25_va_eastpeninsula_2010")
    assert west == pytest.approx(-75.25 - 6 / 32400) and north == pytest.approx(38.0 + 6 / 32400)
    assert south == pytest.approx(37.75 - 6 / 32400) and east == pytest.approx(-75.0 + 6 / 32400)
    assert dem.quad_bounds("Thumbs.db") is None
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    path = dem.build_catalog19(root, progress, control,
                               quads=["ned19_n38x00_w075x25_va_eastpeninsula_2010", "junk",
                                      "ned19_n38x25_w078x75_va_central_2013"])
    cat = dem.Catalog19.load(root)
    assert cat.n == 2 and path.exists() and json.loads(root.dem19_meta.read_text())["unparsed"] == 1
    assert cat.name[cat.quads_for((-78.7, 38.05, -78.6, 38.1))].tolist() == ["ned19_n38x25_w078x75_va_central_2013"]
    assert cat.year.tolist() == [2010, 2013]


def test_threemetre_quads_sit_between_lidar_and_the_seamless(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    res = dem.QUAD_RES_DEG
    left, top = -78.75 - 6 * res, 38.25 + 6 * res
    n = 8112 // 20                                             # a small quad stand-in on the real grid
    path = tmp_path / "ned19_n38x25_w078x75_va_central_2013.img"
    xs = left + (np.arange(n) + 0.5) * res
    values = np.tile(100.0 + np.abs(xs - (left + n * res / 2)) / res * 0.2, (n, 1)).astype("float32")
    with rasterio.open(path, "w", driver="HFA", height=n, width=n, count=1, dtype="float32", crs="EPSG:4269",
                       transform=from_origin(left, top, res, res), nodata=-3.4028234663852886e+38) as ds:
        ds.write(values, 1)
    root = DataRoot(tmp_path / "data").ensure()
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    dem.build_catalog19(root, progress, control, quads=[path.stem])
    cat19 = dem.Catalog19.load(root)
    cat19.url[0] = str(path)                                   # local stand-in for the bucket object
    from shapely.geometry import Point
    center = Point(left + n * res / 2, top - n * res / 2)
    buf = center.buffer(n * res / 4)
    hit = dem.threemetre_dem(cat19, buf)
    assert hit is not None
    da, provenance = hit
    assert provenance["source"] == "19" and provenance["quads"] == [path.stem]
    assert np.nanmin(np.asarray(da.values, dtype=float)) == pytest.approx(100.0, abs=0.3)
    dem5070, res_m, prov = dem.best_available_dem(buf, catalog=None, catalog19=cat19)
    assert res_m == 3 and dem5070.rio.crs.to_epsg() == 5070 and "1m: no catalog" in prov["note"]
    dem.close_tiles()


def test_bandwidth_counter_flushes_and_pauses_at_the_budget(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    counter = state.Bandwidth(root, budget_bytes=5_000, flush_every=2)
    counter.add(1_000)
    assert not root.bandwidth.exists()                          # buffered
    counter.add(1_000)
    assert json.loads(root.bandwidth.read_text())["months"][state.Bandwidth.month()]["bytes"] == 2_000
    other = state.Bandwidth(root, budget_bytes=5_000)
    other.add(2_500)
    assert other.month_bytes() == 4_500                         # disk plus its own buffer
    other.check()
    other.add(600)
    with pytest.raises(state.PauseRequested, match="byte budget"):
        other.check()
    assert json.loads(root.bandwidth.read_text())["months"][state.Bandwidth.month()]["bytes"] == 5_100
