"""Where the 3DEP tile reader finds its catalogs (``easi.datasources.dem_tiles``), offline.

``set_catalog_folder`` wins; else ``STAF_DEM_CATALOG``; else, when the STAF data bundle is the
source, its ``tables/`` (the ``STAF_DATA_BUNDLE`` folder, or the cache the site engine delivers
the bundle into). A look before the catalogs arrive is not remembered.
"""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from easi.datasources import dem_tiles

ENV = ("STAF_DEM_CATALOG", "STAF_DATA_SOURCE", "STAF_DATA_BUNDLE", "STAF_DATA_CACHE")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    dem_tiles.use_environment()
    yield
    dem_tiles.use_environment()


def test_off_without_a_source(monkeypatch, tmp_path):
    assert dem_tiles.catalog_folder() is None and dem_tiles.catalogs() == (None, None)
    monkeypatch.setenv("STAF_DATA_SOURCE", "service")
    monkeypatch.setenv("STAF_DATA_BUNDLE", str(tmp_path))
    assert dem_tiles.catalog_folder() is None


def test_the_order_of_the_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_DATA_SOURCE", "auto")
    monkeypatch.setenv("STAF_DATA_CACHE", str(tmp_path / "cache"))
    assert dem_tiles.catalog_folder() == tmp_path / "cache" / "tables"   # the delivered bundle
    monkeypatch.setenv("STAF_DATA_BUNDLE", str(tmp_path / "bundle"))
    assert dem_tiles.catalog_folder() == tmp_path / "bundle" / "tables"
    monkeypatch.setenv("STAF_DEM_CATALOG", str(tmp_path / "dem"))
    assert dem_tiles.catalog_folder() == tmp_path / "dem"
    dem_tiles.set_catalog_folder(None)
    assert dem_tiles.catalog_folder() is None


def test_catalogs_that_arrive_later_are_found(monkeypatch, tmp_path):
    folder = tmp_path / "tables"
    monkeypatch.setenv("STAF_DEM_CATALOG", str(folder))
    assert dem_tiles.catalogs() == (None, None)                    # not there yet, not remembered
    folder.mkdir()
    pq.write_table(pa.table({"name": ["q1"], "url": ["https://example.invalid/q1.tif"],
                             "west": [-84.0], "south": [40.0], "east": [-83.75], "north": [40.25],
                             "year": [2020]}), folder / dem_tiles.CATALOG_19)
    one, nine = dem_tiles.catalogs()
    assert one is None and nine is not None


# --- a lidar project whose tiles sit in two UTM zones (2026-10-03) --------------------------
# Of 941 projects in the national catalog, 331 have tiles in more than one CRS. A buffer that
# reaches tiles of one project in two zones used to keep only the first zone's tiles.

ZONE_LINE = -90.0                        # UTM 15/16


@pytest.fixture
def close_tiles():
    yield
    for ds in list(dem_tiles._OPEN.values()):
        ds.close()
    dem_tiles._OPEN.clear()


def _tile(path, epsg, west, south, east, north, value):
    """A 1 m float32 GeoTIFF in ``epsg`` covering the lon/lat box, filled with ``value``;
    returns its catalog row."""
    import math

    import numpy as np
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    t = Transformer.from_crs(4326, epsg, always_xy=True)
    xs, ys = t.transform([west, east, west, east], [south, south, north, north])
    minx, maxx = math.floor(min(xs)), math.ceil(max(xs))
    miny, maxy = math.floor(min(ys)), math.ceil(max(ys))
    width, height = maxx - minx, maxy - miny
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="float32",
                       crs=f"EPSG:{epsg}", transform=from_origin(minx, maxy, 1.0, 1.0), nodata=-999999.0) as ds:
        ds.write(np.full((height, width), value, dtype="float32"), 1)
    w, s, e, n = transform_bounds(f"EPSG:{epsg}", "EPSG:4326", minx, miny, maxx, maxy)
    return dict(project="P", name=path.name, url=str(path), last_modified="2026-01-01T00:00:00.000Z",
                epsg=epsg, year=2020, minx=float(minx), miny=float(miny), maxx=float(maxx), maxy=float(maxy),
                west=w, south=s, east=e, north=n)


def _use_catalog(folder, rows):
    cols = dict((k, [r[k] for r in rows]) for k in rows[0])
    pq.write_table(pa.table(cols), folder / dem_tiles.CATALOG_1M)
    dem_tiles.set_catalog_folder(folder)


def test_a_project_in_two_utm_zones_answers_from_both(tmp_path, close_tiles):
    import numpy as np
    from shapely.geometry import box
    west = _tile(tmp_path / "w15.tif", 26915, ZONE_LINE - 0.006, 39.996, ZONE_LINE, 40.004, 100.0)
    east = _tile(tmp_path / "e16.tif", 26916, ZONE_LINE, 39.996, ZONE_LINE + 0.006, 40.004, 200.0)
    _use_catalog(tmp_path, [west, east])
    buf = box(ZONE_LINE - 0.003, 39.998, ZONE_LINE + 0.003, 40.002)      # straddles the zone line
    da, res, provenance = dem_tiles.best_tile_dem(buf)
    assert res == 1 and da.rio.crs.to_epsg() == 5070
    assert provenance["zones"] == [26915, 26916]
    assert provenance["tiles"] == ["w15.tif", "e16.tif"]
    values = np.asarray(da.values, dtype=float)
    finite = values[np.isfinite(values)]
    assert set(np.unique(finite).tolist()) == {100.0, 200.0}            # both halves answered
    assert (finite == 200.0).mean() > 0.4 and (finite == 100.0).mean() > 0.4
    assert provenance["finite"] >= dem_tiles.FINITE_MIN


def test_one_zone_reads_exactly_as_before(tmp_path, close_tiles):
    import numpy as np
    from shapely.geometry import box
    west = _tile(tmp_path / "w15.tif", 26915, ZONE_LINE - 0.006, 39.996, ZONE_LINE, 40.004, 100.0)
    _use_catalog(tmp_path, [west])
    buf = box(ZONE_LINE - 0.005, 39.998, ZONE_LINE - 0.001, 40.002)
    da, res, provenance = dem_tiles.best_tile_dem(buf)
    assert "zones" not in provenance and provenance["tiles"] == ["w15.tif"]
    poly = dem_tiles._project_polygon(buf, 26915)                       # the single-zone path, by hand
    array, transform, crs = dem_tiles.merge_windows([west["url"]], poly.bounds)
    ref = dem_tiles.as_dataarray(array, transform, crs).rio.clip([poly], crs=crs, drop=True, all_touched=True)
    ref = ref.rio.reproject(5070)
    assert da.shape == ref.shape and da.rio.transform() == ref.rio.transform()
    assert np.array_equal(np.asarray(da.values), np.asarray(ref.values), equal_nan=True)
