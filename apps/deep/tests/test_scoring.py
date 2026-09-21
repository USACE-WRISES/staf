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


# Each outcome needs a direct contributor of its own, or it is unmeasured and the
# sub-index is None rather than a number (see the unassessable tests below).
ALL_DIRECT = {"f1": {"physical": "D", "chemical": "D", "biological": "D"}}


def test_rollup_direct_and_indirect_weighting():
    mapping = {"f1": {"physical": "D", "chemical": "D", "biological": "D"},
               "f2": {"physical": "D", "chemical": "i", "biological": "-"}}
    r = scoring.rollup({"f1": 15, "f2": 15}, mapping=mapping)
    assert math.isclose(r.sub_indices["physical"], 1.0)    # (15 + 15) / (15 + 15)
    assert math.isclose(r.sub_indices["chemical"], 1.0)    # (15 + 15*0.1) / (15 + 15*0.1)
    assert math.isclose(r.sub_indices["biological"], 1.0)  # f2 maps "-": no contribution
    assert r.outcomes["biological"].direct == 1
    assert math.isclose(r.ecosystem_condition_index, 1.0)


def test_rollup_partial_score_is_linear():
    r = scoring.rollup({"f1": 7.5}, mapping=ALL_DIRECT)
    assert math.isclose(r.sub_indices["physical"], 0.5)
    assert math.isclose(r.sub_indices["chemical"], 0.5)
    assert math.isclose(r.sub_indices["biological"], 0.5)


def test_an_outcome_with_no_direct_contributor_is_unassessable_not_zero():
    """Carried only by 0.1-weighted indirect signal from other disciplines, an
    outcome is not measured. Reporting 0.0 put it in the Non-Functioning band."""
    mapping = {"f1": {"physical": "D", "chemical": "i", "biological": "-"}}
    r = scoring.rollup({"f1": 15}, mapping=mapping)
    assert math.isclose(r.sub_indices["physical"], 1.0)
    assert r.sub_indices["chemical"] is None      # indirect only
    assert r.sub_indices["biological"] is None    # nothing at all
    assert r.ecosystem_condition_index is None    # and so no point claim
    assert not r.assessable


def test_nothing_entered_yet_claims_nothing():
    """This used to read ECI 0.00, in red, before a single value was entered."""
    r = scoring.rollup({}, mapping=ALL_DIRECT)
    assert r.ecosystem_condition_index is None
    assert all(v is None for v in r.sub_indices.values())


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


# --------------------------------------------------------------------------- #
# Coverage must not flatter the score (2026-09-20)
# --------------------------------------------------------------------------- #
GAPS = ["nutrient-cycling", "water-soil-quality", "population-support", "community-dynamics"]


def test_a_gap_no_longer_reads_as_a_better_site():
    """The defect this restriction exists for.

    Same site, every function 13/15 except the four the Eastern Corn Belt Plains
    cannot assess, which are 3/15. Scored against a full bundle that is 0.68,
    Functioning-at-Risk. Scored against a bundle missing those four, the ratio was
    taken over the remainder and returned 0.87, Functioning: a band flip bought by
    not measuring anything. The partial assessment now claims an interval that
    contains both answers, and names no band.
    """
    mapping = config.outcome_mapping()
    full = {fid: (3 if fid in GAPS else 13) for fid in mapping}
    complete = scoring.score_assessment(full)
    assert complete["ecosystemConditionIndex"] == 0.68
    assert scoring.index_band_label(complete["ecosystemConditionIndex"]) == "Functioning-at-Risk"

    partial = scoring.score_assessment({k: v for k, v in full.items() if k not in GAPS},
                                       unassessed=GAPS)
    assert partial["ecosystemConditionIndex"] is None          # no point claim
    low, high = partial["ecosystemConditionIndexBounds"]
    assert low <= 0.68 <= high and low <= 0.87 <= high         # both answers are inside
    assert scoring.index_band_for_bounds(low, high) is None    # so no band is named
    assert "not determinable" in scoring.index_claim(partial)
    assert "4 of 20 functions not assessed" in scoring.index_claim(partial)


def test_the_old_arithmetic_is_still_available_as_a_running_total():
    """The workbook computes it cell for cell and the rail shows entry progress
    with it. It is kept, and kept out of the claim."""
    mapping = config.outcome_mapping()
    full = {fid: (3 if fid in GAPS else 13) for fid in mapping}
    partial = scoring.score_assessment({k: v for k, v in full.items() if k not in GAPS},
                                       unassessed=GAPS)
    assert partial["ecosystemConditionIndexOverScored"] == 0.87
    assert partial["assessable"] is False


def test_an_unassessed_function_is_not_an_na_function():
    """NA is an answer and stays out of the denominator. Unassessed is a gap and
    widens the interval. The two must not collapse into each other."""
    mapping = {"f1": {"physical": "D", "chemical": "D", "biological": "D"},
               "f2": {"physical": "D", "chemical": "D", "biological": "D"}}
    na = scoring.rollup({"f1": 15}, mapping=mapping)
    assert na.ecosystem_condition_index == 1.0                 # f2 does not apply
    assert na.eci_bounds == (1.0, 1.0)

    gap = scoring.rollup({"f1": 15}, mapping=mapping, unassessed=["f2"])
    assert gap.ecosystem_condition_index is None               # f2 is unknown
    assert gap.eci_bounds == (0.5, 1.0)


def test_a_band_is_named_when_the_interval_stays_inside_one():
    """A restriction is not a refusal. A narrow gap that cannot change the answer
    still gets its band."""
    mapping = config.outcome_mapping()
    gap = "nutrient-cycling"
    scores = {fid: 15 for fid in mapping if fid != gap}
    r = scoring.score_assessment(scores, unassessed=[gap])
    low, high = r["ecosystemConditionIndexBounds"]
    assert scoring.index_band_for_bounds(low, high) == "Functioning"
    assert "(Functioning)" in scoring.index_claim(r)
