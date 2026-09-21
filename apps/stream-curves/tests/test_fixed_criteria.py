"""Fixed criteria for pressure metrics (rule CURVE-11): EASI's bands as a curve.

The load-bearing test is ``test_boundary_values_land_in_the_same_class_as_easi``:
the whole point of inheriting EASI's criteria is that the two tiers cannot
contradict each other, and DEEP's ``<=`` class rule makes that a question of
where exactly each anchor sits.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from streamcurves import fixed_criteria as fc
from streamcurves import methodology

_DEEP = Path(__file__).resolve().parents[2] / "deep"

EASI_TO_DEEP = {"Good": "Functioning", "Fair": "Functioning-at-Risk", "Poor": "Non-Functioning"}


def _entries() -> dict:
    return fc.load_fixed_criteria()["metrics"]


# --------------------------------------------------------------------------- #
# the file is the catalog's
# --------------------------------------------------------------------------- #
def test_the_generated_file_matches_the_vendored_catalog():
    assert fc.criteria_drift() == []


def test_an_edited_band_is_reported(monkeypatch):
    edited = copy.deepcopy(fc.load_fixed_criteria())
    edited["metrics"]["pctimp2019ws"]["bands"][1]["max"] = 30
    monkeypatch.setattr(fc, "load_fixed_criteria", lambda: edited)
    assert any("pctimp2019ws.bands[1].max" in p for p in fc.criteria_drift())


def test_the_methodology_mirror_check_covers_the_fixed_criteria(monkeypatch):
    edited = copy.deepcopy(fc.load_fixed_criteria())
    edited["metrics"]["rddensws"]["points"][1] = [1.5, 0.69]
    monkeypatch.setattr(fc, "load_fixed_criteria", lambda: edited)
    assert any("fixed_criteria.metrics.rddensws.points" in p
               for p in methodology.mirror_drift())


def test_the_pressure_metrics_are_the_five_the_owner_chose():
    assert set(_entries()) == {"pctimp2019ws", "pctag2019ws", "rddensws", "dorws",
                               "nid_dams_1mi"}
    # wetland cover is an expectation metric and keeps a reference curve
    assert not fc.is_fixed("pctwet2019ws")


# --------------------------------------------------------------------------- #
# the curves
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,points", [
    ("pctimp2019ws", [[0.0, 1.0], [10.0, 0.69], [25.005, 0.39], [44.5115, 0.0]]),
    ("pctag2019ws", [[0.0, 1.0], [30.0, 0.69], [50.005, 0.39], [76.0115, 0.0]]),
    ("rddensws", [[0.0, 1.0], [1.0, 0.69], [3.0, 0.39], [5.6, 0.0]]),
    ("dorws", [[0.0, 1.0], [2.0, 0.69], [15.005, 0.39], [31.9115, 0.0]]),
    ("nid_dams_1mi", [[0.0, 1.0], [1.0, 0.545], [2.0, 0.195], [3.0, 0.0]]),
])
def test_the_curves_are_pinned(key, points):
    got = _entries()[key]["points"]
    assert len(got) == len(points)
    for (gx, gy), (wx, wy) in zip(got, points):
        assert gx == pytest.approx(wx, abs=1e-6) and gy == pytest.approx(wy, abs=1e-9)


@pytest.mark.parametrize("key", ["pctimp2019ws", "pctag2019ws", "rddensws", "dorws",
                                 "nid_dams_1mi"])
def test_zero_pressure_scores_one_and_the_curve_only_falls(key):
    e = _entries()[key]
    assert fc.interpolate(e, 0.0) == pytest.approx(1.0)
    xs = [p[0] for p in e["points"]]
    ys = [p[1] for p in e["points"]]
    assert xs == sorted(xs) and len(set(xs)) == len(xs)
    assert ys == sorted(ys, reverse=True)
    assert 0.0 <= min(ys) and max(ys) <= 1.0


def _grid(entry: dict) -> list[float]:
    """Values on the reporting grid around each boundary, and well away from it."""
    step = float(entry["resolution"])
    out = [0.0]
    for b in (entry.get("good_fair"), entry.get("fair_poor")):
        if b is None:
            continue
        for k in (-50, -2, -1, 0, 1, 2, 50):
            out.append(round(float(b) + k * step, 6))
    far = entry["points"][-1][0]
    out += [far, far * 2]
    return [v for v in out if v >= 0]


@pytest.mark.parametrize("key", ["pctimp2019ws", "pctag2019ws", "rddensws", "dorws"])
def test_boundary_values_land_in_the_same_class_as_easi(key):
    e = _entries()[key]
    for v in _grid(e):
        easi = fc.class_of(e, v)
        deep = fc.deep_class(fc.interpolate(e, v))
        assert EASI_TO_DEEP[easi] == deep, (key, v, easi, deep)


def test_dam_counts_land_in_the_same_class_as_easi():
    e = _entries()["nid_dams_1mi"]
    for n, want in ((0, "Good"), (1, "Fair"), (2, "Poor"), (3, "Poor"), (7, "Poor")):
        assert fc.class_of(e, n) == want
        assert fc.deep_class(fc.interpolate(e, n)) == EASI_TO_DEEP[want]


def test_the_owner_examples_no_longer_contradict_easi():
    """2 percent impervious scored 0 in the Northeastern Highlands and 85 percent
    row crops scored 0.70 in the Eastern Corn Belt Plains (published v6, v5)."""
    imp, ag = _entries()["pctimp2019ws"], _entries()["pctag2019ws"]
    assert fc.interpolate(imp, 2.0) == pytest.approx(0.938, abs=1e-3)
    assert fc.class_of(imp, 2.0) == "Good"
    assert fc.interpolate(ag, 85.0) == 0.0
    assert fc.class_of(ag, 85.0) == "Poor"


def test_deeps_own_interpolator_agrees():
    if not (_DEEP / "deep" / "curves.py").exists():
        pytest.skip("apps/deep is not beside this app")
    sys.path.insert(0, str(_DEEP))
    try:
        from deep.curves import interp_curve
    finally:
        sys.path.remove(str(_DEEP))
    for key, e in _entries().items():
        pts = [{"x": x, "y": y} for x, y in e["points"]]
        for v in _grid(e) if not e.get("count") else [0, 1, 2, 3, 5]:
            assert interp_curve(pts, v) == pytest.approx(fc.interpolate(e, v), abs=1e-12), (key, v)


# --------------------------------------------------------------------------- #
# what the pipeline consumes
# --------------------------------------------------------------------------- #
def test_fixed_metrics_never_get_a_sample_confidence():
    row = fc.curve_row("rddensws")
    assert row["n_reference"] is None and row["curve_source"] == "fixed_criteria"
    assert row["curve_status"] == "complete"
    assert list(row["curve_points"].columns) == ["point_order", "metric_value", "index_score"]


def test_metric_config_entries_mark_the_role():
    cfg = fc.metric_config_entries()
    assert set(cfg) == set(_entries())
    for entry in cfg.values():
        assert entry["metric_role"] == fc.METRIC_ROLE
        assert entry["criteria_basis"] == fc.CRITERIA_BASIS
        assert entry["higher_is_better"] is False
        assert "—" not in entry["notes"]


def test_the_criteria_source_carries_citations_and_limitations():
    src = fc.criteria_source(_entries()["pctimp2019ws"])
    assert [b["rating"] for b in src["bands"]] == ["Good", "Fair", "Poor"]
    assert any(c["key"] == "schueler-1994" for c in src["citations"])
    assert src["limitations"]
