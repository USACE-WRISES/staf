"""Rollup parity + the scoring-convention guard (indirect 0.10, 0-15 scale)."""
import math

from deep import config, scoring


def test_scoring_convention_guard():
    # Reject any drift toward the staf/tiered-approach.md 0.25 / 0-10 variant.
    assert config.WEIGHTS["D"] == 1.0
    assert config.WEIGHTS["i"] == 0.10
    assert config.WEIGHTS["-"] == 0.0
    assert config.FUNCTION_SCORE_MAX == 15
    assert config.validate() == []


def test_rollup_direct_and_indirect_weighting():
    mapping = {"f1": {"physical": "D", "chemical": "i", "biological": "-"}}
    r = scoring.rollup({"f1": 15}, mapping=mapping)
    assert math.isclose(r.sub_indices["physical"], 1.0)   # 15*1.0 / 15*1.0
    assert math.isclose(r.sub_indices["chemical"], 1.0)   # 15*0.1 / 15*0.1
    assert r.sub_indices["biological"] == 0.0
    assert math.isclose(r.ecosystem_condition_index, (1.0 + 1.0 + 0.0) / 3)


def test_rollup_partial_score_is_linear():
    mapping = {"f1": {"physical": "D", "chemical": "i", "biological": "-"}}
    r = scoring.rollup({"f1": 7.5}, mapping=mapping)
    assert math.isclose(r.sub_indices["physical"], 0.5)
    assert math.isclose(r.sub_indices["chemical"], 0.5)


def test_na_function_excluded_from_numerator_and_denominator():
    mapping = {
        "f1": {"physical": "D", "chemical": "-", "biological": "-"},
        "f2": {"physical": "D", "chemical": "-", "biological": "-"},
    }
    # f2 omitted entirely (NA): denominator should not include it.
    r = scoring.rollup({"f1": 15}, mapping=mapping)
    assert math.isclose(r.sub_indices["physical"], 1.0)
    assert r.outcomes["physical"].direct == 1  # only f1 counted


def test_score_assessment_shape():
    mapping = {"f1": {"physical": "D", "chemical": "i", "biological": "-"}}
    # score_assessment uses the real outcome mapping; here just check keys/structure.
    out = scoring.score_assessment({})
    for key in ("functionScores", "subIndices", "outcomes", "categorySubIndices",
                "ecosystemConditionIndex"):
        assert key in out
    assert set(out["subIndices"]) == set(config.OUTCOMES)
