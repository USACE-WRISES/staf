"""Nine evenly spaced cross-sections: the geometry metrics score on the reach
medians and the drawn default is the section nearest both medians
(2026-09-06). Pure functions, no DEM."""
from __future__ import annotations

from easi import geomorph


def _cands(ers, bhrs, fracs=None, edges=None):
    out = []
    for i, (er, bhr) in enumerate(zip(ers, bhrs)):
        c = {"entrenchment_ratio": er, "bank_height_ratio": bhr}
        if fracs is not None:
            c["position_frac"] = fracs[i]
        if edges is not None:
            c["edge_limited"] = edges[i]
        out.append(c)
    return out


def test_median_candidate_is_central_for_both_ratios():
    # ER ranks 0,1,2,4,5,6,3 and BHR ranks 0,1.5,4,3,5,6,1.5 (mid 3): index 3 scores 1
    cands = _cands([1.2, 1.5, 1.8, 2.0, 2.4, 3.0, 1.9], [1.0, 1.1, 1.3, 1.2, 1.6, 2.0, 1.1])
    assert geomorph.median_candidate(cands) == 3


def test_median_candidate_ties_go_to_the_reach_middle():
    fracs = [0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875]
    cands = _cands([2.0] * 7, [1.2] * 7, fracs)
    assert geomorph.median_candidate(cands) == 3
    # without positions the middle index wins, lowest on an even count
    assert geomorph.median_candidate(_cands([2.0] * 4, [1.2] * 4)) == 1


def test_median_candidate_falls_back_to_er_then_to_the_middle():
    cands = _cands([1.0, 2.0, 3.0, 4.0, 5.0], [None, 1.1, None, None, 1.4])
    assert geomorph.median_candidate(cands) == 2          # median ER alone
    assert geomorph.median_candidate(_cands([None] * 5, [None] * 5)) == 2
    assert geomorph.median_candidate([]) == 0


def test_reach_stats_are_per_ratio_medians_and_ranges():
    cands = _cands([1.26, 1.33, 1.34, 1.47, 1.66, 1.81, 2.24, None, None],
                   [2.0, 1.47, 2.0, 2.0, 2.0, 1.81, 2.0, 1.9, None])
    s = geomorph.reach_stats(cands)
    assert s["n"] == 9
    assert s["entrenchment_ratio"] == {"median": 1.47, "min": 1.26, "max": 2.24, "n": 7}
    assert s["bank_height_ratio"] == {"median": 2.0, "min": 1.47, "max": 2.0, "n": 8,
                                      "capped": 0, "median_capped": False, "max_capped": False}
    assert geomorph.reach_stats([]) == {"n": 0}
    assert "entrenchment_ratio" not in geomorph.reach_stats(_cands([None], [None]))
    # an even count averages the middle two
    even = geomorph.reach_stats(_cands([1.0, 2.0, 3.0, 4.0], [1.0] * 4))
    assert even["entrenchment_ratio"]["median"] == 2.5


def test_describe_reach_wording():
    s = geomorph.reach_stats(_cands([1.26, 1.47, 2.24], [1.0, 1.2, 1.4]))
    assert geomorph.describe_reach(s, "entrenchment_ratio") == \
        "reach median of 3 sections (1.26 to 2.24)"
    assert geomorph.describe_reach(s, "bank_height_ratio") == \
        "reach median of 3 sections (1.00 to 1.40)"
    assert geomorph.describe_reach(None, "entrenchment_ratio") == ""
    one = geomorph.reach_stats(_cands([1.3], [1.1]))
    assert geomorph.describe_reach(one, "entrenchment_ratio") == ""   # a median of one


def test_median_edge_limited_follows_the_er_median_section():
    ers = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert geomorph.median_edge_limited(
        _cands(ers, [1.0] * 5, edges=[True, False, False, False, True])) is False
    assert geomorph.median_edge_limited(
        _cands(ers, [1.0] * 5, edges=[False, False, True, False, False])) is True
    # an even count: either middle section
    assert geomorph.median_edge_limited(
        _cands([1.0, 2.0, 3.0, 4.0], [1.0] * 4, edges=[False, True, False, False])) is True
    assert geomorph.median_edge_limited([]) is False


def _v(width, depth=1.0):
    stations = list(range(0, 121))
    half = width / 2.0
    return stations, [max(0.0, depth * (1 - abs(x - 60) / half)) * -1 + 3.0 for x in stations]


def test_candidates_from_transects_scores_the_medians_and_draws_the_nearest():
    reach_m = 1000.0 / geomorph.FT_PER_M
    fracs = [round(i / 10, 4) for i in range(1, 10)]
    usable = [(f, *_v(14 + 4 * (i % 4))) for i, f in enumerate(reversed(fracs))]
    out = geomorph.candidates_from_transects(usable, reach_m, 12.0, division="Interior Plains")
    cands = out["candidates"]
    assert len(cands) == 9 and out["n_transects"] == 9
    assert [c["label"] for c in cands] == [f"{100 * i} ft" for i in range(1, 10)]
    assert [c["position_frac"] for c in cands] == fracs         # sorted by position
    sel = out["selected"]
    assert 0 <= sel < 9 and sel == geomorph.median_candidate(cands)
    stats = geomorph.reach_stats(cands)
    assert out["reach"] == stats
    # the top level scores the reach medians, not the drawn section's own ratios
    assert out["entrenchment_ratio"] == (stats.get("entrenchment_ratio") or {}).get("median")
    assert out["bank_height_ratio"] == (stats.get("bank_height_ratio") or {}).get("median")
    assert out["edge_limited"] == geomorph.median_edge_limited(cands)
    # the drawn section keeps its profile and its own ratios on the candidate list
    assert out["profile"] == cands[sel]["profile"]
    assert "entrenchment_ratio" in cands[sel]
    assert out["bankfull_division"] == "Interior Plains"
    assert geomorph.candidates_from_transects([], reach_m, 12.0) == {}


def test_reach_stats_flag_the_capped_bank_height_ratios():
    cands = _cands([1.5] * 5, [1.2, 2.0, 2.0, 1.4, 2.0])
    for c, capped in zip(cands, [False, True, True, False, False]):
        c["low_bank_capped"] = capped
    s = geomorph.reach_stats(cands)["bank_height_ratio"]
    assert s["capped"] == 2 and s["median_capped"] is True and s["max_capped"] is True
    assert geomorph.fmt_bhr(s["median"], s["median_capped"]) == "≥2.00"
    assert geomorph.fmt_bhr(s["median"], s["median_capped"], words=True) == "at least 2.00"
    assert geomorph.fmt_bhr(1.34, True) == "1.34" and geomorph.fmt_bhr(2.0, False) == "2.00"
    assert geomorph.fmt_bhr(None) == "n/a"
    assert geomorph.describe_reach(geomorph.reach_stats(cands), "bank_height_ratio") == \
        "reach median of 5 sections (1.20 to at least 2.00)"
    # a 2.0 median with no capped section is a plain 2.00
    plain = geomorph.reach_stats(_cands([1.5] * 3, [2.0, 2.0, 1.0]))["bank_height_ratio"]
    assert plain["capped"] == 0 and plain["median_capped"] is False
    assert geomorph.describe_reach(geomorph.reach_stats(_cands([1.5] * 3, [2.0, 2.0, 1.0])),
                                   "bank_height_ratio") == "reach median of 3 sections (1.00 to 2.00)"
