"""Code-motion guard for the derive_reach tail extraction.

``_reach_from_lines`` must reproduce the historical merge/orient/trim behavior
on a synthetic mainstem: reach trimmed to the requested length upstream of the
snap point, and the short-mainstem warning when there is not enough line.
Offline (pure shapely/geopandas)."""
from __future__ import annotations

from easi import delineation


def _line(n_pts: int = 40, lat0: float = 40.0, dlat: float = 0.0001):
    # A straight south-to-north line near (-83, 40): each step is ~11.1 m.
    from shapely.geometry import LineString
    return LineString([(-83.0, lat0 + i * dlat) for i in range(n_pts)])


def test_trims_requested_length_upstream_of_snap():
    line = _line(80)                       # ~880 m of mainstem
    mid_lat = 40.0 + 40 * 0.0001           # snap mid-line
    warnings: list = []
    gj, actual_ft, w = delineation._reach_from_lines(
        [line], [line], mid_lat, -83.0, 1000.0, warnings)
    assert gj and gj["features"]
    assert actual_ft is not None and abs(actual_ft - 1000.0) < 2.0
    assert w == []                         # enough line -> no warning


def test_short_mainstem_warns():
    line = _line(12)                       # ~122 m only
    warnings: list = []
    gj, actual_ft, w = delineation._reach_from_lines(
        [line], [line], 40.0006, -83.0, 1000.0, warnings)
    assert gj and actual_ft is not None
    assert actual_ft < 1000.0
    assert any("mainstem available upstream" in x for x in w)
