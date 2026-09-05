"""Seven evenly spaced cross-sections with the reach median as the default
(2026-09-04). Pure functions, no DEM."""
from __future__ import annotations

from easi import geomorph


def _cands(ers, bhrs, fracs=None):
    out = []
    for i, (er, bhr) in enumerate(zip(ers, bhrs)):
        c = {"entrenchment_ratio": er, "bank_height_ratio": bhr}
        if fracs is not None:
            c["position_frac"] = fracs[i]
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


def _v(width, depth=1.0):
    stations = list(range(0, 121))
    half = width / 2.0
    return stations, [max(0.0, depth * (1 - abs(x - 60) / half)) * -1 + 3.0 for x in stations]


def test_candidates_from_transects_labels_and_selects_the_median():
    reach_m = 1000.0 / geomorph.FT_PER_M
    fracs = [i / 8 for i in range(1, 8)]
    usable = [(f, *_v(14 + 4 * (i % 4))) for i, f in enumerate(reversed(fracs))]
    out = geomorph.candidates_from_transects(usable, reach_m, 12.0, division="Interior Plains")
    cands = out["candidates"]
    assert len(cands) == 7 and out["n_transects"] == 7
    assert [c["label"] for c in cands] == ["125 ft", "250 ft", "375 ft", "500 ft",
                                           "625 ft", "750 ft", "875 ft"]
    assert [c["position_frac"] for c in cands] == fracs         # sorted by position
    sel = out["selected"]
    assert 0 <= sel < 7 and sel == geomorph.median_candidate(cands)
    assert out["entrenchment_ratio"] == cands[sel]["entrenchment_ratio"]
    assert out["bank_height_ratio"] == cands[sel]["bank_height_ratio"]
    assert out["bankfull_division"] == "Interior Plains"
    assert geomorph.candidates_from_transects([], reach_m, 12.0) == {}
