"""Parity tests for the EASI scoring engine vs the STAF screening math."""
from __future__ import annotations

import pytest

from easi import config, scoring


# --- metric-level ---------------------------------------------------------- #
def test_rating_to_index_defaults():
    assert scoring.rating_to_index("Good") == 0.85
    assert scoring.rating_to_index("Fair") == 0.545
    assert scoring.rating_to_index("Poor") == 0.195


def test_rating_to_index_per_metric_midpoints():
    mids = {"Good": 0.9, "Fair": 0.5, "Poor": 0.1}
    assert scoring.rating_to_index("Good", mids) == 0.9


def test_rating_to_index_unknown():
    with pytest.raises(ValueError):
        scoring.rating_to_index("Excellent")


@pytest.mark.parametrize("index,expected", [
    (0.85, 13),   # Good  -> round(12.75)
    (0.545, 8),   # Fair  -> round(8.175)
    (0.195, 3),   # Poor  -> round(2.925)
    (1.0, 15),    # clamp high
    (0.0, 0),     # clamp low
    (1.5, 15),    # clamp over
])
def test_function_score(index, expected):
    assert scoring.function_score(index) == expected


# --- rollup math ----------------------------------------------------------- #
def test_rollup_uniform_all_max():
    mapping = {
        "f1": {"physical": "D", "chemical": "i", "biological": "-"},
        "f2": {"physical": "i", "chemical": "D", "biological": "i"},
        "f3": {"physical": "-", "chemical": "i", "biological": "D"},
    }
    scores = {f: 15 for f in mapping}
    res = scoring.rollup(scores, mapping)
    for key in config.OUTCOMES:
        assert res.sub_indices[key] == pytest.approx(1.0)
    assert res.ecosystem_condition_index == pytest.approx(1.0)


def test_rollup_weighting_exact():
    # Hand-computed: f1 max(15), f2 zero. Direct=1.0, indirect=0.1.
    mapping = {
        "f1": {"physical": "D", "chemical": "i", "biological": "-"},
        "f2": {"physical": "-", "chemical": "D", "biological": "i"},
    }
    scores = {"f1": 15, "f2": 0}
    res = scoring.rollup(scores, mapping)
    # physical: f1 D w1 -> 15/15 = 1.0
    assert res.sub_indices["physical"] == pytest.approx(1.0)
    # chemical: f1 i w0.1 (15*.1=1.5 / 1.5) + f2 D w1 (0 / 15) -> 1.5/16.5
    assert res.sub_indices["chemical"] == pytest.approx(1.5 / 16.5)
    # biological: f1 '-' none + f2 i w0.1 (0 / 1.5) -> 0
    assert res.sub_indices["biological"] == pytest.approx(0.0)
    assert res.ecosystem_condition_index == pytest.approx((1.0 + 1.5 / 16.5 + 0.0) / 3)


def test_rollup_direct_indirect_counts():
    mapping = {
        "f1": {"physical": "D", "chemical": "i", "biological": "-"},
        "f2": {"physical": "D", "chemical": "i", "biological": "i"},
    }
    res = scoring.rollup({"f1": 10, "f2": 10}, mapping)
    assert res.outcomes["physical"].direct == 2
    assert res.outcomes["chemical"].indirect == 2
    assert res.outcomes["biological"].direct == 0
    assert res.outcomes["biological"].indirect == 1


def test_rollup_skips_unmapped_function():
    mapping = {"f1": {"physical": "D", "chemical": "-", "biological": "-"}}
    res = scoring.rollup({"f1": 15, "ghost": 15}, mapping)
    assert res.sub_indices["physical"] == pytest.approx(1.0)


# --- full assessment over the real 20 EASI metrics ------------------------- #
def _all(rating: str) -> dict[str, str]:
    return {mid: rating for mid in config.metrics_by_id()}


def test_score_assessment_all_good():
    res = scoring.score_assessment(_all("Good"))
    assert len(res["metrics"]) == 20
    assert len(res["functionScores"]) == 20
    assert set(res["functionScores"].values()) == {13}
    for key in config.OUTCOMES:
        assert res["subIndices"][key] == 0.87  # 13/15 = 0.8667 -> 0.87
    assert res["ecosystemConditionIndex"] == 0.87


def test_score_assessment_all_fair():
    res = scoring.score_assessment(_all("Fair"))
    assert set(res["functionScores"].values()) == {8}
    assert res["ecosystemConditionIndex"] == 0.53  # 8/15 = 0.5333 -> 0.53


def test_score_assessment_all_poor():
    res = scoring.score_assessment(_all("Poor"))
    assert set(res["functionScores"].values()) == {3}
    assert res["ecosystemConditionIndex"] == 0.20  # 3/15 = 0.20


def test_score_assessment_degrades_on_missing():
    ratings = {"catchment-hydrology-impervious-surface-cover": "Good"}
    res = scoring.score_assessment(ratings)
    assert len(res["metrics"]) == 1
    assert res["functionScores"] == {"catchment-hydrology": 13}
    # physical sub-index built from the single direct function only
    assert 0.0 <= res["ecosystemConditionIndex"] <= 1.0


def test_score_assessment_ignores_none_rating():
    ratings = _all("Good")
    ratings["water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl"] = None
    res = scoring.score_assessment(ratings)
    assert len(res["metrics"]) == 19


# --- presentation helpers -------------------------------------------------- #
@pytest.mark.parametrize("value,expected", [
    (0.10, "#f5b5b5"), (0.39, "#f5b5b5"),
    (0.40, "#f5e7a6"), (0.69, "#f5e7a6"),
    (0.70, "#c8d9f2"), (1.0, "#c8d9f2"),
])
def test_index_band_color(value, expected):
    assert scoring.index_band_color(value) == expected


@pytest.mark.parametrize("value,expected", [
    (0.10, "Non-Functioning"), (0.39, "Non-Functioning"), (0.20, "Non-Functioning"),
    (0.40, "Functioning-at-Risk"), (0.69, "Functioning-at-Risk"), (0.53, "Functioning-at-Risk"),
    (0.70, "Functioning"), (0.87, "Functioning"), (1.0, "Functioning"),
])
def test_index_band_label(value, expected):
    assert scoring.index_band_label(value) == expected


@pytest.mark.parametrize("value,expected", [
    (3, "#f5b5b5"), (5, "#f5b5b5"),
    (8, "#f5e7a6"), (10, "#f5e7a6"),
    (13, "#c8d9f2"), (15, "#c8d9f2"),
])
def test_function_score_band_color(value, expected):
    assert scoring.function_score_band_color(value) == expected


@pytest.mark.parametrize("value,expected", [
    (0, "NF"), (5, "NF"),        # 0-5 Non-Functioning
    (6, "AR"), (10, "AR"),       # 6-10 At-Risk (boundary 5/6 and 10/11)
    (11, "F"), (15, "F"),        # 11-15 Functioning
])
def test_function_score_band_label(value, expected):
    assert scoring.function_score_band_label(value) == expected


# --- registry / data consistency ------------------------------------------ #
def test_registry_consistency():
    assert config.validate_registry() == []


def test_every_metric_function_in_cwa_mapping():
    mapping = config.cwa_mapping()
    for m in config.easi_metrics()["metrics"]:
        assert m["functionId"] in mapping
