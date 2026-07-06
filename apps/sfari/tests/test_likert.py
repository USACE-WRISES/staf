"""Tests for the optional Likert -> function-score auto-suggest."""
from sfari import config, scoring


def test_average_of_two():
    # SA=14, A=11 -> mean 12.5
    assert scoring.likert_to_score(["Strongly Agree", "Agree"]) == 12.5


def test_na_and_unknown_are_dropped():
    # NA excluded; mean(14, 8) = 11.0
    assert scoring.likert_to_score(["Strongly Agree", "Not Applicable", "Neutral"]) == 11.0


def test_all_na_or_empty_is_none():
    assert scoring.likert_to_score([]) is None
    assert scoring.likert_to_score(["Not Applicable"]) is None
    assert scoring.likert_to_score([None]) is None


def test_full_ladder_average():
    # SA,A,N,D,SD -> mean(14,11,8,5,2)=8.0
    assert scoring.likert_to_score(list(config.LIKERT_ORDER)) == 8.0


def test_numeric_map_matches_doc():
    assert config.LIKERT_NUMERIC == {
        "Strongly Agree": 14, "Agree": 11, "Neutral": 8, "Disagree": 5, "Strongly Disagree": 2
    }
