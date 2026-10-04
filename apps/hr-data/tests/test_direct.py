"""hrslim.direct on a tiny local package: both modes, the walk, the disk cap."""
from pathlib import Path

import pytest

import fixture
import gdb_fixture
from hrslim import direct

ID = fixture.ID
CELL_SQKM = 0.96                                   # one 0.01 degree cell near 39 N


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    return gdb_fixture.build(tmp_path_factory.mktemp("usgs"))


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(direct, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(direct, "_regions", {})
    return tmp_path / "cache"


def _open(pkg, mode):
    reg = direct.Region(pkg, mode)
    timings = {}
    reg.ensure(timings)
    return reg, timings


@pytest.mark.parametrize("mode", direct.MODES)
def test_nearest_network_stream(pkg, cache, mode):
    reg, timings = _open(pkg, mode)
    assert "open_s" in timings
    f = reg.nearest(*gdb_fixture.NEAR_REACH_3)
    p = f["properties"]
    assert p["nhdplusid"] == ID[3]                 # the closer side ditch is off-network
    assert 80 < p["snap_m"] < 130
    assert p["hydroseq"] == 1003 and p["totdasqkm"] == pytest.approx(1.1)
    assert reg.nearest(fixture.X0 + 0.3, fixture.Y0 + 0.3) is None


@pytest.mark.parametrize("mode", direct.MODES)
def test_catchment_and_watershed(pkg, cache, mode):
    reg, _ = _open(pkg, mode)
    cat = reg.catchment(ID[2])
    assert cat["properties"]["areasqkm"] == pytest.approx(gdb_fixture.AREA_SQKM)
    assert cat["properties"]["measuredSqkm"] == pytest.approx(CELL_SQKM, rel=0.03)
    assert reg.catchment(12345) is None
    timings = {}
    ws = reg.watershed(ID[1], timings)
    # reach 5 sits in the next package, so one package's walk ends at reach 4
    assert ws["status"] == "ok" and ws["nReaches"] == 4 and ws["nHops"] == 3
    assert ws["areaSqkm"] == pytest.approx(4 * CELL_SQKM, rel=0.03)
    assert ws["vaaAreaSqkm"] == pytest.approx(4.6)
    assert {"topology_s", "catchment_index_s", "walk_s", "catchments_s", "union_s"} <= set(timings)
    warm = {}
    assert reg.watershed(ID[2], warm)["nReaches"] == 2
    assert "topology_s" not in warm                # read once per region


def test_budget_refusal_matches_the_engine(pkg, cache):
    reg, _ = _open(pkg, "download")
    out = reg.watershed(ID[1], {}, max_reaches=2)
    assert out["status"] == "refused"
    assert "exceeds the engine budget (3 reaches, 1 hops; the budget is 2 reaches" in out["reason"]


def test_download_mode_unzips_once(pkg, cache):
    reg, timings = _open(pkg, "download")
    assert {"download_s", "unzip_s", "mb_per_s"} <= set(timings)
    assert Path(reg.path).is_dir() and reg.path.endswith(".gdb")
    assert not list((cache / "9901").glob("_get_*"))      # the work folder is gone
    again, timings2 = _open(pkg, "download")
    assert timings2["download_note"] == "already on disk"
    assert direct.downloaded("9901") is True


def test_disk_cap_evicts_the_oldest_region(cache, monkeypatch):
    old = cache / "0101" / "x.gdb"
    old.mkdir(parents=True)
    (old / "t").write_bytes(b"0" * 1000)
    monkeypatch.setattr(direct, "CACHE_CAP_BYTES", 1500)
    assert direct._make_room(400, keep="9901") == []
    assert direct._make_room(800, keep="9901") == ["0101"]
    assert not (cache / "0101").exists()


def test_point_to_package_lookup():
    assert direct.vpus_at(-93.76691, 41.016806) == ["0710"]   # White Breast Creek, Iowa
    assert direct.vpus_at(-40.0, 30.0) == []                  # mid-Atlantic
