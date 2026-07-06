"""Tests for streamcurves/geomorph.py.

Ports tests/geomorph_tests.R from the R repo verbatim (same profiles, same
hand-computed expectations), adds extra hand-computed island / edge-limited /
rounding cases, and cross-checks against the sister app DEEP
(D:\\Code\\Work\\deep — the original Python source the R file was ported from),
skipping gracefully when DEEP is not importable.
"""

import math
import sys

import numpy as np
import pytest

from streamcurves.geomorph import (
    bank_height_ratio,
    bieger_coef,
    bieger_division_abbr,
    bieger_division_name,
    bieger_geometry,
    derive_from_stages,
    flow_width,
    summarize_profile,
    top_of_bank_elev,
    top_width,
    transect_entrenchment,
)

sys.path.insert(0, r"D:\Code\Work\deep")
try:
    import deep.bieger as deep_bieger
    import deep.geomorph as deep_geomorph
except Exception:  # pragma: no cover - depends on the sibling checkout
    deep_bieger = deep_geomorph = None

needs_deep = pytest.mark.skipif(
    deep_geomorph is None, reason="DEEP repo not importable from D:\\Code\\Work\\deep"
)

# Symmetric trapezoid: thalweg at station 0 (elev 0); banks at 3 (+-10);
# valley walls at 10 (+-20).  (geomorph_tests.R section 1)
ST = [-20, -10, -5, 0, 5, 10, 20]
EL = [10, 3, 1, 0, 1, 3, 10]

# Terrace profile: banks rise to 3 at +-10, then a terrace at 2.0 (> 0.5 below
# the crest), then walls.  (geomorph_tests.R section 3)
ST2 = [-30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30]
EL2 = [10, 2, 2, 3, 1, 0, 1, 3, 2, 2, 10]

# W-shaped profile with two pools (island behaviour).
STW = [0, 2, 4, 6, 8]
ELW = [3, 0, 2, 0, 3]


# --------------------------------------------------------------------------- #
# 1. top_width / flow_width on the symmetric trapezoid (geomorph_tests.R)
# --------------------------------------------------------------------------- #


def test_top_width_trapezoid_stage2():
    T, merged = top_width(ST, EL, 2)
    assert abs(T - 15) < 1e-9                     # wetted top width at stage 2 = 15
    assert len(merged) == 1
    assert abs(merged[0][0] + 7.5) < 1e-9 and abs(merged[0][1] - 7.5) < 1e-9


def test_flow_width_trapezoid_stage2():
    width, edge_limited = flow_width(ST, EL, 2)
    assert abs(width - 15) < 1e-9
    assert not edge_limited


def test_flow_width_flood_prone_stage4():
    # flood-prone stage = thalweg + 2*depth(=2) = 4 -> contiguous width spans
    # toward the walls
    width, _ = flow_width(ST, EL, 4)
    assert abs(width - (2 * (10 + 10 / 7))) < 1e-6    # = 22.857...


def test_flow_width_above_walls_edge_limited():
    width, edge_limited = flow_width(ST, EL, 12)
    assert edge_limited
    assert abs(width - 40) < 1e-9


# --------------------------------------------------------------------------- #
# 2. derive_from_stages: ER / BHR (geomorph_tests.R)
# --------------------------------------------------------------------------- #


def test_derive_from_stages_trapezoid():
    res = derive_from_stages(ST, EL, bankfull_stage=2, low_bank_stage=3, thalweg=0)
    assert res["bankfull_depth_max_m"] == 2
    assert res["fp_stage_m"] == 4
    assert abs(res["bankfull_width_m"] - 15) < 1e-9
    assert abs(res["flood_prone_width_m"] - 22.9) < 0.05
    assert res["entrenchment_ratio"] == 1.52          # 22.857/15 -> 1.52
    assert res["bank_height_ratio"] == 1.5            # (3-0)/2
    assert res["edge_limited"] is False


def test_derive_from_stages_bankfull_at_thalweg():
    # bankfull stage at/below thalweg -> no geometry
    flat = derive_from_stages(ST, EL, bankfull_stage=0, low_bank_stage=1, thalweg=0)
    assert flat["entrenchment_ratio"] is None
    assert flat["bankfull_depth_max_m"] is None
    assert flat["bankfull_width_m"] is None
    assert flat["fp_stage_m"] is None
    assert flat["edge_limited"] is False


def test_derive_from_stages_output_keys_match_r_order():
    res = derive_from_stages(ST, EL, bankfull_stage=2, low_bank_stage=3, thalweg=0)
    assert list(res) == [
        "thalweg", "bankfull_stage", "low_bank_stage", "bankfull_depth_max_m",
        "entrenchment_ratio", "bank_height_ratio", "bankfull_width_m",
        "flood_prone_width_m", "fp_stage_m", "edge_limited",
    ]


def test_derive_from_stages_default_thalweg_and_rounding():
    # thalweg defaults to min(elevs) = 0; depth/fp-stage rounded to 3 dp
    res = derive_from_stages(ST, EL, bankfull_stage=1.23456, low_bank_stage=3)
    assert res["thalweg"] == 0
    assert res["bankfull_depth_max_m"] == round(1.23456, 3)
    assert res["fp_stage_m"] == round(2 * 1.23456, 3)
    assert res["bank_height_ratio"] == round(3 / 1.23456, 2)


def test_derive_from_stages_flood_prone_edge_limited():
    st = [-10, -5, 0, 5, 10]
    el = [1, 0.5, 0, 0.5, 1]
    res = derive_from_stages(st, el, bankfull_stage=0.6, low_bank_stage=1, thalweg=0)
    assert res["edge_limited"] is True                # fp stage 1.2 tops the profile
    assert res["flood_prone_width_m"] == 20.0         # spans the whole transect
    res2 = derive_from_stages(st, el, bankfull_stage=0.4, low_bank_stage=0.5, thalweg=0)
    # hand-computed: w_bf crossings at +-4 -> 8; fp stage 0.8 crossings at +-8 -> 16
    assert res2["bankfull_width_m"] == 8.0
    assert res2["flood_prone_width_m"] == 16.0
    assert res2["entrenchment_ratio"] == 2.0
    assert res2["edge_limited"] is False


# --------------------------------------------------------------------------- #
# 3. top_of_bank_elev / bank_height_ratio / transect_entrenchment
#    (geomorph_tests.R)
# --------------------------------------------------------------------------- #


def test_top_of_bank_detects_bank_crest_not_wall():
    assert top_of_bank_elev(ST2, EL2) == 3


def test_bank_height_ratio_floodplain_connected():
    assert bank_height_ratio(ST2, EL2, 3) == 1        # (3-0)/3 = 1.0


def test_transect_entrenchment_regional_denominator():
    te = transect_entrenchment(ST, EL, d_bf=2, w_bf=15)
    assert te is not None
    assert te["er"] == 1.52
    assert te["thalweg"] == 0
    assert te["floodprone_width_m"] == 22.9
    assert te["edge_limited"] is False


def test_unusable_inputs_return_none():
    assert transect_entrenchment(ST, EL, d_bf=0, w_bf=15) is None
    assert transect_entrenchment(ST, EL, d_bf=2, w_bf=0) is None
    assert transect_entrenchment([0, 1, 2], [1, 0, 1], d_bf=1, w_bf=5) is None
    assert top_of_bank_elev([0, 1, 2], [1, 0, 1]) is None      # < 5 points
    assert bank_height_ratio(ST2, EL2, 0) is None
    assert bank_height_ratio([0, 1, 2], [1, 0, 1], 1) is None


def test_transect_entrenchment_edge_limited_shallow_profile():
    st = [-10, -5, 0, 5, 10]
    el = [1, 0.5, 0, 0.5, 1]
    te = transect_entrenchment(st, el, d_bf=1, w_bf=10)   # fp stage 2 > max elev
    assert te is not None
    assert te["edge_limited"] is True
    assert te["er"] == 2.0                                # 20 / 10
    assert te["floodprone_width_m"] == 20.0


# --------------------------------------------------------------------------- #
# 4. Bieger regional geometry (geomorph_tests.R)
# --------------------------------------------------------------------------- #


def test_bieger_usa_curve():
    usa = bieger_geometry(10, "USA")
    assert abs(usa["width_m"] - 6.07) < 0.05      # 2.70 * 10^0.352
    assert abs(usa["depth_m"] - 0.49) < 0.02      # 0.30 * 10^0.213
    assert abs(usa["area_m2"] - 3.29) < 0.05      # 0.95 * 10^0.540
    assert usa["division"] == "USA"
    assert usa["regional"] is False
    assert usa["division_name"] == "National curve"


def test_bieger_regional_curve():
    apl = bieger_geometry(10, "APL")
    assert apl["division"] == "APL"
    assert apl["regional"] is True
    assert abs(apl["width_m"] - 5.12) < 0.05
    rms = bieger_geometry(10, "RMS")
    assert rms["division_name"] == "Rocky Mountain System"


def test_bieger_unknown_division_falls_back_to_usa():
    assert bieger_geometry(10, "NOPE")["division"] == "USA"
    assert bieger_geometry(10, "apl")["division"] == "USA"    # case-sensitive, like R
    assert bieger_geometry(10, ["APL"])["division"] == "USA"  # non-string -> fallback


def test_bieger_bad_da_clamped():
    # R: suppressWarnings(as.numeric(.)) then !is.finite -> 0.01; max(da, 0.01)
    ref = bieger_geometry(0.01)
    for bad in (float("nan"), None, -5, 0, float("inf"), [1, 2], "abc"):
        got = bieger_geometry(bad)
        assert got == ref
        assert math.isfinite(got["width_m"]) and math.isfinite(got["depth_m"])
    assert bieger_geometry("10") == bieger_geometry(10)       # numeric strings coerce


def test_bieger_exact_rounding():
    usa = bieger_geometry(10, "USA")
    assert usa["width_m"] == round(2.70 * 10 ** 0.352, 2)     # R: round(, 2)
    assert usa["depth_m"] == round(0.30 * 10 ** 0.213, 3)     # R: round(, 3)
    assert usa["area_m2"] == round(0.95 * 10 ** 0.540, 2)     # R: round(, 2)


# --------------------------------------------------------------------------- #
# 5. summarize_profile (geomorph_tests.R + extras)
# --------------------------------------------------------------------------- #


def test_summarize_profile_seeds_usable_default_stages():
    sp = summarize_profile(ST, EL, da_sqkm=50, division="USA")
    assert sp["bankfull_depth_max_m"] is not None or sp["entrenchment_ratio"] is not None
    assert sp["division"] == "USA"
    assert isinstance(sp["curve_width_m"], float)


def test_summarize_profile_hand_computed():
    bf = bieger_geometry(50, "USA")                 # depth_m = 0.69 (3 dp)
    sp = summarize_profile(ST, EL, da_sqkm=50, division="USA")
    assert sp["curve_width_m"] == bf["width_m"]
    assert sp["curve_depth_m"] == bf["depth_m"] == 0.69
    assert sp["bankfull_stage"] == 0.69             # thalweg 0 + curve depth
    assert sp["low_bank_stage"] == 10               # top-of-bank (walls), > bankfull
    assert sp["bankfull_depth_max_m"] == 0.69
    # crossings at +-3.45 (bankfull) and +-5.95 (flood-prone, stage 1.38)
    assert sp["bankfull_width_m"] == 6.9
    assert sp["flood_prone_width_m"] == 11.9
    assert sp["entrenchment_ratio"] == 1.72
    assert sp["bank_height_ratio"] == 14.49         # (10-0)/0.69
    assert sp["edge_limited"] is False


def test_summarize_profile_with_bankfull_override():
    # bankfull is positional (width_m, depth_m), like R's bankfull[[1]]/[[2]]
    sp = summarize_profile(ST, EL, da_sqkm=50, bankfull=(15, 2), division="APL")
    assert sp["curve_width_m"] == 15
    assert sp["curve_depth_m"] == 2
    assert sp["division"] == "APL"                  # falls back to the argument
    assert sp["bankfull_width_m"] == 15.0           # measured at stage 2
    assert sp["entrenchment_ratio"] == 1.52
    assert sp["bank_height_ratio"] == 5.0           # low bank = tob 10; (10-0)/2


def test_summarize_profile_division_defaults_to_curve():
    sp = summarize_profile(ST, EL, da_sqkm=50)      # no division -> national curve
    assert sp["division"] == "USA"


# --------------------------------------------------------------------------- #
# Extra hand-computed island / degenerate cases
# --------------------------------------------------------------------------- #


def test_top_width_islands_vs_flow_width():
    # Two pools at stage 1: top_width sums both, flow_width only the thalweg's.
    T, merged = top_width(STW, ELW, 1)
    assert abs(T - 10 / 3) < 1e-9
    assert len(merged) == 2
    assert np.allclose(merged[0], [4 / 3, 3])
    assert np.allclose(merged[1], [5, 20 / 3])
    width, edge = flow_width(STW, ELW, 1)           # thalweg = first min (index 1)
    assert abs(width - 5 / 3) < 1e-9 and not edge
    width_r, _ = flow_width(STW, ELW, 1, thalweg_index=3)   # right pool
    assert abs(width_r - 5 / 3) < 1e-9


def test_top_width_terrace_hand_computed():
    # stage 2.5 on the terrace profile: three wetted spans, hand-computed
    T, merged = top_width(ST2, EL2, 2.5)
    assert abs(T - 33.75) < 1e-9
    assert len(merged) == 3
    width, edge = flow_width(ST2, EL2, 2.5)         # contiguous channel body only
    assert abs(width - 17.5) < 1e-9 and not edge


def test_top_width_skips_zero_dx_and_dry_profiles():
    T, merged = top_width([0, 5, 5, 10], [2, 0, 0, 2], 1)    # duplicate station
    assert abs(T - 5) < 1e-9
    assert np.allclose(merged, [[2.5, 7.5]])
    T0, merged0 = top_width(ST, EL, -1)                       # stage below thalweg
    assert T0 == 0.0 and merged0 == []


def test_flow_width_degenerate_inputs():
    assert flow_width(ST, EL, -1) == (0.0, False)             # stage below thalweg
    assert flow_width(ST, EL, 0) == (0.0, False)              # stage == thalweg
    assert flow_width([0], [1], 5) == (0.0, False)            # n < 2
    width, edge = flow_width([0, 1, 2, 3, 4], [5, 1, 0, 1, 2], 3)
    assert abs(width - 3.5) < 1e-9 and edge is True           # right side truncated


def test_numpy_array_inputs_match_lists():
    st, el = np.array(ST, dtype=float), np.array(EL, dtype=float)
    assert top_width(st, el, 2) == top_width(ST, EL, 2)
    assert flow_width(st, el, 4) == flow_width(ST, EL, 4)
    assert derive_from_stages(st, el, 2, 3, thalweg=0) == \
        derive_from_stages(ST, EL, 2, 3, thalweg=0)
    assert top_of_bank_elev(np.array(ST2), np.array(EL2)) == top_of_bank_elev(ST2, EL2)
    assert transect_entrenchment(st, el, 2, 15) == transect_entrenchment(ST, EL, 2, 15)


# --------------------------------------------------------------------------- #
# Cross-checks against DEEP (deep/geomorph.py, deep/bieger.py) — the original
# Python implementation the R file was ported from. Skipped when the sibling
# checkout is unavailable.
# --------------------------------------------------------------------------- #


@needs_deep
def test_deep_top_width_matches():
    for st, el in ((ST, EL), (ST2, EL2), (STW, ELW)):
        for stage in (0.5, 1, 2, 2.5, 4, 12):
            T_r, merged_r = top_width(st, el, stage)
            T_d, merged_d = deep_geomorph.top_width(st, el, stage)
            assert T_r == pytest.approx(T_d, abs=1e-12)
            assert len(merged_r) == len(merged_d)
            for mr, md in zip(merged_r, merged_d):
                assert mr == pytest.approx(md, abs=1e-12)


@needs_deep
def test_deep_flow_width_matches():
    for stage in (0.5, 1, 2, 4, 12):
        assert flow_width(ST, EL, stage) == deep_geomorph.flow_width(ST, EL, stage)
    # explicit thalweg index (0-based in both Python implementations)
    assert flow_width(STW, ELW, 1, thalweg_index=3) == \
        deep_geomorph.flow_width(STW, ELW, 1, thalweg_index=3)


@needs_deep
def test_deep_top_of_bank_and_bhr_match():
    assert top_of_bank_elev(ST2, EL2) == deep_geomorph.top_of_bank_elev(ST2, EL2)
    assert top_of_bank_elev(ST, EL) == deep_geomorph.top_of_bank_elev(ST, EL)
    assert bank_height_ratio(ST2, EL2, 3) == deep_geomorph.bank_height_ratio(ST2, EL2, 3)
    assert bank_height_ratio(ST, EL, 2) == deep_geomorph.bank_height_ratio(ST, EL, 2)


@needs_deep
def test_deep_transect_entrenchment_matches():
    r = transect_entrenchment(ST, EL, 2, 15)
    d = deep_geomorph.transect_entrenchment(ST, EL, 2, 15)
    assert r == d
    assert transect_entrenchment(ST, EL, 0, 15) is None
    assert deep_geomorph.transect_entrenchment(ST, EL, 0, 15) is None


@needs_deep
def test_deep_derive_from_stages_matches():
    r = derive_from_stages(ST, EL, bankfull_stage=2, low_bank_stage=3, thalweg=0)
    # DEEP renamed the low-bank stage to `floodplain_stage` (keyword-only);
    # the R port (authoritative) calls it `low_bank_stage`.
    d = deep_geomorph.derive_from_stages(ST, EL, thalweg=0,
                                         bankfull_stage=2, floodplain_stage=3)
    for key in ("thalweg", "bankfull_stage", "bankfull_depth_max_m",
                "entrenchment_ratio", "bank_height_ratio", "bankfull_width_m",
                "flood_prone_width_m", "fp_stage_m", "edge_limited"):
        assert r[key] == d[key], key
    assert r["low_bank_stage"] == d["floodplain_stage"] == 3


@needs_deep
def test_deep_bieger_table_identical():
    assert set(bieger_coef) == set(deep_bieger.COEF)
    for key, dims in bieger_coef.items():
        for dim, ab in dims.items():
            assert ab == deep_bieger.COEF[key][dim], (key, dim)
    assert bieger_division_name == deep_bieger.DIV_NAME
    assert bieger_division_abbr == deep_bieger._DIV_ABBR


@needs_deep
def test_deep_bieger_geometry_matches_national():
    # deep.bieger with no lat/lon resolves to the national curve — identical
    # output dict (keys, values, rounding) to bieger_geometry(da) here.
    for da in (0.5, 10, 250):
        assert bieger_geometry(da) == deep_bieger.bankfull_geometry(da)


@needs_deep
def test_deep_divergence_da_sanitising():
    # R (ported here) coerces NaN drainage area to 0.01; DEEP's `da_sqkm or 0.0`
    # guard lets NaN through (max(nan, 0.01) is nan). Assert the R behaviour and
    # pin DEEP's current divergence so a future DEEP fix flags this comment.
    assert bieger_geometry(float("nan")) == bieger_geometry(0.01)
    assert math.isnan(deep_bieger.bankfull_geometry(float("nan"))["width_m"])


@needs_deep
def test_deep_divergence_national_fallback_curve():
    # deep.geomorph.bankfull_geometry (national fallback tuple) is UNROUNDED and
    # uses depth = 0.30 * DA^0.315, not the Bieger USA exponent 0.213 used by the
    # R port. Assert the R-rounded values (authoritative) and pin the divergence.
    w_d, d_d = deep_geomorph.bankfull_geometry(10)
    usa = bieger_geometry(10, "USA")
    assert usa["width_m"] == round(w_d, 2)              # widths agree (a=2.70, b=0.352)
    assert usa["depth_m"] == round(0.30 * 10 ** 0.213, 3)   # R-rounded Bieger depth
    assert d_d == pytest.approx(0.30 * 10 ** 0.315)     # DEEP's different exponent
    assert usa["depth_m"] != round(d_d, 3)
