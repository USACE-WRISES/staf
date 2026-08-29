"""Reach-trim parity: the engine's ported merge/orient/trim must reproduce
EASI's ``_reach_from_lines`` output exactly (skipped without the source)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from site_engine.geometry import reach_from_lines

_EASI = Path(__file__).resolve().parents[3] / "apps" / "easi"


def _line(n=60, lat0=40.0, dlat=0.0001):
    from shapely.geometry import LineString
    return LineString([(-83.0, lat0 + i * dlat) for i in range(n)])


def test_trims_to_length():
    line = _line(80)
    gj, actual_ft, w = reach_from_lines([line], [line], 40.004, -83.0,
                                        1000.0, [])
    assert gj and abs(actual_ft - 1000.0) < 2.0 and w == []


@pytest.mark.skipif(not _EASI.is_dir(), reason="EASI source not present")
def test_parity_with_easi():
    sys.path.insert(0, str(_EASI))
    from easi import delineation as easi_delineation

    for n, snap_lat in ((80, 40.004), (12, 40.0006)):
        line = _line(n)
        ours = reach_from_lines([line], [line], snap_lat, -83.0, 1000.0, [])
        theirs = easi_delineation._reach_from_lines(
            [line], [line], snap_lat, -83.0, 1000.0, [])
        assert ours[0] == theirs[0]
        assert ours[1] == theirs[1]
        assert ours[2] == theirs[2]
