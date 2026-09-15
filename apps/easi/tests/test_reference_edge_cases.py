"""Reference presentation must disclose rounding without changing interpolation."""
from __future__ import annotations

import pytest

from easi import config, geo, screening_methods as sm


@pytest.mark.parametrize("slope", [True, False])
def test_boolean_slope_is_unknown(slope):
    assert geo.slope_class(slope) is None
    assert geo.strata_for("45", slope=slope)["slope_class"] is None


def test_approximate_crossing_label_does_not_change_scoring(monkeypatch):
    spec = {"set": "synthetic", "stratifier": "l2", "mode": "banded", "fallback": ["national"]}
    curve = {"points": [[0, 0], [1, .7], [2, 1]], "n": 100,
             "x39": .557143, "x69": .985714}
    monkeypatch.setattr(sm, "curve_sets", lambda: {"synthetic": {
        "stratifier": "l2", "higherIsBetter": True, "curves": {"national": curve}}})
    assert sm.curve_reference(spec) == "national curve, 100 reference reaches; crossings are approximate"
    # The six-place displayed crossing sits just below the true .69 crossing.
    assert sm.score_quantity(curve["x69"], {"curve": spec})[0] == "Fair"
    assert sm.score_quantity(.985715, {"curve": spec})[0] == "Good"


def test_fixed_legacy_bands_have_no_approximate_curve_note():
    legacy = config._load("screening-methods-legacy.json")
    method = next(m for m in legacy["methods"] if m["operator"] == "threshold" and m.get("bands"))
    criteria = sm.criteria_for(method)
    assert criteria["automated"]
    assert all("reference" not in block for block in criteria["automated"])
    assert "crossings are approximate" not in str(criteria)
