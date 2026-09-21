"""Per-metric reference pools with comparable borrowing (rules REF-05, 06, 07).

Synthetic frames, so every rule is exercised without the archive: the ladder
picks the narrowest level that supports the metric, a borrowed station must be
comparable, no level means insufficient, and the local best-available stations
never reach a pool.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import reference_pool as rp

CFG = {
    "envelope": {"basis": "target_frame_stations", "log10": ["drainage_area_sqkm"],
                 "slope_floor": 0.00001},
    "lithology": {"dominance_min_pct": 50, "target_share_min": 0.10},
    "families": {"channel_form": {"covariates": ["drainage_area_sqkm"], "lithology": True},
                 "biology": {"covariates": ["drainage_area_sqkm"], "lithology": False}},
    "metric_family": {"m_form": "channel_form", "m_bio": "biology"},
    "local_comparison": {"label": "Local best-available comparison (not reference)",
                         "min_n": 10,
                         "pressure_variables": ["pctimp2019ws", "agriculture_ws"]},
}
SETTINGS = {"adequate": 20, "exploratory": 10, "quantiles": (0.025, 0.975),
            "min_self_coverage": 0.80, "stratum_min_n": 15}


def _stations(prefix, n, *, l3, l2="8.3", l1="8", strict=True, relaxed=None, da=25.0,
              lith="noncarb_resid", imp=0.5, ag=5.0, start=0, flat=False):
    # No jitter: a target's own spread defines its envelope, so the fixtures say
    # exactly which donors sit inside it. A scalar ``da`` spreads the stations
    # evenly over 0.4 to 2.5 times that size, and ``flat`` puts every station
    # at exactly ``da`` (a donor that is certainly inside a spread target).
    das = (np.asarray(da, dtype=float) if not np.isscalar(da)
           else np.full(n, float(da)) if flat else np.linspace(0.4 * da, 2.5 * da, n))
    return pd.DataFrame({
        "station_key": [f"{prefix}{i + start:03d}" for i in range(n)],
        "l3": l3, "l3_name": f"L3 {l3}", "l2": l2, "l2_name": f"L2 {l2}",
        "l1": l1, "l1_name": f"L1 {l1}", "huc8": [f"{prefix}{i % 7}" for i in range(n)],
        "huc12": [f"{prefix}{i:04d}" for i in range(n)],
        "drainage_area_sqkm": das, "lith_group": lith,
        "pass_strict": strict, "pass_relaxed": strict if relaxed is None else relaxed,
        "fail_strict": "" if strict else "agriculture_ws",
        "pctimp2019ws": imp, "agriculture_ws": ag, "source_cycle": "2324"})


def _frame(*parts) -> pd.DataFrame:
    return pd.concat(parts, ignore_index=True)


def _values(frame, *, missing=()) -> pd.Series:
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(20.0, 4.0, len(frame)), index=frame["station_key"].astype(str))
    s[list(missing)] = np.nan
    return s


def _choose(metric, frame, target, **kw):
    values = kw.pop("values", None)
    values = _values(frame) if values is None else values
    return rp.choose_pool(metric, values, frame, target,
                          profile=rp.family_profile(metric, CFG), cfg=CFG,
                          settings=SETTINGS, **kw)


# --------------------------------------------------------------------------- #
# the ladder
# --------------------------------------------------------------------------- #
def test_a_self_sufficient_region_stays_local():
    frame = _frame(_stations("A", 30, l3="58", l2="5.3", l1="5"),
                   _stations("B", 40, l3="59", l2="5.3", l1="5"))
    d, ledger = _choose("m_form", frame, "58")
    assert d.status == rp.STATUS_LOCAL and d.level == "l3" and d.region_code == "58"
    assert d.n_usable == 30 and d.n_local == 30 and d.disposition == "adequate"
    assert d.transfer_risk == rp.RISK_NONE and d.transfer_note == ""
    assert set(d.station_ids) == {f"A{i:03d}" for i in range(30)}
    # the wider levels were never needed, so they were never tried
    assert set(ledger["level"]) == {"l3"}


def test_a_thin_region_borrows_from_its_level_ii_parent():
    frame = _frame(_stations("T", 3, l3="71"), _stations("T", 38, l3="71", strict=False, start=3),
                   _stations("D", 30, l3="65", flat=True))
    d, ledger = _choose("m_form", frame, "71")
    assert d.status == "borrowed_l2" and d.level == "l2" and d.region_code == "8.3"
    assert d.n_usable == 33 and d.n_local == 3
    assert "Level II 8.3" in d.transfer_note and "3 of them inside this ecoregion" in d.transfer_note
    assert d.transfer_risk == rp.RISK_UNASSESSED          # no registry entry was given
    in_pool = ledger[ledger["in_pool"]]
    assert set(in_pool["level"]) == {"l2"} and len(in_pool) == 33


def test_the_narrowest_adequate_level_wins_over_a_wider_one():
    frame = _frame(_stations("T", 2, l3="55", l2="8.2"),
                   _stations("M", 25, l3="56", l2="8.2", flat=True),
                   _stations("W", 60, l3="67", l2="8.4", flat=True))
    d, _ = _choose("m_form", frame, "55")
    assert d.level == "l2" and d.n_usable == 27


def test_an_exploratory_pool_is_used_only_when_no_level_is_adequate():
    frame = _frame(_stations("T", 12, l3="55", l2="8.2"), _stations("M", 3, l3="56", l2="8.2", flat=True))
    d, _ = _choose("m_form", frame, "55")
    assert d.level == "l3" and d.n_usable == 12 and d.disposition == "exploratory"
    # with an adequate Level II the exploratory Level III loses
    frame2 = _frame(frame, _stations("N", 20, l3="57", l2="8.2", flat=True))
    d2, _ = _choose("m_form", frame2, "55")
    assert d2.level == "l2" and d2.disposition == "adequate"


def test_no_level_supports_the_metric_means_insufficient():
    frame = _frame(_stations("T", 40, l3="55", l2="8.2", strict=False),
                   _stations("M", 4, l3="56", l2="8.2", flat=True))
    d, ledger = _choose("m_form", frame, "55")
    assert d.status == rp.STATUS_INSUFFICIENT and d.level is None and d.station_ids == ()
    assert "Insufficient reference support" in d.transfer_note
    assert not ledger["in_pool"].any()
    assert [t["level"] for t in d.levels_tried] == ["l3", "l2", "l1"]


def test_usable_n_is_judged_per_metric():
    """A station without a value for THIS metric does not support it."""
    frame = _frame(_stations("A", 30, l3="58", l2="5.3", l1="5"))
    values = _values(frame, missing=[f"A{i:03d}" for i in range(15)])
    d, ledger = _choose("m_form", frame, "58", values=values)
    assert d.n_pool == 30 and d.n_usable == 15 and d.disposition == "exploratory"
    # the ledger holds a row per station per level tried; the pool is the first
    at_l3 = ledger[ledger["level"] == "l3"]
    assert (at_l3["reason"] == "no_value").sum() == 15 and at_l3["in_pool"].sum() == 15
    assert not ledger[ledger["level"] != "l3"]["in_pool"].any()


# --------------------------------------------------------------------------- #
# comparability
# --------------------------------------------------------------------------- #
def test_a_borrowed_station_outside_the_size_envelope_is_refused():
    target = _frame(_stations("T", 2, l3="71"), _stations("T", 40, l3="71", strict=False, start=2))
    big = _stations("R", 30, l3="65", da=5000.0, flat=True)            # large rivers, same Level II
    small = _stations("S", 25, l3="66", da=25.0, flat=True)
    d, ledger = _choose("m_form", _frame(target, big, small), "71")
    assert d.level == "l2" and d.n_pool == 57 and d.n_comparable == 27 and d.n_usable == 27
    refused = ledger[(ledger["level"] == "l2") & ledger["station_key"].str.startswith("R")]
    assert (refused["reason"] == "outside_envelope:drainage_area_sqkm").all()


def test_geology_matters_only_where_the_family_says_so():
    target = _frame(_stations("T", 2, l3="71"), _stations("T", 40, l3="71", strict=False, start=2))
    other_rock = _stations("K", 30, l3="65", lith="carbonate", flat=True)
    frame = _frame(target, other_rock)
    form, ledger = _choose("m_form", frame, "71")
    bio, _ = _choose("m_bio", frame, "71")
    assert form.status == rp.STATUS_INSUFFICIENT
    assert (ledger["reason"] == "lithology").sum() >= 30
    assert bio.status == "borrowed_l2" and bio.n_usable == 32


def test_the_regions_own_reference_stations_are_always_admitted():
    odd = _stations("T", 25, l3="71", da=np.r_[np.full(24, 25.0), 9000.0])
    d, _ = _choose("m_form", odd, "71")
    assert d.status == rp.STATUS_LOCAL and d.n_usable == 25


def test_a_metric_without_a_family_may_not_borrow():
    frame = _frame(_stations("T", 3, l3="71"), _stations("D", 30, l3="65", flat=True))
    d, _ = rp.choose_pool("unlisted", _values(frame), frame, "71",
                          profile=rp.family_profile("unlisted", CFG), cfg=CFG,
                          settings=SETTINGS)
    assert d.status == rp.STATUS_INSUFFICIENT and d.n_usable == 3


def test_an_owner_exclusion_leaves_every_pool_and_says_why():
    frame = _frame(_stations("A", 22, l3="58", l2="5.3", l1="5"))
    d, ledger = _choose("m_form", frame, "58", excluded={"A000": "a gravel pit upstream"})
    assert d.n_usable == 21 and "A000" not in d.station_ids
    row = ledger[ledger["station_key"] == "A000"].iloc[0]
    assert row["reason"] == "excluded_by_owner:a gravel pit upstream"


@pytest.mark.parametrize("used,supported,risk", [
    ("l3", "l3", rp.RISK_NONE), ("l3", None, rp.RISK_NONE),
    ("l2", "l2", rp.RISK_LOW), ("l2", "l1", rp.RISK_LOW), ("l2", "national", rp.RISK_LOW),
    ("l2", "l3", rp.RISK_MODERATE), ("l1", "l2", rp.RISK_MODERATE),
    ("l1", "l3", rp.RISK_HIGH), ("l1", None, rp.RISK_UNASSESSED)])
def test_transfer_risk(used, supported, risk):
    assert rp.transfer_risk(used, supported) == risk


def test_the_scale_registry_sets_the_risk_and_the_note():
    frame = _frame(_stations("T", 3, l3="71"), _stations("D", 30, l3="65", flat=True))
    d, _ = _choose("m_form", frame, "71", scale_entry={"supported_level": "l3"})
    assert d.transfer_risk == rp.RISK_MODERATE and "moderate" in d.transfer_note
    d2, _ = _choose("m_form", frame, "71", scale_entry={"supported_level": "l1"})
    assert d2.transfer_risk == rp.RISK_LOW and "low" in d2.transfer_note


# --------------------------------------------------------------------------- #
# the local comparison never becomes reference
# --------------------------------------------------------------------------- #
def test_local_best_available_is_a_comparison_and_never_a_pool():
    target = _stations("T", 44, l3="55", l2="8.2", strict=False, relaxed=False,
                       imp=np.linspace(1, 20, 44), ag=np.linspace(30, 95, 44))
    donors = _stations("D", 30, l3="56", l2="8.2", flat=True)
    frame = _frame(target, donors)
    values = _values(frame)
    out = rp.build_pools(["m_form"], pd.DataFrame({"site_id": values.index, "m_form": values.values}),
                         frame, "55", cfg=CFG)
    d = out["decisions"]["m_form"]
    assert d.status == "borrowed_l2" and d.n_local == 0
    assert not any(s.startswith("T") for s in d.station_ids)
    comp = out["local_comparison"]["m_form"]
    assert comp["label"].endswith("(not reference)") and comp["n"] == 11     # a quarter of 44
    assert "lowest landscape pressure" in comp["definition"]


def test_the_relaxed_screen_defines_the_comparison_when_enough_stations_pass():
    target = _frame(_stations("T", 5, l3="58", l2="5.3", l1="5"),
                    _stations("U", 14, l3="58", l2="5.3", l1="5", strict=False, relaxed=True),
                    _stations("V", 20, l3="58", l2="5.3", l1="5", strict=False, relaxed=False))
    stations, definition = rp.local_comparison_stations(target, CFG)
    assert len(stations) == 19 and "relaxed pressure screen" in definition


# --------------------------------------------------------------------------- #
# the masked frame
# --------------------------------------------------------------------------- #
def test_each_metric_column_holds_values_only_inside_its_own_pool():
    local = _stations("A", 25, l3="58", l2="5.3", l1="5")
    donors = _stations("B", 30, l3="59", l2="5.3", l1="5", flat=True)
    frame = _frame(local, donors)
    full = _values(frame)
    thin = full.copy()
    thin[[f"A{i:03d}" for i in range(20)]] = np.nan          # only 5 local values
    wide = pd.DataFrame({"site_id": full.index, "m_form": full.values, "m_bio": thin.values})
    out = rp.build_pools(["m_form", "m_bio"], wide, frame, "58", cfg=CFG)
    assert out["decisions"]["m_form"].level == "l3"
    assert out["decisions"]["m_bio"].level == "l2"
    data = out["data"].set_index("site_id")
    assert data["m_form"].notna().sum() == 25
    assert data.loc[[s for s in data.index if s.startswith("B")], "m_form"].isna().all()
    assert data["m_bio"].notna().sum() == out["decisions"]["m_bio"].n_usable == 35
    assert out["target_n_frame"] == 25 and out["target_n_strict"] == 25


def test_an_insufficient_metric_gets_no_column():
    frame = _frame(_stations("T", 40, l3="55", l2="8.2", strict=False))
    values = _values(frame)
    out = rp.build_pools(["m_form"], pd.DataFrame({"site_id": values.index,
                                                   "m_form": values.values}),
                         frame, "55", cfg=CFG)
    assert out["decisions"]["m_form"].status == rp.STATUS_INSUFFICIENT
    assert "m_form" not in out["data"].columns


def test_the_support_record_carries_what_deep_must_show():
    frame = _frame(_stations("T", 3, l3="71"), _stations("D", 30, l3="65", flat=True))
    d, _ = _choose("m_form", frame, "71", scale_entry={"supported_level": "l2"})
    rec = rp.reference_support_record(d)
    for key in ("status", "level", "regionCode", "regionName", "screen", "nPool",
                "nComparable", "nUsable", "nLocal", "covariates", "selfCoverage",
                "transferRisk", "transferNote"):
        assert key in rec, key
    assert rec["screen"].startswith("least-disturbed-v1 (strict)")
    assert "lith_group" in rec["covariates"]
    assert "—" not in rec["transferNote"]
