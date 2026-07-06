"""Scoring-rollup parity tests.

The golden case is the SFARI document's own worked example (numerical-code table),
which must reproduce the published sub-indices exactly:
    Physical 0.55 / Chemical 0.70 / Biological 0.30 / ECI 0.52
using the operative outcome mapping (data/sfari-outcome-mapping.json).
"""
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


def test_na_exclusion_changes_denominator():
    # Two Physical-Direct functions; one 0, one 15 -> physical 0.5.
    both = scoring.rollup({"catchment-hydrology": 0, "surface-water-storage": 15})
    assert scoring.round2(both.sub_indices["physical"]) == 0.5
    # Marking surface-water-storage NA (omitting it) -> physical 0.0 (denominator drops).
    one = scoring.rollup({"catchment-hydrology": 0})
    assert one.sub_indices["physical"] == 0.0


def test_none_scores_are_skipped():
    res = scoring.rollup({"catchment-hydrology": 15, "surface-water-storage": None})
    # Only the scored Physical-Direct function counts -> 1.0.
    assert res.sub_indices["physical"] == 1.0


def test_function_score_bands():
    assert scoring.function_score_band_label(3) == "NF"
    assert scoring.function_score_band_label(8) == "AR"
    assert scoring.function_score_band_label(14) == "F"


def test_data_files_consistent():
    assert config.validate() == []
    assert len(config.functions()) == 20
    assert len(config.metrics()) == 82
    assert len(config.desktop_metrics()) == 26
