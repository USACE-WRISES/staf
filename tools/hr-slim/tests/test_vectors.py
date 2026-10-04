"""TIGER roads for the roads values (hrbuild.vectors): a county file the Census server refuses, and a
region that touches no county."""
import zipfile

import pytest

from hrbuild import sources, vectors

REJECTION = b"<html><head><title>Request Rejected</title></head><body>The requested URL was rejected.</body></html>"


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("no download expected")
    monkeypatch.setattr(sources, "fetch_url", refuse)
    monkeypatch.setattr(sources, "remote_size", refuse)


def _county_zip(root, year, geoid):
    """A one-road TIGER/Line county roads zip, as the Census server sends it."""
    gpd = pytest.importorskip("geopandas")
    import shapely
    folder = root / "tiger" / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"tl_{year}_{geoid}_roads"
    gdf = gpd.GeoDataFrame({"LINEARID": ["1104"], "MTFCC": ["S1400"]},
                           geometry=[shapely.LineString([(-73.6, 40.7), (-73.5, 40.7)])], crs="EPSG:4269")
    gdf.to_file(folder / f"{stem}.shp")
    with zipfile.ZipFile(folder / f"{stem}.zip", "w") as z:
        for ext in ("shp", "shx", "dbf", "prj"):
            z.write(folder / f"{stem}.{ext}", f"{stem}.{ext}")
            (folder / f"{stem}.{ext}").unlink()


def test_a_region_with_no_county_has_no_roads(tmp_path, no_network):
    roads_m, dups, other_year = vectors.read_roads([], root=tmp_path)
    assert len(roads_m) == 0 and dups == 0 and other_year == {}


def test_a_refused_county_file_is_read_from_the_year_before(tmp_path, no_network):
    refused = tmp_path / "tiger" / str(sources.TIGER_YEAR) / f"tl_{sources.TIGER_YEAR}_36059_roads.zip"
    refused.parent.mkdir(parents=True)
    refused.write_bytes(REJECTION)
    _county_zip(tmp_path, sources.TIGER_YEAR - 1, "36059")
    seen = []
    roads_m, dups, other_year = vectors.read_roads(["36059"], root=tmp_path, log=seen.append)
    assert len(roads_m) == 1 and dups == 0
    assert other_year == {"36059": sources.TIGER_YEAR - 1}
    assert not refused.exists()                       # the rejection page is gone, so a later run asks again
    assert any("rejection page" in m for m in seen)


def test_the_download_names_counties_read_from_the_year_before(tmp_path, monkeypatch):
    y = sources.TIGER_YEAR
    (tmp_path / "tiger" / str(y)).mkdir(parents=True)
    files = {}
    for geoid, year in (("36059", y - 1), ("48003", y)):
        path = tmp_path / f"{geoid}.zip"
        path.write_bytes(b"PK\x03\x04" + b"0" * 12)
        files[geoid] = (path, year)
    recorded = {}
    monkeypatch.setattr(sources, "counties", lambda vpus: ["36059", "48003"])
    monkeypatch.setattr(sources, "tiger_county_zip", lambda geoid, root, log: files[geoid])
    monkeypatch.setattr(sources, "record", lambda name, info, root: recorded.update(info))
    res = sources.tiger(["0203"], root=tmp_path, log=lambda m: None)
    assert res["other_year"] == {"36059": y - 1}
    assert recorded["vintage"] == f"TIGER/Line {y} roads ({y - 1} for county 36059)"


def test_a_good_county_file_keeps_the_build_year(tmp_path, no_network):
    _county_zip(tmp_path, sources.TIGER_YEAR, "48003")
    z, year = vectors.county_roads_zip("48003", root=tmp_path)
    assert year == sources.TIGER_YEAR and z.name == f"tl_{sources.TIGER_YEAR}_48003_roads.zip"
