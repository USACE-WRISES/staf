"""Offline tests for the cross-section geomorphology math (ported from xs-calc)."""
from __future__ import annotations

import pytest

from easi import geomorph


def test_top_width_simple_trapezoid():
    # flat valley at 10, V-notch channel 40..60 with thalweg 6 at 50
    stations = list(range(0, 101, 2))
    elevs = []
    for x in stations:
        if 40 <= x <= 60:
            elevs.append(6 + abs(x - 50) * (4 / 10))  # 6 at 50 rising to 10 at 40/60
        else:
            elevs.append(10.0)
    # at stage 10 the whole valley is wet -> width = 100
    T, merged = geomorph.top_width(stations, elevs, 10.0)
    assert T == pytest.approx(100.0, abs=0.01)
    # at stage 8 only the channel core is wet (elev<=8 between ~45 and ~55)
    T2, _ = geomorph.top_width(stations, elevs, 8.0)
    assert 8 <= T2 <= 12


def test_top_width_merges_islands():
    # two separate pools split by a mid bar above stage
    stations = [0, 1, 2, 3, 4, 5, 6]
    elevs = [0, 0, 5, 0, 0, 5, 0]  # bars at x=2 and x=5
    T, merged = geomorph.top_width(stations, elevs, 1.0)
    assert len(merged) >= 2  # distinct wetted spans (islands)
    assert T > 0


def test_flow_width_contiguous_simple_v():
    # V channel, thalweg 0 at x=50, slope 0.5/unit -> at stage 2 the contiguous span is 8
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]
    w, edge = geomorph.flow_width(stations, elevs, 2.0)
    assert w == pytest.approx(8.0, abs=0.01) and edge is False


def test_flow_width_excludes_disconnected_pocket():
    # the channel (thalweg 0 at x=50) plus a separate low pocket at x=20, walled off by
    # high ground at elev 5. The contiguous span across the thalweg must NOT count the
    # pocket — unlike top_width, which sums every wetted segment.
    stations = list(range(0, 101))
    elevs = []
    for x in stations:
        if x == 20:
            elevs.append(1.0)                      # disconnected pocket (below stage 3)
        elif 40 <= x <= 60:
            elevs.append(abs(x - 50) * 0.3)        # channel V, thalweg 0 at x=50
        else:
            elevs.append(5.0)                      # high ground walls the pocket off
    w, edge = geomorph.flow_width(stations, elevs, 3.0)
    t_sum, _ = geomorph.top_width(stations, elevs, 3.0)
    assert w == pytest.approx(20.0, abs=0.5)       # channel only (40..60), pocket excluded
    assert t_sum > w                               # summed top width also counts the pocket
    assert edge is False


def test_flow_width_zero_at_or_below_thalweg():
    stations = list(range(0, 11))
    elevs = [abs(x - 5) * 0.5 for x in stations]   # thalweg 0 at x=5
    assert geomorph.flow_width(stations, elevs, 0.0) == (0.0, False)


def test_flow_width_edge_limited_when_never_rises():
    # stage above every sampled point -> both sides reach the profile end -> edge_limited
    stations = list(range(0, 11))
    elevs = [abs(x - 5) * 0.1 for x in stations]   # max 0.5 at the ends
    w, edge = geomorph.flow_width(stations, elevs, 1.0)
    assert edge is True and w == pytest.approx(10.0, abs=0.01)


def test_entrenchment_connected_vs_incised():
    stations = list(range(0, 101))
    # connected: low banks (floodplain at 10), channel depth 4 (thalweg 6)
    connected = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    # incised: high banks (terrace at 14), same channel
    incised = [14.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.8 for x in stations]
    d_bf, w_bf = 2.0, 10.0
    er_c = geomorph.transect_entrenchment(connected, [connected[i] for i in range(len(connected))], d_bf, w_bf)
    # (use elevs lists directly)
    er_conn = geomorph.transect_entrenchment(stations, connected, d_bf, w_bf)
    er_inc = geomorph.transect_entrenchment(stations, incised, d_bf, w_bf)
    assert er_conn and er_inc
    # connected spreads wide at flood-prone stage -> high ER; incised stays narrow
    assert er_conn["er"] > er_inc["er"]
    assert er_conn["er"] >= 2.0          # not entrenched
    assert er_inc["er"] < 2.0            # entrenched


def test_bankfull_geometry_monotonic():
    w_small, d_small = geomorph.bankfull_geometry(5)
    w_big, d_big = geomorph.bankfull_geometry(4000)
    assert w_big > w_small > 0 and d_big > d_small > 0


def test_bank_height_ratio_connected_low():
    stations = list(range(0, 101))
    connected = [10.0 if not (45 <= x <= 55) else 8 + abs(x - 50) * 0.4 for x in stations]
    bhr = geomorph.bank_height_ratio(stations, connected, d_bf=2.0)
    assert bhr is not None and bhr <= 1.3   # bank ~2 m above thalweg / 2 m bankfull


def test_top_of_bank_elev_picks_lower_bank():
    stations = list(range(0, 101))
    # left bank crest 12, right bank crest 15, thalweg 6 at x=50
    elevs = []
    for x in stations:
        if x < 50:
            elevs.append(12.0 if x <= 40 else 12 - (x - 40) * 0.6)
        else:
            elevs.append(15.0 if x >= 60 else 6 + (x - 50) * 0.9)
    tob = geomorph.top_of_bank_elev(stations, elevs)
    assert tob == pytest.approx(12.0, abs=0.5)   # the lower of the two banks


def test_derive_from_stages_bhr_and_depth():
    stations = list(range(0, 101))
    connected = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    # bankfull at 8 (2 m above thalweg 6); floodplain = bankfull -> BHR 1.0 (connected)
    d = geomorph.derive_from_stages(stations, connected, thalweg=6.0,
                                    bankfull_stage=8.0, floodplain_stage=8.0)
    assert d["bankfull_depth_max_m"] == pytest.approx(2.0, abs=1e-6)
    assert d["bank_height_ratio"] == pytest.approx(1.0, abs=1e-6)
    assert d["entrenchment_ratio"] is not None and d["entrenchment_ratio"] > 1.0
    # incised: terrace/floodplain at 12 -> BHR = (12-6)/2 = 3.0
    d2 = geomorph.derive_from_stages(stations, connected, thalweg=6.0,
                                     bankfull_stage=8.0, floodplain_stage=12.0)
    assert d2["bank_height_ratio"] == pytest.approx(3.0, abs=1e-6)


def test_derive_from_stages_er_rises_with_flood_prone_width():
    # channel V 6->8 over [45,55] (bankfull depth 2 at thalweg 6); flood-prone stage = 10.
    stations = list(range(0, 101))

    def chan(x):
        return 6 + abs(x - 50) * 0.4 if 45 <= x <= 55 else None

    # connected: low floodplain at 8.5 (below the flood-prone stage) -> wide flood-prone width;
    # incised: terrace at 14 (above the flood-prone stage) hugging the channel -> narrow.
    connected = [c if (c := chan(x)) is not None else 8.5 for x in stations]
    incised = [c if (c := chan(x)) is not None else 14.0 for x in stations]
    er_c = geomorph.derive_from_stages(stations, connected, thalweg=6.0,
                                       bankfull_stage=8.0, floodplain_stage=8.0)["entrenchment_ratio"]
    er_i = geomorph.derive_from_stages(stations, incised, thalweg=6.0,
                                       bankfull_stage=8.0, floodplain_stage=8.0)["entrenchment_ratio"]
    assert er_i < 1.4 < er_c     # entrenched (incised) vs broad accessible floodplain (connected)


def test_derive_from_stages_invalid_bankfull_returns_none():
    stations, elevs = list(range(0, 11)), [5.0] * 11
    d = geomorph.derive_from_stages(stations, elevs, thalweg=5.0,
                                    bankfull_stage=5.0, floodplain_stage=6.0)
    assert d["entrenchment_ratio"] is None and d["bank_height_ratio"] is None


def test_balanced_profile_recenters_offset_thalweg():
    # transect sampled symmetric about the flowline vertex (0), but the channel low
    # point sits at station -50 -> a thalweg-datumed plot would be lopsided.
    stations = list(range(-200, 201, 25))                  # 17 pts, symmetric about 0
    elevs = [5.0 + abs(s - (-50)) * 0.1 for s in stations]  # V min (thalweg) at -50
    out = geomorph.balanced_profile(stations, elevs)
    assert out is not None
    out_s, out_e = out
    assert len(out_s) >= 7
    assert out_s == sorted(out_s) and len(set(out_s)) == len(out_s)   # strictly increasing
    assert out_s[0] == pytest.approx(-out_s[-1])                      # symmetric about the thalweg
    assert out_s[out_e.index(min(out_e))] == pytest.approx(0.0)       # thalweg recentred to 0


def test_balanced_profile_extends_short_side_to_taller_bank():
    # thalweg at 0; left tops out low+near (h=2 by -40, then flat); right rises to a tall
    # crest far out (h=8 by +200). The short (left) side must be shown out to the right's
    # crest distance, not cut at its own low bank.
    stations = list(range(-300, 301, 20))
    elevs = [min(2.0, abs(s) * 0.05) if s <= 0 else min(8.0, s * 0.04) for s in stations]
    out = geomorph.balanced_profile(stations, elevs)
    assert out is not None
    out_s, out_e = out
    assert out_s[out_e.index(min(out_e))] == pytest.approx(0.0)  # thalweg at center
    assert out_s[0] == pytest.approx(-out_s[-1])                # symmetric
    assert abs(out_s[-1]) == pytest.approx(200.0)              # reach = right's crest distance
    assert abs(out_s[0]) > 100                                 # left extended past its own low bank (~40)


def test_balanced_profile_trims_terrace_on_incised_reach():
    # banks crest at ~+/-30 (h=5) then a flat terrace out to +/-300 -> stay tight, not +/-300.
    stations = list(range(-300, 301, 5))
    elevs = [min(5.0, abs(s) * (5.0 / 30.0)) for s in stations]
    out = geomorph.balanced_profile(stations, elevs)
    assert out is not None
    out_s, _ = out
    assert out_s[0] == pytest.approx(-out_s[-1])
    assert abs(out_s[-1]) <= 50                                # trimmed near the bank crest (~30)


def test_balanced_profile_guards():
    assert geomorph.balanced_profile([0, 1, 2], [0, 1, 2]) is None       # < 7 points
    assert geomorph.balanced_profile([0, 1, 2, 3, 4, 5, 6], [0] * 5) is None  # length mismatch
    # thalweg at the very edge -> symmetric data limit below min_half -> None
    stations = list(range(-200, 201, 10))
    elevs = [5.0 + abs(s - (-190)) * 0.1 for s in stations]
    assert geomorph.balanced_profile(stations, elevs) is None


def test_reach_summary_injected_bankfull_overrides_national():
    stations = list(range(0, 101))
    elevs = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    rs = geomorph.reach_summary([(stations, elevs)], 50.0,
                                bankfull=(12.0, 1.5), division="Interior Plains")
    # injected bankfull DEPTH (1.5) sets the curve; bankfull WIDTH is now measured on
    # the section (edge-of-water at the bankfull stage), not the regional-curve width.
    assert rs["bankfull_depth_m"] == 1.5
    assert rs["bankfull_division"] == "Interior Plains"
    assert rs["bankfull_width_m"] is not None and rs["bankfull_width_m"] > 0


def test_reach_summary_widths_match_representative_profile():
    stations = list(range(0, 101))
    elevs = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    rs = geomorph.reach_summary([(stations, elevs)], 50.0)
    assert rs.get("flood_prone_width_m") and rs.get("bankfull_width_m")
    # ER/BHR equal derive_from_stages on the representative profile at the same defaults
    # (use the raw curve depth reach_summary used internally, not the rounded report value)
    _, d_bf = geomorph.bankfull_geometry(50.0)
    thal = rs["thalweg"]
    low_bank = max(rs.get("top_of_bank_m") or thal + d_bf, thal + d_bf)
    d = geomorph.derive_from_stages(rs["profile"]["stations"], rs["profile"]["elevs"],
                                    thalweg=thal, bankfull_stage=thal + d_bf,
                                    floodplain_stage=low_bank)
    assert rs["entrenchment_ratio"] == d["entrenchment_ratio"]
    assert rs["bank_height_ratio"] == d["bank_height_ratio"]
    assert rs["flood_prone_width_m"] == d["flood_prone_width_m"]


def test_summarize_profile_matches_derive_from_stages():
    stations = list(range(0, 101))
    elevs = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    s = geomorph.summarize_profile(stations, elevs, 50.0, division="Interior Plains")
    assert s["profile"]["stations"] == stations
    assert s["thalweg"] == pytest.approx(6.0, abs=1e-6)
    assert s["bankfull_division"] == "Interior Plains"
    _, d_bf = geomorph.bankfull_geometry(50.0)
    low_bank = max(s.get("top_of_bank_m") or s["thalweg"] + d_bf, s["thalweg"] + d_bf)
    d = geomorph.derive_from_stages(stations, elevs, thalweg=s["thalweg"],
                                    bankfull_stage=s["thalweg"] + d_bf, floodplain_stage=low_bank)
    assert s["entrenchment_ratio"] == d["entrenchment_ratio"]
    assert s["bank_height_ratio"] == d["bank_height_ratio"]
    assert s["flood_prone_width_m"] == d["flood_prone_width_m"]


def test_reach_summary_retains_profile_and_stages():
    stations = list(range(0, 101))
    elevs = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in stations]
    rs = geomorph.reach_summary([(stations, elevs)], 50.0)
    # new representative-profile keys for the hydraulics + cross-section plot
    assert rs["profile"]["stations"] and rs["profile"]["elevs"]
    assert rs["thalweg"] == pytest.approx(6.0, abs=1e-6)
    assert rs["top_of_bank_m"] == pytest.approx(10.0, abs=0.5)
    assert "fp_stage_m" in rs
    # existing aggregates unchanged
    assert "entrenchment_ratio" in rs and "bank_height_ratio" in rs


# --- area-at-stage + area-based bankfull (Bieger XS-area inversion) --------------- #
def test_flow_area_v_channel_matches_analytic():
    # symmetric V, thalweg 0 at x=50, side slope 0.5 -> area below stage h is 2*h^2
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]
    a, edge = geomorph.flow_area(stations, elevs, 2.0)
    assert a == pytest.approx(8.0, abs=0.01) and edge is False


def test_flow_area_zero_at_or_below_thalweg():
    stations = list(range(0, 11))
    elevs = [abs(x - 5) * 0.5 for x in stations]
    assert geomorph.flow_area(stations, elevs, 0.0) == (0.0, False)


def test_flow_area_monotonic_in_stage():
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]
    a1, _ = geomorph.flow_area(stations, elevs, 1.0)
    a2, _ = geomorph.flow_area(stations, elevs, 2.0)
    a3, _ = geomorph.flow_area(stations, elevs, 4.0)
    assert 0 < a1 < a2 < a3


def test_flow_area_excludes_disconnected_pocket():
    # a low pocket walled off from the channel must not add wetted area (contiguous only)
    stations = list(range(0, 101))
    elevs = []
    for x in stations:
        if x == 20:
            elevs.append(1.0)                      # disconnected pocket below the stage
        elif 40 <= x <= 60:
            elevs.append(abs(x - 50) * 0.3)        # channel V, thalweg 0 at x=50
        else:
            elevs.append(5.0)                      # high ground walls the pocket off
    a, edge = geomorph.flow_area(stations, elevs, 3.0)
    assert a == pytest.approx(30.0, abs=1.0) and edge is False   # channel only


def test_stage_for_area_recovers_depth():
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]        # thalweg 0 -> 2*h^2 = 8 at h=2
    stage, edge = geomorph.stage_for_area(stations, elevs, 8.0)
    assert stage == pytest.approx(2.0, abs=0.02) and edge is False
    a, _ = geomorph.flow_area(stations, elevs, stage)
    assert a == pytest.approx(8.0, abs=0.05)


def test_stage_for_area_edge_limited_when_target_exceeds_capacity():
    stations = list(range(0, 11))
    elevs = [abs(x - 5) * 0.1 for x in stations]         # max 0.5 -> capacity 2.5 m^2
    stage, edge = geomorph.stage_for_area(stations, elevs, 5.0)
    assert edge is True and stage == pytest.approx(max(elevs), abs=1e-6)


def test_stage_for_area_nonpositive_target_returns_thalweg():
    stations = list(range(0, 11))
    elevs = [abs(x - 5) * 0.5 for x in stations]
    stage, edge = geomorph.stage_for_area(stations, elevs, 0.0)
    assert stage == pytest.approx(0.0) and edge is False


def test_summarize_profile_area_sets_bankfull_stage():
    # a target XS area of 8 m^2 gives bankfull depth ~2 m on the V (thalweg 0),
    # overriding the injected Bieger depth (1.5) that the fallback would use.
    stations = list(range(0, 101))
    elevs = [abs(x - 50) * 0.5 for x in stations]
    s = geomorph.summarize_profile(stations, elevs, 50.0, bankfull=(12.0, 1.5),
                                   bankfull_area_m2=8.0, division="Interior Plains")
    assert s["bankfull_area_m2"] == 8.0
    assert s["bankfull_area_edge_limited"] is False
    assert s["bankfull_depth_m"] == pytest.approx(2.0, abs=0.05)   # area-derived, not 1.5


# --- profile simplification (ported from xs-calc) -------------------------- #
def test_simplify_profile_drops_collinear_run():
    st = [float(s) for s in range(0, 101)]
    el = [2.0 * s for s in st]                     # perfectly linear -> interior all collinear
    s2, e2 = geomorph.simplify_profile(st, el)
    assert (s2[0], s2[-1]) == (0.0, 100.0)          # endpoints preserved
    assert len(s2) <= 3                             # collinear interior removed


def test_simplify_profile_keeps_thalweg_vertex():
    st = [float(s) for s in range(-50, 51)]
    el = [abs(s) * 0.1 for s in st]                 # V-notch, thalweg at station 0
    s2, e2 = geomorph.simplify_profile(st, el)
    assert 0.0 in s2                                # thalweg vertex kept
    assert (s2[0], s2[-1]) == (-50.0, 50.0)
    assert len(s2) <= 5                             # straight arms collapse to few points


def test_simplify_profile_caps_at_max_points():
    st = [float(s) for s in range(0, 400)]
    el = [float(s % 2) for s in st]                 # zigzag: nothing is collinear
    s2, e2 = geomorph.simplify_profile(st, el, max_points=50)
    assert len(s2) == 50                            # Visvalingam trims to the cap
    assert (s2[0], s2[-1]) == (0.0, 399.0)          # endpoints kept


def test_simplify_profile_short_unchanged():
    st, el = [0.0, 1.0, 2.0], [1.0, 0.0, 1.0]
    assert geomorph.simplify_profile(st, el) == (st, el)
