"""Offline tests for the cross-section grid builder."""
from __future__ import annotations

import math
import pickle
import random
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xsgrid import codec, config, derive, sample, sections  # noqa: E402
from xsgrid.engine import bankfull, bieger, geomorph  # noqa: E402

CACHE = Path(r"D:\Data\nhdplus-hr\research\xs_cache.pkl")


def test_codec_round_trip_is_bit_exact():
    rng = np.random.default_rng(3)
    base = np.cumsum(rng.normal(0, 0.2, 5000)) + 250.0
    z = base.astype(np.float32)
    z[[5, 6, 900]] = np.nan
    z[100:110] = -86.5                                   # below sea level
    z[2000] = 4400.0                                     # a cliff: big jump
    z[3000:3005] = 0.0
    z[3005] = -0.0
    for arr in (z, z[:1], z[:2], np.zeros(0, dtype=np.float32), np.float32([np.inf, -np.inf, 1.5])):
        back = codec.decode(codec.encode(arr))
        assert back.dtype == np.float32
        assert np.array_equal(back.view(np.uint32), arr.view(np.uint32))


def test_codec_codes_are_small_for_terrain():
    z = (np.cumsum(np.random.default_rng(1).normal(0, 0.05, 500)) + 300).astype(np.float32)
    codes = codec.encode(z)
    assert np.percentile(np.abs(codes[2:]), 95) < 2 ** 16


def _profiles():
    out = []
    if CACHE.exists():
        for reach in pickle.load(open(CACHE, "rb")):
            for sec in reach["sections"]:
                bx, bz = (np.asarray(v, dtype=float) for v in sec["balanced"])
                out.append((bx.tolist(), bz.tolist()))
                out.append((bx.tolist(), bz.astype(np.float32).astype(float).tolist()))
    rng = random.Random(11)
    for n in (4, 5, 30, 251, 260, 400, 900):
        xs = [float(i) for i in range(n)]
        out.append((xs, [rng.choice([0.0, 0.5, 1.0]) for _ in xs]))           # many equal areas: ties
        out.append((xs, [math.sin(i / 7.0) * 3 + rng.random() * 0.01 for i in xs]))
        out.append((xs, [0.0] * n))
    return out


def test_fast_simplify_matches_reference():
    profiles = _profiles()
    assert len(profiles) > 20
    for st, el in profiles:
        assert derive.simplify(st, el) == tuple(geomorph.simplify_profile(st, el)) or \
            list(derive.simplify(st, el)) == list(geomorph.simplify_profile(st, el))


def test_fast_simplify_returns_same_lists():
    for st, el in _profiles():
        a = derive.simplify(st, el)
        b = geomorph.simplify_profile(st, el)
        assert list(a[0]) == list(b[0]) and list(a[1]) == list(b[1])


@pytest.mark.parametrize("da", [0.004, 0.3, 5.0, 87.3, 950.0, 25000.0])
def test_bankfull_matches_bieger(da):
    for lat, lon in ((41.1, -93.3), (35.6, -82.55), (47.45, -121.95), (31.9, -82.5), (60.0, -150.0)):
        ref = bieger.bankfull_geometry(da, lat, lon)
        got = bankfull(da, bieger.division_at(lat, lon))
        assert got == (ref["width_m"], ref["depth_m"], ref["area_m2"], ref["division_name"], ref["extrapolated"])


def test_divisions_match_bieger_division_at():
    rng = np.random.default_rng(5)
    lon = rng.uniform(-124.5, -67.0, 120)
    lat = rng.uniform(25.0, 49.0, 120)
    lon = np.r_[lon, -124.9, -70.0]                      # just offshore: the nearest-within rule
    lat = np.r_[lat, 44.0, 41.3]
    got = sections.divisions_at(lon, lat)
    for i in range(len(lon)):
        assert got[i] == bieger.division_at(float(lat[i]), float(lon[i])), (lat[i], lon[i])


def _line_table(coords_deg, da=12.5, fcode=46006, nid=1):
    xs = [int(round(x * 1e5)) for x, _ in coords_deg]
    ys = [int(round(y * 1e5)) for _, y in coords_deg]
    return pa.table(dict(nhdplusid=[nid], fcode=pa.array([fcode], pa.int32()), totdasqkm=[da],
                         x=pa.array([xs], pa.list_(pa.int32())), y=pa.array([ys], pa.list_(pa.int32())),
                         parts=pa.array([[len(xs)]], pa.list_(pa.int32()))))


def test_place_spacing_and_orientation():
    from pyproj import Transformer
    to4326 = Transformer.from_crs(5070, 4326, always_xy=True)
    x0, y0 = 200_000.0, 2_000_000.0
    lon, lat = to4326.transform([x0, x0 + 1000.0], [y0, y0])        # 1 km due east in EPSG:5070
    p = sections.place(_line_table(list(zip(lon, lat))))
    n = len(p["s_m"])
    length = p["s_m"][0] * 2 * n
    assert n == int(math.floor(length / config.SPACING_M + 0.5))
    gaps = np.diff(p["s_m"])
    assert np.allclose(gaps, length / n)
    assert abs(p["s_m"][0] - length / n / 2) < 1e-9
    assert np.allclose(p["nx"], 0.0, atol=2e-3) and np.allclose(p["ny"], 1.0, atol=2e-3)   # north: left of east
    assert set(p["wide_m"].tolist()) == {250.0}
    assert (p["k"] == np.arange(n)).all() and (p["n_sec"] == n).all()


def test_place_skips_other_flowline_types():
    t = pa.concat_tables([_line_table([(-93.3, 41.1), (-93.29, 41.1)], fcode=55800, nid=1),
                          _line_table([(-93.3, 41.2), (-93.29, 41.2)], fcode=46006, nid=2),
                          _line_table([(-93.3, 41.3), (-93.29, 41.3)], fcode=33600, nid=3)])
    keep = np.zeros(t.num_rows, dtype=bool)
    fc = t.column("fcode").to_numpy()
    for lo, hi in config.FCODE_RANGES:
        keep |= (fc >= lo) & (fc < hi)
    assert keep.tolist() == [False, True, True]


def test_place_handles_a_region_with_nothing_to_grid():
    from xsgrid import build
    p = sections.place(_line_table([(-93.3, 41.1), (-93.29, 41.1)]).slice(0, 0))   # run 7 stopped on 0418
    assert set(build.SECTION_COLUMNS) - {"cell"} <= set(p) and all(len(v) == 0 for v in p.values())
    assert len(sections.cell_ids(p["x"], p["y"])) == 0


def test_a_region_with_nothing_to_grid_is_recorded_done(tmp_path, monkeypatch):
    import time

    import pyarrow.parquet as pq

    from xsgrid import archive, build
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "archive")
    rec = build.finish_region("0418", [], time.time())
    assert rec["sections"] == 0 and rec["cells"] == 0 and build.region_done("0418")
    for path, schema in ((tmp_path / "archive" / "archive" / "xs_0418.parquet", archive.ARCHIVE_SCHEMA),
                         (tmp_path / "archive" / "metrics" / "xsm_0418.parquet", archive.METRICS_SCHEMA)):
        t = pq.read_table(path)
        assert t.num_rows == 0 and t.column_names == schema.names
    assert (tmp_path / "work" / "metrics" / "xsm_0418.parquet").exists()


def test_bilinear_is_exact_on_a_plane_and_propagates_missing():
    from affine import Affine
    tr = Affine(1.0, 0.0, 1000.0, 0.0, -1.0, 2000.0)
    rows, cols = np.mgrid[0:50, 0:60]
    cx = 1000.0 + cols + 0.5
    cy = 2000.0 - rows - 0.5
    arr = (0.25 * cx - 0.5 * cy + 3.0).astype(np.float32)
    X = np.array([1010.3, 1020.0, 1033.75])
    Y = np.array([1980.6, 1975.5, 1960.2])
    got = sample.bilinear(arr.astype(np.float64).astype(np.float32), tr, X, Y)
    want = 0.25 * X - 0.5 * Y + 3.0
    assert np.allclose(got, want, atol=1e-3)
    arr2 = arr.copy()
    arr2[19, 10] = np.nan                                # a neighbour of the first point
    got2 = sample.bilinear(arr2, tr, X, Y)
    assert math.isnan(got2[0]) and np.isfinite(got2[1:]).all()
    assert math.isnan(sample.bilinear(arr, tr, np.array([999.0]), np.array([1990.0]))[0])


def test_n_points_follows_the_app():
    assert sample.n_points(250.0, 1) == 501
    assert sample.n_points(250.0, 3) == 167
    assert sample.n_points(250.0, 10) == 51
    assert sample.n_points(800.0, 1) == 1601
    assert sample.n_points(1200.0, 1) == 2001


@pytest.mark.skipif(not CACHE.exists(), reason="research cache not on this machine")
def test_metrics_follow_the_app_chain():
    reach = pickle.load(open(CACHE, "rb"))[0]
    sec = reach["sections"][0]
    ts, z = (np.asarray(v, dtype=float) for v in sec["raw"])
    bf = reach["bankfull"]
    wide = min(max(8.0 * bf["width_m"], 250.0), 800.0)
    n_full = sample.n_points(wide, 1)
    full = np.linspace(-wide, wide, n_full)
    at = np.searchsorted(full, ts)                      # the cache kept the finite samples only
    assert np.array_equal(full[at], ts)
    z_full = np.full(n_full, np.nan, dtype=np.float32)
    z_full[at] = z.astype(np.float32)
    got = derive.metrics(z_full, wide, n_full, 1, reach["da"], bf["width_m"], bf["depth_m"], bf["area_m2"],
                         bf.get("division_name"), verify=True)
    zz = z.astype(np.float32).astype(np.float64)
    bal = geomorph.balanced_profile(ts.tolist(), zz.tolist())
    st, el = geomorph.simplify_profile(bal[0], bal[1])
    ref = geomorph.summarize_profile(st, el, reach["da"], bankfull=(bf["width_m"], bf["depth_m"]),
                                     bankfull_area_m2=bf["area_m2"], division=bf.get("division_name"), dem_res_m=1)
    assert got["status"] == "ok" and got["verified"]
    for k in derive.FLOAT_KEYS:
        assert got[k] == (None if ref.get(k) is None else float(ref[k]))
    assert got["bank_detection"] == ref["bank_detection"]


def _transects():
    """Real raw transects: the research cache plus any archive parts on this machine."""
    out = []
    if CACHE.exists():
        for reach in pickle.load(open(CACHE, "rb")):
            bf = reach["bankfull"]
            for sec in reach["sections"]:
                ts, z = (np.asarray(v, dtype=float) for v in sec["raw"])
                out.append((ts, z, reach["da"], bf))
    return out


def test_fast_copies_follow_the_pinned_geomorph():
    from xsgrid import fastgeo
    assert fastgeo.ACTIVE, "geomorph.py changed: re-verify fastgeo against it, then update PINNED"


def test_fast_balanced_and_simplify_match_reference():
    from xsgrid import fastgeo
    n = 0
    for ts, z, _da, _bf in _transects():
        for zz in (z, z.astype(np.float32).astype(np.float64)):
            a = fastgeo.balanced_profile(ts.tolist(), zz.tolist())
            b = geomorph.balanced_profile(ts.tolist(), zz.tolist())
            assert a == (None if b is None else (list(b[0]), list(b[1])))
            if b is None:
                continue
            fa = fastgeo.simplify_profile(b[0], b[1])
            fb = geomorph.simplify_profile(b[0], b[1])
            assert list(fa[0]) == list(fb[0]) and list(fa[1]) == list(fb[1])
            n += 1
    for st, el in _profiles():
        fa = fastgeo.simplify_profile(st, el)
        fb = geomorph.simplify_profile(st, el)
        assert list(fa[0]) == list(fb[0]) and list(fa[1]) == list(fb[1])
    assert n > 100 or not CACHE.exists()


def test_fast_flow_area_matches_reference():
    from xsgrid import fastgeo
    rng = np.random.default_rng(9)
    for ts, z, _da, _bf in _transects()[:80]:
        st, el = ts.tolist(), z.tolist()
        lo, hi = min(el), max(el)
        for stage in np.r_[lo - 1, lo, rng.uniform(lo, hi, 25), hi, hi + 1].tolist():
            assert fastgeo.flow_area(st, el, stage) == geomorph.flow_area(st, el, stage)
    # truncated sides and a flat profile
    assert fastgeo.flow_area([0.0, 1.0, 2.0], [1.0, 0.0, 1.0], 5.0) == geomorph.flow_area([0.0, 1.0, 2.0], [1.0, 0.0, 1.0], 5.0)
    assert fastgeo.flow_area([0.0, 1.0], [0.0, 0.0], 0.0) == geomorph.flow_area([0.0, 1.0], [0.0, 0.0], 0.0)


def test_fast_chain_matches_reference_chain():
    from xsgrid import derive
    for ts, z, da, bf in _transects():
        wide = min(max(8.0 * bf["width_m"], 250.0), 800.0)
        n_full = sample.n_points(wide, 1)
        full = np.linspace(-wide, wide, n_full)
        at = np.searchsorted(full, ts)
        z_full = np.full(n_full, np.nan, dtype=np.float32)
        z_full[at] = z.astype(np.float32)
        ok = np.isfinite(z_full)
        args = (da, bf["width_m"], bf["depth_m"], bf["area_m2"], bf.get("division_name"), 1)
        ts_ok, z_ok = full[ok].tolist(), z_full[ok].astype(np.float64).tolist()
        assert derive._chain(ts_ok, z_ok, args, True) == derive._chain(ts_ok, z_ok, args, False)


def test_run_window_parsing_and_membership():
    from datetime import datetime

    from xsgrid import build
    night = build.parse_window("20:00-07:00")
    assert night == (1200, 420) and build.fmt_window(night) == "20:00-07:00"
    at = lambda h, m: datetime(2026, 10, 3, h, m)  # noqa: E731
    assert build.in_window(night, at(20, 0)) and build.in_window(night, at(23, 59))
    assert build.in_window(night, at(0, 0)) and build.in_window(night, at(6, 59))
    assert not build.in_window(night, at(7, 0)) and not build.in_window(night, at(12, 0))
    day = build.parse_window("09:00-17:30")
    assert build.in_window(day, at(9, 0)) and not build.in_window(day, at(17, 30))
    assert build.in_window(None, at(12, 0))


def test_run_outside_its_window_touches_nothing(tmp_path, monkeypatch):
    from datetime import datetime

    from xsgrid import build
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "archive")
    now = datetime.now().hour * 60 + datetime.now().minute
    closed = ((now + 60) % 1440, (now + 120) % 1440)   # opens in an hour
    assert build.run(["0710"], window=closed) == 0
    assert not (tmp_path / "work").exists() and not (tmp_path / "archive").exists()


def test_lock_and_archive_checks(tmp_path, monkeypatch):
    import os

    from xsgrid import build
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "archive")
    assert build.running_pid() is None
    assert build.acquire_lock() and build.running_pid() == os.getpid()
    assert not build.acquire_lock()                       # one run at a time
    build.release_lock()
    assert build.running_pid() is None
    assert build.archive_writable()
    monkeypatch.setattr(config, "ARCHIVE", Path("Q:/no-such-drive/staf-xs"))
    assert not build.archive_writable()


def test_status_says_when_the_archive_drive_is_missing(tmp_path, monkeypatch, capsys):
    import run
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    monkeypatch.setattr(config, "ARCHIVE", Path("Q:/no-such-drive/staf-xs"))
    assert run.cmd_status(None) == 1
    out = capsys.readouterr().out
    assert "PAUSED" in out and "not connected" in out
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "archive")     # the drive is there, the folder not yet
    monkeypatch.setattr(run, "lower48", lambda: ["0101", "0102"])
    assert run.cmd_status(None) == 0
    assert "regions done 0 of 2" in capsys.readouterr().out


def test_a_run_never_starts_a_new_archive_over_finished_regions(tmp_path, monkeypatch, capsys):
    import run

    from xsgrid import build
    monkeypatch.setattr(config, "WORK", tmp_path / "work")
    monkeypatch.setattr(config, "ARCHIVE", tmp_path / "another-drive" / "staf-xs")
    (tmp_path / "work" / "metrics").mkdir(parents=True)
    (tmp_path / "work" / "metrics" / "xsm_0101.parquet").write_bytes(b"x")   # a region is done
    assert build.run(["0101"]) == 2
    assert not config.ARCHIVE.exists() and not build.lock_path().exists()
    assert run.cmd_status(None) == 1 and "no archive at" in capsys.readouterr().out


def test_read_waits_out_a_tile_another_process_holds(monkeypatch):
    from rasterio.errors import RasterioIOError
    calls = []

    def busy_twice(paths, bounds):
        calls.append(paths)
        if len(calls) < 3:
            raise RasterioIOError(f"{paths[0]}: file used by other process")
        return "mosaic"
    monkeypatch.setattr(sample, "ensure", lambda url: url)
    monkeypatch.setattr(sample, "BUSY_WAIT_S", 0.0)
    monkeypatch.setattr(sample.dem_tiles, "merge_windows", busy_twice)
    assert sample._read(["a.tif"], (0, 0, 1, 1)) == "mosaic" and len(calls) == 3

    def broken(paths, bounds):
        calls.append(paths)
        raise RasterioIOError("a.tif: not recognized as a supported file format")
    calls.clear()
    monkeypatch.setattr(sample.dem_tiles, "merge_windows", broken)
    with pytest.raises(RasterioIOError):
        sample._read(["a.tif"], (0, 0, 1, 1))
    assert len(calls) == 1                                  # any other error is not retried


def test_a_download_stops_when_its_pool_shuts_down(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from xsgrid import tilecache
    monkeypatch.setenv("XSGRID_TILECACHE", str(tmp_path))
    monkeypatch.setattr(tilecache, "CHUNK", 2)                # 4 ranges, one worker
    started, release = threading.Event(), threading.Event()

    def slow_range(u, a, b):
        started.set()
        release.wait(10)
        return b"x" * (b - a + 1)
    monkeypatch.setattr(tilecache, "_get_range", slow_range)
    pool, out = ThreadPoolExecutor(1), {}

    def download():
        try:
            tilecache.fetch("https://example.invalid/tiles/USGS_one_meter_x1y1_TEST.tif", pool, size=8)
            out["end"] = "finished"
        except Exception as exc:  # noqa: BLE001
            out["end"] = type(exc).__name__
    t = threading.Thread(target=download, daemon=True)
    t.start()
    assert started.wait(10)
    pool.shutdown(wait=False, cancel_futures=True)          # a pause: the queued ranges are cancelled
    release.set()
    t.join(30)
    assert not t.is_alive() and out["end"] == "CancelledError"
    assert not list(tmp_path.glob("*.part"))


def test_a_download_never_replaces_a_tile_already_in_place(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from xsgrid import tilecache
    monkeypatch.setenv("XSGRID_TILECACHE", str(tmp_path))
    url = "https://example.invalid/tiles/USGS_one_meter_x1y1_TEST.tif"
    dest = tilecache.local_path(url)

    def get_range(u, a, b):                                 # meanwhile another process puts its copy in place
        dest.write_bytes(b"theirs")
        return b"m" * (b - a + 1)
    monkeypatch.setattr(tilecache, "_get_range", get_range)
    with ThreadPoolExecutor(2) as pool:
        assert tilecache.fetch(url, pool, size=6) == dest
    assert dest.read_bytes() == b"theirs"
    assert not list(tmp_path.glob("*.part"))


def test_a_download_writes_only_the_ranges_that_arrived(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from xsgrid import tilecache
    monkeypatch.setenv("XSGRID_TILECACHE", str(tmp_path))
    monkeypatch.setattr(tilecache, "CHUNK", 2)                # 4 ranges
    gates = dict((a, threading.Event()) for a in (0, 2, 4, 6))

    def gated_range(u, a, b):
        gates[a].wait(10)
        return bytes([65 + a]) * (b - a + 1)
    monkeypatch.setattr(tilecache, "_get_range", gated_range)
    url = "https://example.invalid/tiles/USGS_one_meter_x1y1_TEST.tif"
    with ThreadPoolExecutor(4) as pool:
        t = threading.Thread(target=tilecache.fetch, args=(url, pool), kwargs=dict(size=8), daemon=True)
        t.start()
        for _ in range(100):
            parts = list(tmp_path.glob("*.part"))
            if parts:
                break
            time.sleep(0.05)
        assert parts and parts[0].read_bytes() == b""          # no zeros written ahead of the data (run 6)
        for gate in gates.values():
            gate.set()
        t.join(10)
    assert tilecache.local_path(url).read_bytes() == b"AACCEEGG"
    assert not list(tmp_path.glob("*.part"))



def test_a_failed_range_is_asked_again_on_a_short_timeout_and_counted(monkeypatch):
    from xsgrid import tilecache

    class Response:
        content = b"xxxx"

        def raise_for_status(self):
            pass
    asked = []

    class Session:
        def get(self, url, headers=None, timeout=None):
            asked.append(timeout)
            if len(asked) == 1:
                raise ConnectionError("stalled")              # the first ask hears nothing
            return Response()
    monkeypatch.setattr(tilecache, "_session", lambda: Session())
    monkeypatch.setattr(tilecache, "_backoff", lambda attempt: None)
    monkeypatch.setattr(tilecache, "STATS", dict(bytes=0, failed=0))
    assert tilecache._get_range("https://example.invalid/t.tif", 0, 3) == b"xxxx"
    assert tilecache.STATS == dict(bytes=4, failed=1)
    assert asked == [tilecache.TIMEOUT] * 2 and tilecache.TIMEOUT[1] <= 30

def test_eviction_spares_only_tiles_used_in_the_last_10_minutes(tmp_path, monkeypatch):
    import os
    import time

    from xsgrid import tilecache
    monkeypatch.setenv("XSGRID_TILECACHE", str(tmp_path))
    now = time.time()
    recent, older = tmp_path / "recent.tif", tmp_path / "older.tif"
    for p, age_s in ((recent, 5 * 60), (older, 15 * 60)):      # 30 min spared both: the cap never held (run 9)
        p.write_bytes(b"x" * 10)
        os.utime(p, (now - age_s, now - age_s))
    assert tilecache.evict(set(), cap_bytes=0) == 10
    assert recent.exists() and not older.exists()


def test_cache_scans_survive_a_file_going_away_mid_scan(tmp_path, monkeypatch):
    import os

    from xsgrid import tilecache
    monkeypatch.setenv("XSGRID_TILECACHE", str(tmp_path))
    old, part = tmp_path / "old.tif", tmp_path / "new.tif.1.2.part"
    real_stat, looks = Path.stat, []

    def stat(self, *, follow_symlinks=True):                # the download's .part goes at its k-th look
        if self == part:
            looks.append(self)
            if len(looks) == k and os.path.exists(part):
                os.remove(part)
        return real_stat(self, follow_symlinks=follow_symlinks)
    monkeypatch.setattr(Path, "stat", stat)
    for k in range(1, 5):                                   # run 5 stopped on usage at k = 2 (2026-10-09)
        for scan in ("usage", "evict"):
            old.write_bytes(b"x" * 10)
            os.utime(old, (1, 1))                           # long unused
            part.write_bytes(b"x" * 20)
            looks.clear()
            if scan == "usage":
                assert tilecache.usage() in (10, 30)
            else:
                assert tilecache.evict(set(), cap_bytes=0) == 10 and not old.exists()


def test_a_quad_is_read_from_the_file_usgs_serves(monkeypatch):
    base = "https://example.invalid/19/TILES/"
    listed = base + "ned19_n33x00_w088x75_P_2014/ned19_n33x00_w088x75_P_2014.img"
    renamed = base + "ned19_n33x00_w088x75_P_2014/imgned19_n33x00_w088x75_P_2014_19.img"
    normal = base + "ned19_n34x00_w088x75_Q_2012/ned19_n34x00_w088x75_Q_2012.img"
    gone = base + "ned19_n35x00_w088x75_R_2014/ned19_n35x00_w088x75_R_2014.img"
    tried = []

    def ensure(url):                                        # USGS's answer: a 403 or 404 is FileNotFoundError
        tried.append(url)
        if url not in (renamed, normal):
            raise FileNotFoundError(url)
        return url
    monkeypatch.setattr(sample, "ensure", ensure)
    monkeypatch.setattr(sample, "_QUAD_URLS", {})
    assert sample.quad_url(normal) == normal and tried == [normal]
    assert sample.quad_url(listed) == renamed
    assert sample.quad_url(gone) is None
    tried.clear()
    assert sample.quad_url(listed) == renamed and not tried    # looked up once per process

    def offline(url):
        raise IOError(f"no size for {url}")
    monkeypatch.setattr(sample, "ensure", offline)
    other = base + "ned19_n36x00_w088x75_S_2014/ned19_n36x00_w088x75_S_2014.img"
    with pytest.raises(IOError):
        sample.quad_url(other)                              # a network failure still fails the cell
    assert other not in sample._QUAD_URLS


def test_sections_touching_an_unserved_quad_take_the_10m_tiles(monkeypatch):
    from types import SimpleNamespace

    import shapely
    cat = SimpleNamespace(
        one=dict(tree=shapely.STRtree([])),                 # no 1 m tiles here
        nine=dict(name=np.asarray(["q_ok", "q_gone"], dtype=object), url=np.asarray(["u_ok", "u_gone"], dtype=object),
                  tree=shapely.STRtree(shapely.box([0.0, 1.0], [0.0, 0.0], [1.0, 2.0], [1.0, 1.0]))))
    boxes = [(0.2, 0.2, 0.4, 0.4), (1.2, 0.2, 1.4, 0.4), (0.9, 0.2, 1.1, 0.4)]   # in q_ok, in q_gone, on both
    secs = [sample.Section(i=i, x=0.0, y=0.0, nx=1.0, ny=0.0, wide=250.0, bbox4326=b) for i, b in enumerate(boxes)]
    reads = []

    def sample_group(urls, epsg, group, n_pts, pad):
        reads.append((list(urls), [s.i for s in group]))
        return [np.zeros(k) for k in n_pts]
    monkeypatch.setattr(sample, "quad_url", lambda url: "served_ok" if url == "u_ok" else None)
    monkeypatch.setattr(sample, "seamless_urls", lambda bxs: ["s10"])
    monkeypatch.setattr(sample, "sample_group", sample_group)
    counts = sample.sample_cell(cat, secs)
    assert reads == [(["served_ok"], [0]), (["s10"], [1, 2])]
    assert [(s.res, s.source, s.tiles) for s in secs] == [(3, "3dep-19", "q_ok"), (10, "3dep-13", "s10"),
                                                          (10, "3dep-13", "s10")]
    assert (counts["three"], counts["ten"], counts["none"]) == (1, 2, 0)
