"""Offline tests for the rolling ``staf-xs-current`` release (``xsgrid/release.py``)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xsgrid import archive, codec, config, release  # noqa: E402
from xsgrid.engine import geomorph  # noqa: E402

VPU = "0999"


def _section_rows():
    """Three flowlines in two cells, cell order (not flowline order), every kind of section."""
    rng = np.random.default_rng(5)
    rows = []
    specs = [(22000900000044, 4), (22000900000012, 5), (22000900000030, 2)]
    for nid, n in specs:
        for k in range(n):
            rows.append((nid, k, n))
    order = [5, 0, 7, 1, 9, 2, 6, 3, 8, 4, 10]           # mixed, as cells write them
    rows = [rows[i] for i in order]
    out = []
    for i, (nid, k, n) in enumerate(rows):
        wide = 250.0 if i % 5 else 319.76
        n_pts = 501 if wide == 250.0 else 640
        status = "ok"
        if nid == 22000900000030:
            status = "no_dem"                            # a segment with nothing scored: no median
        elif i == 2:
            status = "unbalanced"
        ts = np.linspace(-wide, wide, n_pts)
        z = (np.cumsum(rng.normal(0, 0.05, n_pts)) + 300.0).astype(np.float32)
        thal_i = int(rng.integers(100, 400))
        z[thal_i] = np.float32(z.min() - 1.0)
        thalweg = float(z[thal_i])
        out.append(dict(nid=nid, k=k, n=n, cell=0 if i < 6 else 1, wide=wide, n_pts=n_pts, status=status, z=z,
                        thalweg=thalweg, station=float(ts[thal_i]),
                        er=round(float(rng.uniform(1.0, 9.0)), 2), bhr=round(float(rng.uniform(0.8, 2.0)), 2),
                        bfw=round(float(rng.uniform(2, 40)), 1), fpw=round(float(rng.uniform(5, 300)), 1),
                        bfd=round(float(rng.uniform(0.1, 2)), 2)))
    return out


def _tables(rows):
    arch, met = {}, {}
    for f in archive.ARCHIVE_SCHEMA:
        arch[f.name] = []
    for f in archive.METRICS_SCHEMA:
        met[f.name] = []
    for r in rows:
        scored = r["status"] == "ok"
        no_dem = r["status"] == "no_dem"
        arch["nhdplusid"].append(r["nid"])
        arch["part"].append(0)
        arch["k"].append(r["k"])
        arch["n_sec"].append(r["n"])
        arch["s_m"].append(30.48 * (r["k"] + 0.5))
        arch["x"].append(100000.0 + 30.123456789 * r["k"])
        arch["y"].append(2000000.0 + 0.987654321 * r["k"])
        arch["nx"].append(0.6)
        arch["ny"].append(0.8)
        arch["da_sqkm"].append(12.5)
        arch["division"].append("Interior Plains")
        arch["bf_width_m"].append(7.31)
        arch["bf_depth_m"].append(0.452)
        arch["bf_area_m2"].append(3.3)
        arch["bf_extrapolated"].append(r["k"] == 1)
        arch["wide_m"].append(r["wide"])
        arch["res_m"].append(0 if no_dem else 1)
        arch["n_pts"].append(0 if no_dem else r["n_pts"])
        arch["n_finite"].append(0 if no_dem else r["n_pts"])
        arch["source"].append("" if no_dem else "IA_FullState_2019")
        arch["tiles"].append("" if no_dem else f"USGS_1M_15_x{40 + r['k']}y462_IA_FullState_2019.tif")
        arch["z"].append(None)
        met["nhdplusid"].append(r["nid"])
        met["part"].append(0)
        met["k"].append(r["k"])
        met["n_sec"].append(r["n"])
        met["s_m"].append(30.48 * (r["k"] + 0.5))
        met["res_m"].append(0 if no_dem else 1)
        met["status"].append(r["status"])
        th = r["thalweg"] if not no_dem else None
        vals = dict(entrenchment_ratio=r["er"], bank_height_ratio=r["bhr"], bankfull_width_m=r["bfw"],
                    flood_prone_width_m=r["fpw"], bankfull_depth_m=r["bfd"], thalweg=th,
                    bankfull_stage_m=th + r["bfd"] + 0.0034 if th else None,
                    low_bank_stage_m=th + 0.71234 if th else None, fp_stage_m=None,
                    top_of_bank_m=th + 1.5 if th else None)
        for key in ("entrenchment_ratio", "bank_height_ratio", "bankfull_width_m", "flood_prone_width_m",
                    "bankfull_depth_m", "thalweg", "bankfull_stage_m", "low_bank_stage_m", "fp_stage_m",
                    "top_of_bank_m"):
            met[key].append(vals[key] if scored else None)
        for key in ("low_bank_capped", "edge_limited", "bankfull_area_edge_limited"):
            met[key].append((r["k"] % 2 == 0) if scored else None)
        met["bank_detection"].append("slope_break" if scored else None)
        met["thalweg_station_m"].append(r["station"] if scored else None)
        met["n_points_thinned"].append(250 if scored else None)
        met["verified"].append(False)
    zs = [None if r["status"] == "no_dem" else r["z"] for r in rows]
    arch["z"] = archive.as_float32_lists(zs)
    return pa.table(arch, schema=archive.ARCHIVE_SCHEMA), pa.table(met, schema=archive.METRICS_SCHEMA)


@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A fake archive drive with one merged region (two cells, so two row groups)."""
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "staf-xs")
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(json.dumps({"vpus": [VPU, "0998", "1901"], "built": "2026-10-03",
                                                      "format": 2}), encoding="utf-8")
    monkeypatch.setattr(config, "BUNDLE", bundle)
    rows = _section_rows()
    at, mt = _tables(rows)
    parts = []
    for cell in (0, 1):
        idx = [i for i, r in enumerate(rows) if r["cell"] == cell]
        pa_ = tmp_path / f"c{cell}.parquet"
        pm = tmp_path / f"c{cell}.metrics.parquet"
        archive.write(at.take(pa.array(idx)), pa_)
        archive.write(mt.take(pa.array(idx)), pm)
        parts.append((pa_, pm))
    xs = config.ARCHIVE / "archive" / f"xs_{VPU}.parquet"
    xm = config.ARCHIVE / "metrics" / f"xsm_{VPU}.parquet"
    archive.concat([p[0] for p in parts], xs)
    archive.concat([p[1] for p in parts], xm)
    rec = dict(vpu=VPU, sections=len(rows), cells=2, archive_bytes=xs.stat().st_size,
               metrics_bytes=xm.stat().st_size, archive_sha256=release.sha256(xs), metrics_sha256=release.sha256(xm),
               tiers={"one": 9, "three": 0, "ten": 0, "none": 2}, statuses={"ok": 8, "unbalanced": 1, "no_dem": 2},
               finished="2026-10-10T12:00:00+00:00", sampling_version=config.SAMPLING_VERSION, format=config.FORMAT)
    (config.ARCHIVE / "regions").mkdir(parents=True)
    (config.ARCHIVE / "regions" / f"{VPU}.json").write_text(json.dumps(rec), encoding="utf-8")
    return dict(rows=rows, out=tmp_path / "release")


def test_pack_writes_three_files_and_the_manifest_last(drive):
    got = release.pack(None, drive["out"], log=lambda *_: None)
    assert got["packed"] == [VPU]
    rel = release.read_manifest(drive["out"])
    assert set(rel["assets"]) == {release.numbers_asset(VPU), release.sections_asset(VPU), release.median_asset(VPU)}
    assert rel["regions"][VPU]["sections"] == len(drive["rows"])
    assert rel["pending"] == ["0998"]                  # 1901 is Alaska, outside the grid
    assert rel["tag"] == release.TAG and rel["grid"]["sampling_version"] == config.SAMPLING_VERSION
    for name, a in rel["assets"].items():
        assert release.sha256(drive["out"] / name) == a["sha256"]


def test_numbers_are_exact_and_sorted_by_flowline(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    t = pq.read_table(drive["out"] / release.numbers_asset(VPU))
    keys = list(zip(t.column("nhdplusid").to_pylist(), t.column("k").to_pylist()))
    assert keys == sorted(keys)
    by_key = dict(((r["nid"], r["k"]), r) for r in drive["rows"])
    for row in t.to_pylist():
        r = by_key[(row["nhdplusid"], row["k"])]
        assert release.STATUS[row["status"]] == r["status"]
        if r["status"] == "ok":
            assert row["er"] / 100 == r["er"] and row["bhr"] / 100 == r["bhr"] and row["bfd"] / 100 == r["bfd"]
            assert row["bfw"] / 10 == r["bfw"] and row["fpw"] / 10 == r["fpw"]
            assert row["flags"] & 1 == (r["k"] % 2 == 0)
        else:
            assert row["er"] is None and row["bank"] == release.BANK_NONE
    assert release.verify_region(VPU, drive["out"])["sections"] == len(drive["rows"])


def test_medians_follow_the_drawing_rule_and_copy_the_archive(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    md = pq.read_table(drive["out"] / release.median_asset(VPU)).to_pylist()
    assert [m["nhdplusid"] for m in md] == [22000900000012, 22000900000044]   # the no-DEM segment has none
    for m in md:
        segs = sorted((r for r in drive["rows"] if r["nid"] == m["nhdplusid"]), key=lambda r: r["k"])
        cands = [{"entrenchment_ratio": r["er"] if r["status"] == "ok" else None,
                  "bank_height_ratio": r["bhr"] if r["status"] == "ok" else None} for r in segs]
        chosen = segs[geomorph.median_candidate(cands)]
        assert m["k"] == chosen["k"]
        z = codec.decode(np.asarray(m["z"], dtype=np.int32))
        assert np.array_equal(z.view(np.uint32), chosen["z"].view(np.uint32))       # bit for bit
        ts = np.linspace(-m["wide_cm"] / 100, m["wide_cm"] / 100, m["n_pts"])
        assert ts[m["thalweg_i"]] == chosen["station"] and m["thalweg"] == np.float32(chosen["thalweg"])


def test_sections_carry_what_a_pull_needs(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    st = pq.read_table(drive["out"] / release.sections_asset(VPU)).to_pylist()
    r = [x for x in drive["rows"] if x["nid"] == 22000900000012 and x["status"] == "ok"][0]
    first = [s for s in st if s["nhdplusid"] == r["nid"] and s["k"] == r["k"]][0]
    assert "x" not in first and "tob_m" not in first     # placement follows from the bundle's lines
    assert first["tiles"] == f"USGS_1M_15_x{40 + r['k']}y462_IA_FullState_2019.tif" and first["n_pts"] == r["n_pts"]
    assert first["rc_width"] == 731 and first["rc_depth"] == 452 and first["division"] == "Interior Plains"
    th = r["thalweg"]
    assert first["bf_m"] == np.float32((th + r["bfd"] + 0.0034) - th)
    assert first["lb_m"] == np.float32((th + 0.71234) - th)
    unbalanced = [x for x in drive["rows"] if x["status"] == "unbalanced"][0]
    gone = [s for s in st if s["nhdplusid"] == unbalanced["nid"] and s["k"] == unbalanced["k"]][0]
    assert gone["bf_m"] is None and gone["thalweg"] is None and gone["tiles"]    # no stages, still pullable


def test_placement_rebuilds_the_archive_positions_from_the_lines(tmp_path, monkeypatch):
    from pyproj import Transformer

    from xsgrid import sections
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "staf-xs")
    to4326 = Transformer.from_crs(5070, 4326, always_xy=True)
    lon, lat = to4326.transform([150_000.0, 150_600.0, 151_000.0], [2_100_000.0, 2_100_300.0, 2_100_350.0])
    xs = [int(round(x * 1e5)) for x in lon]
    ys = [int(round(y * 1e5)) for y in lat]
    lines = pa.table(dict(nhdplusid=[7], fcode=pa.array([46006], pa.int32()), totdasqkm=[3.1],
                          x=pa.array([xs], pa.list_(pa.int32())), y=pa.array([ys], pa.list_(pa.int32())),
                          parts=pa.array([[len(xs)]], pa.list_(pa.int32()))))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    pq.write_table(lines, bundle / f"lines2_{VPU}.parquet")
    placed = sections.place(sections.flowlines(VPU, bundle))
    n = len(placed["x"])
    cols = dict((f.name, None) for f in archive.ARCHIVE_SCHEMA)
    for key in ("nhdplusid", "part", "k", "n_sec", "s_m", "x", "y", "nx", "ny", "da_sqkm", "bf_width_m",
                "bf_depth_m", "bf_area_m2", "bf_extrapolated", "wide_m"):
        cols[key] = placed[key]
    cols["division"] = [str(v) for v in placed["division"]]
    cols.update(res_m=[1] * n, n_pts=[501] * n, n_finite=[501] * n, source=["p"] * n, tiles=["t"] * n,
                z=archive.as_float32_lists([np.zeros(501, np.float32)] * n))
    order = np.arange(n)[::-1].copy()                    # the archive keeps cell order, not flowline order
    table = pa.table(cols, schema=archive.ARCHIVE_SCHEMA).take(pa.array(order))
    archive.write(table, config.ARCHIVE / "archive" / f"xs_{VPU}.parquet")
    assert release.verify_placement(VPU, bundle) == n and n > 20
    x = table.column("x").to_numpy().copy()
    x[3] = np.nextafter(x[3], np.inf)                   # one ulp off
    archive.write(table.set_column(table.schema.get_field_index("x"), "x", pa.array(x)),
                  config.ARCHIVE / "archive" / f"xs_{VPU}.parquet")
    with pytest.raises(ValueError, match="another x"):
        release.verify_placement(VPU, bundle)


def test_a_second_pack_keeps_unchanged_regions_and_is_byte_identical(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    before = dict((n, a["sha256"]) for n, a in release.read_manifest(drive["out"])["assets"].items())
    assert release.pack(None, drive["out"], log=lambda *_: None)["kept"] == [VPU]
    assert release.pack(None, drive["out"], force=True, log=lambda *_: None)["packed"] == [VPU]
    after = dict((n, a["sha256"]) for n, a in release.read_manifest(drive["out"])["assets"].items())
    assert before == after


def test_a_changed_metrics_file_is_refused_until_the_record_matches(drive):
    xm = config.ARCHIVE / "metrics" / f"xsm_{VPU}.parquet"
    xm.write_bytes(xm.read_bytes() + b"x")
    with pytest.raises(ValueError, match="differs from its region record"):
        release.pack_region(VPU, drive["out"])
    said = []
    got = release.pack(None, drive["out"], log=said.append)          # a sync keeps going past it
    assert got["failed"] == [VPU] and got["packed"] == [] and "NOT packed" in said[0]
    assert release.read_manifest(drive["out"])["pending"] == ["0998", VPU]


def test_inexact_values_are_refused():
    with pytest.raises(ValueError, match="not exact"):
        release._scaled(np.asarray([1.234]), 100, pa.uint16(), "x")
    with pytest.raises(ValueError, match="outside"):
        release._scaled(np.asarray([700.0]), 100, pa.uint16(), "x")
    assert release._scaled(np.asarray([1.23, np.nan]), 100, pa.uint16(), "x").to_pylist() == [123, None]


class FakeGh:
    def __init__(self, live: dict | None):
        self.live, self.calls = live, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "release", "view"]:
            if self.live is None:
                return subprocess.CompletedProcess(cmd, 1, "", "release not found")
            assets = [{"name": n, "digest": f"sha256:{d}"} for n, d in self.live.items()]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"assets": assets}), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_publish_creates_a_prerelease_and_uploads_the_manifest_last(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    gh = FakeGh(None)
    cmds = release.publish(drive["out"], run=gh, log=lambda *_: None)
    assert cmds[0][:4] == ["gh", "release", "create", release.TAG] and "--prerelease" in cmds[0]
    uploads = [c for c in cmds if c[2] == "upload"]
    assert all("--clobber" in c for c in uploads)
    assert Path(uploads[-1][-1]).name == release.MANIFEST and len(uploads[-1]) == 8
    sent = [Path(p).name for c in uploads[:-1] for p in c[7:]]
    assert sorted(sent) == sorted(release.read_manifest(drive["out"])["assets"])


def test_publish_uploads_only_what_differs(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    rel = release.read_manifest(drive["out"])
    live = dict((n, a["sha256"]) for n, a in rel["assets"].items())
    live[release.median_asset(VPU)] = "0" * 64
    gh = FakeGh(live)
    cmds = release.publish(drive["out"], run=gh, log=lambda *_: None)
    assert not any(c[2] == "create" for c in cmds)
    sent = [Path(p).name for c in cmds if c[2] == "upload" for p in c[7:]]
    assert sent == [release.median_asset(VPU), release.MANIFEST]


def test_publish_refuses_a_file_changed_since_packing(drive):
    release.pack(None, drive["out"], log=lambda *_: None)
    p = drive["out"] / release.numbers_asset(VPU)
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(ValueError, match="changed since it was packed"):
        release.publish(drive["out"], run=FakeGh(None), log=lambda *_: None)


def test_the_release_lock_keeps_one_step_at_a_time(tmp_path):
    assert release.lock(tmp_path)
    assert release.lock(tmp_path)                       # the holder may take it again
    lock = tmp_path / "release.lock"
    lock.write_text(json.dumps({"pid": 4, "started": "x"}), encoding="utf-8")   # a pid that is not python
    assert release.lock(tmp_path)
    release.unlock(tmp_path)
    assert not lock.exists()


def test_the_build_publishes_through_a_separate_process(monkeypatch):
    from xsgrid import build
    started = []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append(cmd)
            self.returncode = None

        def poll(self):
            return self.returncode

    monkeypatch.setattr(build.subprocess, "Popen", Proc)
    monkeypatch.setattr(build, "log", lambda *_: None)
    pub = build.Publisher(True)
    pub.kick()
    pub.kick()                                           # a merge while a round runs: one more later
    assert len(started) == 1 and pub.again
    assert started[0][-4:-2] == ["sync", "--yes"] and started[0][1].endswith("run.py")
    pub.proc.returncode = 0
    pub.poll()                                           # the round ended: the next one starts
    assert len(started) == 2 and not pub.again
    off = build.Publisher(False)
    off.kick()
    assert len(started) == 2 and off.proc is None
