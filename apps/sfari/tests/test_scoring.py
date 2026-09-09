"""Scoring-rollup parity tests.

The golden case is the SFARI document's own worked example (numerical-code table),
which must reproduce the published sub-indices exactly:
    Physical 0.55 / Chemical 0.70 / Biological 0.30 / ECI 0.52
using the operative outcome mapping (data/sfari-outcome-mapping.json).
"""
import pytest

from sfari import config, scoring

# Function scores from the doc's worked example (docx table[24]).
DOC_EXAMPLE = {
    "catchment-hydrology": 8,
    "surface-water-storage": 15,
    "reach-inflow": 7,
    "streamflow-regime": 12,
    "low-flow-baseflow-dynamics": 4,
    "high-flow-dynamics": 11,
    "floodplain-connectivity": 9,
    "hyporheic-connectivity": 13,
    "channel-evolution": 5,
    "channel-floodplain-dynamics": 10,
    "sediment-continuity": 6,
    "bed-composition-bedform-dynamics": 5,
    "light-thermal-regime": 12,
    "carbon-processing": 13,
    "nutrient-cycling": 13,
    "water-soil-quality": 10,
    "habitat-provision": 2,
    "population-support": 3,
    "community-dynamics": 4,
    "watershed-connectivity": 1,
}


def test_golden_worked_example():
    res = scoring.score_assessment(DOC_EXAMPLE)
    assert res["subIndices"]["physical"] == 0.55
    assert res["subIndices"]["chemical"] == 0.70
    assert res["subIndices"]["biological"] == 0.30
    assert res["ecosystemConditionIndex"] == 0.52


def test_all_functioning():
    scores = {fid: 15 for fid in config.functions_by_id()}
    res = scoring.score_assessment(scores)
    assert res["subIndices"] == {"physical": 1.0, "chemical": 1.0, "biological": 1.0}
    assert res["ecosystemConditionIndex"] == 1.0


def test_all_zero():
    scores = {fid: 0 for fid in config.functions_by_id()}
    res = scoring.score_assessment(scores)
    assert res["ecosystemConditionIndex"] == 0.0


def test_the_denominator_is_every_function_scored_or_not():
    """An unscored function counts as zero (2026-09-08), so the denominator is a
    constant: 15 * the summed weight of all 20 functions, never a subset."""
    both = scoring.rollup({"catchment-hydrology": 0, "surface-water-storage": 15})
    assert both.outcomes["physical"].max == 160.5           # 10 Direct + 7 indirect
    assert both.outcomes["chemical"].max == 67.5
    assert both.outcomes["biological"].max == 82.5
    # 15 on one Physical-Direct function against the full denominator, not against
    # the two functions that happen to be in the dict.
    assert scoring.round2(both.sub_indices["physical"]) == 0.09

    # The denominator does not move when a function is left out of the dict; only
    # the numerator does. This is what used to change it.
    one = scoring.rollup({"catchment-hydrology": 0})
    assert one.outcomes["physical"].max == 160.5
    assert one.sub_indices["physical"] == 0.0


def test_none_scores_count_as_zero():
    """An explicit None and an absent key mean the same thing: zero."""
    explicit = scoring.rollup({"catchment-hydrology": 15, "surface-water-storage": None})
    absent = scoring.rollup({"catchment-hydrology": 15})
    assert scoring.round2(explicit.sub_indices["physical"]) == 0.09
    assert explicit.sub_indices == absent.sub_indices


def test_the_rollup_reproduces_the_calculator_arithmetic():
    """The point of counting a blank as zero: the app and the SFARI worksheet now
    compute the same number.

    The worksheet's sub-index is SUMIF(AH8:AH27)/SUM(AK8:AK27), where AH is
    score * weight per function and AK is 15 * weight for all 20 unconditionally.
    That is written out here in Python and compared against the rollup.
    """
    mapping, weights = config.outcome_mapping(), config.WEIGHTS
    partial = {"catchment-hydrology": 11, "sediment-continuity": 4,
               "habitat-provision": 15, "nutrient-cycling": 7}
    res = scoring.rollup(partial)
    for outcome in config.OUTCOMES:
        num = sum(partial.get(fid, 0) * weights[codes[outcome]]
                  for fid, codes in mapping.items())
        den = sum(config.FUNCTION_SCORE_MAX * weights[codes[outcome]]
                  for fid, codes in mapping.items())
        assert res.sub_indices[outcome] == pytest.approx(num / den)


def test_a_complete_assessment_is_unaffected_by_the_change():
    """The rule only moves partial assessments. With every function scored the
    old exclude-unscored rule and the new count-as-zero rule are identical."""
    scores = {fid: 8 for fid in config.functions_by_id()}
    res = scoring.rollup(scores)
    assert scoring.round2(res.sub_indices["physical"]) == 0.53
    assert scoring.round2(res.ecosystem_condition_index) == 0.53


def test_function_score_bands():
    assert scoring.function_score_band_label(3) == "NF"
    assert scoring.function_score_band_label(8) == "AR"
    assert scoring.function_score_band_label(14) == "F"


def test_data_files_consistent():
    assert config.validate() == []
    assert len(config.functions()) == 20
    # 80, not 82: two fifth-metrics are excluded so the app's metric set matches the
    # four-rows-per-function SFARI calculator (see scripts/build_sfari_data.py).
    assert len(config.metrics()) == 80
    assert len(config.desktop_metrics()) == 26
    from collections import Counter
    assert set(Counter(m["functionId"] for m in config.metrics()).values()) == {4}
