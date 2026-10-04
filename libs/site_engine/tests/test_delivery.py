"""The STAF data bundle delivered from its release (``site_engine.delivery``), offline.

The release is the hr-data fixture bundle packed by the builder (``tools/hr-slim/hrbuild/
release.py``) into a local folder that stands in for the GitHub prerelease: ``release.json``,
``core.zip``, a ``tables-<file>`` per national table and a ``region-<vpu>.zip`` per region. The
engine fetches the core on first use, a region or a table the first time a request reads it, keeps
what it fetched across restarts, and fetches again only what a new release replaced.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from site_engine import _hrslim, bundle, compute_site, delivery, hr
from site_engine._hrslim import arcs, fmt, fmt2, grid

REPO = Path(__file__).resolve().parents[3]
_FIXTURE = REPO / "apps" / "hr-data" / "tests" / "fixture2.py"
_PACKER = REPO / "tools" / "hr-slim" / "hrbuild" / "release.py"
pytestmark = pytest.mark.skipif(not (_FIXTURE.exists() and _PACKER.exists()),
                                reason="hr-data fixture or hr-slim packer not present")


def _load(path: Path, name: str, aliases: dict | None = None):
    aliases = aliases or {}
    saved = {k: sys.modules.get(k) for k in aliases}
    sys.modules.update(aliases)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(scope="module")
def fx():
    # the fixture imports ``hrslim``; the engine's byte-identical copy stands in
    return _load(_FIXTURE, "delivery_fixture2", {"hrslim": _hrslim, "hrslim.arcs": arcs, "hrslim.fmt": fmt,
                                                 "hrslim.fmt2": fmt2, "hrslim.grid": grid})


@pytest.fixture(scope="module")
def packer():
    return _load(_PACKER, "delivery_release_packer")


def _bundle_folder(fx, folder: Path) -> Path:
    root = fx.build(folder)
    (root / bundle.ABSENT_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                           encoding="utf-8")
    fx.build_v2(root)
    fx.write_lookups(root)
    (root / "values" / "values.json").write_text("{}", encoding="utf-8")
    (root / "tables").mkdir()
    (root / "tables" / "nid_points.parquet").write_bytes(b"nid points")
    (root / "tables" / "dem1m_tiles.parquet").write_bytes(b"1 m tiles")
    return root


@pytest.fixture()
def published(tmp_path, fx, packer, monkeypatch):
    """``(release folder, cache folder)`` with the bundle delivered from the release."""
    src = _bundle_folder(fx, tmp_path / "bundle")
    out = tmp_path / "release"
    packer.pack(src, out)
    cache = tmp_path / "cache"
    monkeypatch.setenv(delivery.ENV_BASE, str(out))
    monkeypatch.setenv(delivery.ENV_CACHE, str(cache))
    monkeypatch.delenv(bundle.ENV_ROOT, raising=False)
    bundle.set_source("bundle")
    hr.clear_caches()
    yield out, cache
    bundle.set_source(None)
    hr.clear_caches()


def test_the_core_first_then_each_region_when_a_request_reads_it(published, fx):
    out, cache = published
    assert bundle.enabled() and bundle.describe()["delivered"]["cache"] == str(cache)
    assert (cache / "manifest.json").exists() and (cache / "v2" / "manifest.json").exists()
    assert (cache / "tables" / "dem1m_tiles.parquet").exists()       # the tile catalogs ride in the core
    assert not (cache / fmt2.lines_file("9901")).exists()
    lon, lat = fx.point_deg(50, 25)
    rec = compute_site(lat, lon, {"metricFamilies": []})
    assert rec["status"] == "ok" and rec["watershed"]["areaSqkm"] == pytest.approx(5.0, abs=1e-6)
    assert rec["hrSource"]["answeredBy"] == ["bundle"]
    for vpu in ("9901", "9902"):                                     # the walk crossed into 9902
        assert (cache / fmt2.lines_file(vpu)).exists() and (cache / "values" / f"extras2_{vpu}.parquet").exists()
    assert not (cache / "tables" / "nid_points.parquet").exists()     # no lookup read it yet
    assert bundle.point_tables()._path("nid_points.parquet").read_bytes() == b"nid points"


def test_a_restart_keeps_the_cache_and_a_new_release_replaces_what_changed(published, fx, packer, tmp_path):
    out, cache = published
    assert bundle.flowline(nhdplusid=fx.ID[5])["nhdplusid"] == fx.ID[5]     # both regions read
    assert bundle.flowline(nhdplusid=fx.ID[1])["nhdplusid"] == fx.ID[1]
    fetched = []
    real = delivery.Release._download

    def counting(self, name, sha):
        fetched.append(name)
        return real(self, name, sha)
    delivery.Release._download = counting
    try:
        bundle.set_source("bundle")                                   # a new process, the same disk
        assert bundle.flowline(nhdplusid=fx.ID[5]) is not None and fetched == []
        src = tmp_path / "bundle"                                      # the builder changes region 9902
        (src / "values" / "extras2_9902.parquet").write_bytes((src / "values" / "extras2_9901.parquet").read_bytes())
        packer.pack(src, out)
        bundle.set_source("bundle")
        assert bundle.flowline(nhdplusid=fx.ID[5]) is not None and bundle.flowline(nhdplusid=fx.ID[1]) is not None
        assert fetched == ["region-9902.zip"]
    finally:
        delivery.Release._download = real


def _v2_ranges_overlap(root: Path, index: bool) -> None:
    """V2 ranges that overlap across the regions, as NHDPlus V2's COMIDs and hydroseqs do, and with
    ``index`` the id index that sends a lookup to its region (it rides in the core)."""
    v2 = root / "v2"
    m = json.loads((v2 / "manifest.json").read_text(encoding="utf-8"))
    for key in ("id_range", "hydroseq_range"):
        lo = min(v[key][0] for v in m["vpus"].values())
        hi = max(v[key][1] for v in m["vpus"].values())
        for v in m["vpus"].values():
            v[key] = [lo, hi]
    (v2 / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    if index:
        from site_engine._hrslim.reader2 import write_index
        write_index(v2)


@pytest.mark.parametrize("index, fetched_regions", [(False, ["region-9901.zip", "region-9902.zip"]),
                                                    (True, ["region-9902.zip"])])
def test_a_v2_lookup_by_comid_fetches_only_its_region_with_the_index(tmp_path, fx, packer, monkeypatch,
                                                                     index, fetched_regions):
    """2026-10-04: without the index one New Jersey point fetched 139 region archives."""
    src = _bundle_folder(fx, tmp_path / "bundle")
    _v2_ranges_overlap(src, index)
    out = tmp_path / "release"
    packer.pack(src, out)
    monkeypatch.setenv(delivery.ENV_BASE, str(out))
    monkeypatch.setenv(delivery.ENV_CACHE, str(tmp_path / "cache"))
    monkeypatch.delenv(bundle.ENV_ROOT, raising=False)
    fetched = []
    real = delivery.Release._download

    def counting(self, name, sha):
        fetched.append(name)
        return real(self, name, sha)
    monkeypatch.setattr(delivery.Release, "_download", counting)
    bundle.set_source("bundle")
    hr.clear_caches()
    try:
        rec = bundle.v2_flowline(fx.ID[5])
        assert rec is not None and rec["nhdplusid"] == fx.ID[5]
        assert sorted(n for n in fetched if n.startswith("region-")) == fetched_regions
    finally:
        bundle.set_source(None)
        hr.clear_caches()


def test_an_asset_that_fails_its_checksum_is_left_to_the_service(published, fx):
    out, cache = published
    (out / "region-9902.zip").write_bytes(b"not the packed archive")
    assert bundle.flowline(nhdplusid=fx.ID[5]) is None               # the service answers instead
    assert not (cache / fmt2.lines_file("9902")).exists()
    assert bundle.flowline(nhdplusid=fx.ID[1])["nhdplusid"] == fx.ID[1]


def test_no_release_reads_as_the_service(tmp_path, monkeypatch):
    monkeypatch.setenv(delivery.ENV_BASE, str(tmp_path / "nothing here"))
    monkeypatch.setenv(delivery.ENV_CACHE, str(tmp_path / "cache"))
    monkeypatch.delenv(bundle.ENV_ROOT, raising=False)
    bundle.set_source("bundle")
    try:
        assert not bundle.enabled()
        assert "release unavailable" in bundle.describe()["bundleUnavailable"]
    finally:
        bundle.set_source(None)


def test_an_archive_cannot_write_outside_the_cache(tmp_path):
    out = tmp_path / "release"
    out.mkdir()
    with zipfile.ZipFile(out / "core.zip", "w") as zf:
        zf.writestr("../escaped.txt", "x")
    sha = delivery._sha256(out / "core.zip")
    (out / delivery.MANIFEST).write_text(json.dumps({
        "format": 1, "core": "core.zip", "regions": {}, "tables": {},
        "assets": {"core.zip": {"sha256": sha, "bytes": 1}}}), encoding="utf-8")
    rel = delivery.Release(str(out), tmp_path / "cache")
    with pytest.raises(delivery.Unavailable, match="unsafe"):
        rel.prepare()
    assert not (tmp_path / "escaped.txt").exists()
