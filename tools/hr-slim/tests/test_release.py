"""The STAF data bundle as release assets (``hrbuild.release``).

The bundle is the hr-data app's two-region fixture (``apps/hr-data/tests/fixture2.py``) with its V2
part, its value tables and two national tables. Every file lands in exactly one asset, the
archives are deterministic, and ``publish`` uploads ``release.json`` last.
"""
import importlib.util
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from hrbuild import release

FIXTURE = Path(__file__).resolve().parents[3] / "apps" / "hr-data" / "tests" / "fixture2.py"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="hr-data fixture not present")


def _fixture():
    spec = importlib.util.spec_from_file_location("release_fixture2", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def bundle(tmp_path):
    fx = _fixture()
    root = fx.build(tmp_path / "bundle")
    (root / "coverage_absent.geojson").write_text('{"type": "FeatureCollection", "features": []}',
                                                  encoding="utf-8")
    fx.build_v2(root)
    fx.write_lookups(root)
    (root / "values" / "values.json").write_text("{}", encoding="utf-8")
    (root / "tables").mkdir()
    (root / "tables" / "nid_points.parquet").write_bytes(b"nid points")
    (root / "tables" / "dem1m_tiles.parquet").write_bytes(b"1 m tiles")
    return root


def _names(path: Path) -> set:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_every_file_lands_in_one_asset(bundle, tmp_path):
    out = tmp_path / "release"
    res = release.pack(bundle, out)
    assert res["regions"] == 2 and res["tables"] == 1
    rel = json.loads((out / release.MANIFEST).read_text(encoding="utf-8"))
    assert rel["tag"] == "staf-data-current" and rel["bundle"]["built"] == "2026-10-01T00:00:00+00:00"
    assert rel["regions"] == {"9901": "region-9901.zip", "9902": "region-9902.zip"}
    assert rel["tables"] == {"nid_points.parquet": "tables-nid_points.parquet"}
    core = _names(out / "core.zip")
    assert core == {"manifest.json", "links2.parquet", "coverage_absent.geojson", "values/values.json",
                    "v2/manifest.json", "v2/links2.parquet", "tables/dem1m_tiles.parquet"}
    r1 = _names(out / "region-9901.zip")
    assert {"lines2_9901.parquet", "steps2_9901.bin", "values/extras2_9901.parquet",
            "v2/cats2_9901.parquet"} <= r1 and not any("9902" in n for n in r1)
    every = core | r1 | _names(out / "region-9902.zip") | {"tables/nid_points.parquet"}
    assert every == set(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    for name, entry in rel["assets"].items():
        assert entry["sha256"] == release._sha256(out / name) and entry["bytes"] == (out / name).stat().st_size


def test_archives_are_deterministic(bundle, tmp_path):
    release.pack(bundle, tmp_path / "a")
    release.pack(bundle, tmp_path / "b")
    a = json.loads((tmp_path / "a" / release.MANIFEST).read_text(encoding="utf-8"))["assets"]
    b = json.loads((tmp_path / "b" / release.MANIFEST).read_text(encoding="utf-8"))["assets"]
    assert a == b


def test_the_v2_id_index_rides_in_the_core(bundle, tmp_path):
    from hrslim.reader2 import INDEX_FILE, write_index
    write_index(bundle / "v2")
    out = tmp_path / "release"
    release.pack(bundle, out)
    assert f"v2/{INDEX_FILE}" in _names(out / "core.zip")


def test_the_usgs_i_packages_are_regions_too():
    """USGS ships a few Great Lakes units as their own "i" packages (0418i ...): their files are a
    region's like any other (the national build has five)."""
    for name, vpu in (("arcs2_0418i.parquet", "0418i"), ("lines2_0101.parquet", "0101"),
                      ("steps2_19020401.bin", "19020401")):
        assert release.REGION_FILE.match(name).group("vpu") == vpu
    assert release.REGION_FILE.match("arcs2_0418x.parquet") is None


def test_a_stray_file_is_refused(bundle, tmp_path):
    (bundle / "notes.txt").write_text("not part of the bundle", encoding="utf-8")
    with pytest.raises(ValueError, match="notes.txt"):
        release.pack(bundle, tmp_path / "release")


def test_publish_uploads_the_manifest_last(bundle, tmp_path):
    out = tmp_path / "release"
    release.pack(bundle, out)
    ran = []

    def fake_run(cmd, **kwargs):
        ran.append(cmd)
        return SimpleNamespace(returncode=1 if cmd[:3] == ["gh", "release", "view"] else 0)
    commands = release.publish(out, run=fake_run)
    assert commands[0][:3] == ["gh", "release", "create"] and "--prerelease" in commands[0]
    assert commands[-1][-1].endswith(release.MANIFEST)
    assert all(not c[-1].endswith(release.MANIFEST) for c in commands[:-1])
    uploaded = [Path(x).name for c in commands[1:-1] for x in c[7:]]
    assert sorted(uploaded) == sorted(json.loads((out / release.MANIFEST).read_text(encoding="utf-8"))["assets"])
    (out / "region-9902.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="region-9902.zip"):
        release.publish(out, run=fake_run)
