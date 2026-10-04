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
