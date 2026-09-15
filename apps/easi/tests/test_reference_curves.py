"""Curve-scored quantities and report projection must use the same regional context."""
from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path

import pytest

from easi import config, methods, screening_methods as sm
from easi.metrics import biology, physicochemistry

MID = biology.HABITAT_ID
THERMAL = physicochemistry.TEMPERATURE_ID
STRATA = {"l2": "8.3", "l2_name": "Southeastern USA Plains"}
SPEC = {"set": "corridor-woody", "stratifier": "l2", "fallback": ["national"],
        "mode": "banded"}


def _curve(points, x39, x69, n=6316):
    return {"points": points, "n": n, "nMembers": n,
            "x39": x39, "x69": x69, "q25": x69, "q50": 80, "q75": 100,
            "status": "usable", "panelTier": "strict", "screen": "least-disturbed-v1"}


@pytest.fixture
def curve_catalog(monkeypatch):
    data = copy.deepcopy(config._load("screening-methods-legacy.json"))
    asset = {
        "corridor-woody": {"quantity": "woody_wsrp100", "stratifier": "l2",
            "higherIsBetter": True, "curves": {
                "8.3": _curve([[0, 0], [39, .39], [69, .69], [100, 1]], 39, 69),
                "national": _curve([[0, 0], [19.5, .39], [34.5, .69], [50, 1]],
                                   19.5, 34.5, n=20000)}},
        "flow-variability": {"quantity": "q_cv_monthly", "stratifier": "l2",
            "higherIsBetter": False, "curves": {
                "national": _curve([[0, 1], [31, .69], [61, .39], [100, 0]], 61, 31)}},
    }
    habitat = next(m for m in data["methods"] if m["metricId"] == MID)
    habitat.pop("bands")
    habitat["curve"] = copy.deepcopy(SPEC)
    thermal = next(m for m in data["methods"] if m["metricId"] == THERMAL)
    woody = next(i for i in thermal["inputs"] if i["key"] == "woodyRiparian")
    woody.pop("bands")
    woody["curve"] = copy.deepcopy(SPEC)
    monkeypatch.setattr(sm, "catalog", lambda: data)
    monkeypatch.setattr(sm, "curve_sets", lambda: asset)
    methods._catalog_methods.cache_clear()
    methods._catalog_variants.cache_clear()
    yield data, asset, habitat, thermal
    methods._catalog_methods.cache_clear()
    methods._catalog_variants.cache_clear()


def test_resolves_region_then_national_and_records_depth(curve_catalog):
    assert sm.resolve_curve(SPEC, STRATA)[::2] == ("8.3", 0)
    for strata in ({"l2": "missing"}, {}, None):
        assert sm.resolve_curve(SPEC, strata)[::2] == ("national", 1)


@pytest.mark.parametrize("value,rating,index", [
    (38.999, "Poor", .10), (39, "Fair", .55), (68.999, "Fair", .55),
    (69, "Good", .90), (1000, "Good", .90), (-100, "Poor", .10),
])
def test_higher_curve_crossings_and_rating_anchors(curve_catalog, value, rating, index):
    result = sm.evaluate(MID, {"woodyRiparian": value}, context={"strata": STRATA})
    assert (result.rating, result.index, result.combined_value) == (rating, index, value)
    assert result.trace["curves"]["method"] == {
        "set": "corridor-woody", "stratum": "8.3", "fallbackDepth": 0,
        "n": 6316, "x39": 39, "x69": 69}
    assert result.trace["context"]["strata"] == STRATA


@pytest.mark.parametrize("value,rating", [
    (31, "Good"), (31.001, "Fair"), (61, "Fair"), (61.001, "Poor"),
])
def test_lower_curve_crossings(curve_catalog, value, rating):
    spec = {**SPEC, "set": "flow-variability"}
    assert sm.score_quantity(value, {"curve": spec}, STRATA)[0] == rating


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "missing", True])
def test_invalid_quantity_is_unassessed(curve_catalog, value):
    assert sm.score_quantity(value, {"curve": SPEC}, STRATA) == (None, None, None)


def test_interpolation_not_display_crossings_governs_rating(curve_catalog):
    _, asset, _, _ = curve_catalog
    asset["corridor-woody"]["curves"]["8.3"].update(x39=1, x69=2)
    assert sm.score_quantity(50, {"curve": SPEC}, STRATA)[0] == "Fair"


def test_worst_input_and_partial_curve_keep_anchor_combined_value(curve_catalog):
    _, _, _, thermal = curve_catalog
    complete = sm.evaluate(THERMAL, {"woodyRiparian": 80, "impervious": 30},
                           context={"strata": STRATA})
    assert (complete.rating, complete.combined_value) == ("Poor", .10)
    assert complete.trace["governingInput"] == "impervious"
    assert complete.trace["curves"]["woodyRiparian"]["stratum"] == "8.3"
    thermal["formula"]["allowPartial"] = True
    partial = sm.evaluate(THERMAL, {"woodyRiparian": 50, "impervious": None},
                         context={"strata": STRATA})
    assert partial.rating == "Fair" and partial.trace["completeness"] == "partial"
    assert partial.combined_value == .55


def test_partial_disallowed_and_best_input(curve_catalog):
    _, _, _, thermal = curve_catalog
    thermal["formula"]["allowPartial"] = False
    missing = sm.evaluate(THERMAL, {"woodyRiparian": 50}, context={"strata": STRATA})
    assert missing.rating is None and missing.trace["completeness"] == "not_assessed"
    thermal["operator"] = "best_index"
    best = sm.evaluate(THERMAL, {"woodyRiparian": 80, "impervious": 30},
                       context={"strata": STRATA})
    assert (best.rating, best.combined_value) == ("Good", .90)


def test_sum_capped_curve_preserves_physical_combined_value(curve_catalog):
    _, _, habitat, _ = curve_catalog
    habitat.update(operator="sum_capped", formula={"cap": 100})
    habitat["inputs"] = [{"key": key, "label": key, "required": True}
                          for key in ("forest", "wetland")]
    result = sm.evaluate(MID, {"forest": 40, "wetland": 10}, context={"strata": STRATA})
    assert (result.rating, result.combined_value) == ("Fair", 50)


def test_criteria_and_projection_describe_resolved_reference(curve_catalog):
    _, _, habitat, _ = curve_catalog
    criteria = sm.criteria_for(habitat, {"strata": STRATA})["automated"][0]
    assert "Level II region 8.3" in criteria["bands"]["Good"]
    assert "6,316 reference reaches" in criteria["reference"]
    method = methods.resolve(MID, context={"strata": STRATA})
    assert method.mode == "scalar" and method.context == {"strata": STRATA}
    assert method.bands[1].lo == 39 and method.bands[1].hi == 69
    assert "6,316 reference reaches" in methods.band_range_texts(method)["Good"]
    assert methods.evaluate_method(method, {"woodyRiparian": 50})["rating"] == "Fair"
    national = methods.resolve(MID)
    assert "national curve" in national.reference
    assert methods.evaluate_method(national, {"woodyRiparian": 50})["rating"] == "Good"
    thermal = methods.resolve(THERMAL, context={"strata": STRATA})
    rate_woody = next(fn for key, fn, _ in thermal.per_input if key == "woodyRiparian")
    assert rate_woody(50) == "Fair"


def test_valid_curve_catalog(curve_catalog):
    assert sm.validate_catalog() == []


@pytest.mark.parametrize("change,expected", [
    ("bands", "bands xor curve"), ("mode", "mode must be banded"),
    ("set", "unknown reference curve set"), ("stratifier", "stratifier does not match"),
    ("fallback", "requires a national fallback"), ("national", "requires a national curve"),
    ("points", "invalid points"),
])
def test_curve_catalog_validation(curve_catalog, change, expected):
    _, asset, habitat, _ = curve_catalog
    if change == "bands":
        habitat["bands"] = []
    elif change == "mode":
        habitat["curve"]["mode"] = "continuous"
    elif change == "set":
        habitat["curve"]["set"] = "missing"
    elif change == "stratifier":
        habitat["curve"]["stratifier"] = "l3"
    elif change == "fallback":
        habitat["curve"]["fallback"] = []
    elif change == "national":
        del asset["corridor-woody"]["curves"]["national"]
    elif change == "points":
        asset["corridor-woody"]["curves"]["national"]["points"] = [[float("nan"), 1]]
    assert any(expected in issue for issue in sm.validate_catalog())


def test_resolution_rejects_missing_national_even_when_region_exists(curve_catalog):
    _, asset, _, _ = curve_catalog
    del asset["corridor-woody"]["curves"]["national"]
    with pytest.raises(ValueError, match="national reference curve"):
        sm.resolve_curve(SPEC, STRATA)


def test_variant_rule_replaces_parent_rule_in_both_directions(curve_catalog):
    _, _, habitat, _ = curve_catalog
    bands = sm.curve_bands(SPEC, {})
    habitat["variants"] = [{"methodKey": "band-variant", "bands": bands}]
    resolved = sm._resolved_method(habitat, "band-variant")
    assert "curve" not in resolved and resolved["bands"] == bands
    result = sm.evaluate(MID, {"woodyRiparian": 50}, variant_key="band-variant",
                         context={"strata": STRATA})
    assert result.rating == "Good" and "curves" not in result.trace
    habitat.pop("curve")
    habitat["bands"] = bands
    habitat["variants"] = [{"methodKey": "curve-variant", "curve": SPEC}]
    resolved = sm._resolved_method(habitat, "curve-variant")
    assert "bands" not in resolved and resolved["curve"] == SPEC
    assert sm.evaluate(MID, {"woodyRiparian": 50}, variant_key="curve-variant",
                       context={"strata": STRATA}).rating == "Fair"


@pytest.fixture(scope="module")
def streamcurves_interp():
    root = next(p for p in Path(__file__).resolve().parents if (p / "apps/stream-curves").is_dir())
    path = root / "apps/stream-curves/streamcurves/curves.py"
    spec = importlib.util.spec_from_file_location("_reference_streamcurves", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.interp_curve


@pytest.mark.parametrize("points", [
    [], [[10, .7]], [[100, 1], [0, 0], [50, .5]],
    [[0, -.1], [1, 1.2]], [[0, 0], [1, .2], [1, .8], [2, 1]],
    [[0, .2], [0, .8], [1, 1]], [[0, 0], [1, .2], [1, .8]],
    [{"x": None, "y": 0}, {"x": 0, "y": 1}, {"x": 5, "y": 0}],
    [[0, float("nan")], [1, .4], [2, .6]],
])
def test_interpolation_matches_streamcurves_edge_cases(streamcurves_interp, points):
    for x in (-100, 0, .5, 1, math.nextafter(1, math.inf), 1.5, 2, 39, 69, 100, 200):
        assert sm.interp_curve(points, x) == streamcurves_interp(points, x)


def test_interpolation_matches_every_packaged_point_list(streamcurves_interp):
    path = config.DATA_DIR / "reference-curves.json"
    for definition in json.loads(path.read_text(encoding="utf-8"))["sets"].values():
        for curve in definition["curves"].values():
            points = curve["points"]
            xs = sorted(float(p[0]) for p in points)
            probes = [xs[0] - 1, xs[-1] + 1, *xs,
                      *((a + b) / 2 for a, b in zip(xs, xs[1:]))]
            for x in probes:
                assert sm.interp_curve(points, x) == streamcurves_interp(points, x)
