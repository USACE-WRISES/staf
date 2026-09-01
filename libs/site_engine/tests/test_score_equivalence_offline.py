"""The equivalence study's pure pieces: deterministic panel selection, the
DEEP index shift, and the inclusive pre-registered rule."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "score_equivalence_study.py"


def _script():
    spec = importlib.util.spec_from_file_location("score_equivalence_study", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cands():
    out = []
    for i in range(30):
        out.append({"station_key": f"S{i:02d}", "region": "58", "lat": 44.0,
                    "lon": -72.0, "comid": 1000 + i, "da_sqkm": float(1 + i * 10),
                    "pctimp": 0.5 if i % 3 else 5.0,
                    "damnrmstor": 100.0 if i in (4, 17, 25) else 0.0})
    return out


def test_select_panel_is_deterministic_and_stratified():
    mod = _script()
    a = mod.select_panel(_cands(), per_region=15, min_dammed=3, max_da_sqkm=None)
    b = mod.select_panel(list(reversed(_cands())), per_region=15, min_dammed=3,
                         max_da_sqkm=None)
    assert [c["station_key"] for c in a] == [c["station_key"] for c in b]
    assert len(a) == 15
    assert {c["da_tertile"] for c in a} == {0, 1, 2}
    assert any(c["urban"] for c in a) and any(not c["urban"] for c in a)
    assert sum(1 for c in a if c["dammed"]) >= 3


def test_select_panel_respects_the_size_cap_and_missing_values():
    mod = _script()
    cands = _cands()
    cands[0]["da_sqkm"] = None
    out = mod.select_panel(cands, per_region=15, min_dammed=0, max_da_sqkm=100.0)
    assert all(c["da_sqkm"] is not None and c["da_sqkm"] <= 100.0 for c in out)
    assert "S00" not in {c["station_key"] for c in out}


def test_deep_shift_on_synthetic_points():
    mod = _script()
    pts = [{"x": 0.0, "y": 1.0}, {"x": 10.0, "y": 0.0}]
    out = mod.deep_shift(pts, 2.0, 3.0)
    assert out["streamcat_index"] == 0.8 and out["engine_index"] == 0.7
    assert out["shift"] == -0.1
    assert mod.deep_shift(pts, None, 3.0)["shift"] is None


def test_verdict_is_inclusive_at_the_named_boundaries():
    mod = _script()
    assert mod.verdict(0.90, 0.90, 0.049)["interchangeable"] is True
    assert mod.verdict(0.899, 0.90, 0.049)["interchangeable"] is False
    assert mod.verdict(0.90, 0.90, 0.05)["interchangeable"] is False
    assert mod.verdict(None, 0.90, 0.01)["interchangeable"] is False
