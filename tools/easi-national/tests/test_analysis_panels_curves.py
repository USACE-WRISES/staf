"""The reference panels (fixed screen, relaxed tier, HUC12 thinning, floors)
and the curve fitting (engine status, the 0.69 / 0.39 crossings, the closed
form seed, np.interp parity, usability rules, the fallback chain)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.analysis import curves, panels
from builder.paths import DataRoot

RNG = np.random.default_rng(3)


def _stratum(code, n, *, huc12s, impervious, l2="8.1", canal_share=0.05, start=0):
    comids = np.arange(start, start + n)
    return pd.DataFrame({
        "comid": comids,
        "huc12": [f"{code}{i % huc12s:06d}" for i in range(n)],
        "state": ["VA"] * n, "l3": [code] * n, "l2": [l2] * n, "l1": [l2.split(".")[0]] * n, "nars9": ["SAP"] * n,
        "slope_class": RNG.choice(["lt_0.5", "0.5_to_2", "ge_2"], size=n),
        "fcode_class": np.where(RNG.random(n) < canal_share, "canal", "perennial"),
        "da_class": ["le_10"] * n, "wadeable": RNG.random(n) > 0.05,
        "totdasqkm": RNG.uniform(1, 50, size=n), "streamorde": RNG.integers(1, 5, size=n),
        "in_scored_set": RNG.random(n) < 0.5,
        "pctimp2019ws": RNG.uniform(*impervious, size=n), "agriculture_ws": RNG.uniform(0, 5, size=n),
        "rddensws": np.full(n, 0.5), "dor": np.zeros(n), "sc__nabd_densws": np.zeros(n),
        "sc__npdesdensws": np.zeros(n), "mines_ws": np.zeros(n), "corridor_conversion_wsrp100": RNG.uniform(0, 5, size=n),
        "wetland_ws": RNG.uniform(0, 20, size=n), "woody_wsrp100": RNG.uniform(20, 90, size=n),
        "natural_wsrp100": RNG.uniform(30, 100, size=n),
    })


def _landscape():
    return pd.concat([
        _stratum("1", 3000, huc12s=300, impervious=(0.0, 0.5)),                    # every reach passes the strict screen
        _stratum("2", 3000, huc12s=300, impervious=(5.0, 8.0), start=3000),         # fails both screens
        _stratum("3", 500, huc12s=50, impervious=(0.0, 0.5), start=6000),           # too small to be a stratum
        _stratum("4", 1200, huc12s=40, impervious=(1.5, 2.5), l2="8.2", start=6500),  # relaxed tier only
    ], ignore_index=True)


def test_panels_apply_the_screen_the_floors_and_the_relaxed_tier():
    frame = _landscape()
    out, members = panels.select_panels(frame, "l3")
    by = {r["code"]: r for r in out.to_dict("records")}
    assert by["1"]["panel_tier"] == "complete" and by["1"]["screen"] == "strict" and 100 <= by["1"]["n_ref"] <= 300
    assert by["1"]["n_pass_strict"] > 2500 and by["1"]["n_strict_thinned"] == by["1"]["n_ref"]
    assert by["2"]["panel_tier"] == "none" and by["2"]["n_pass_strict"] == 0 and "either screen" in by["2"]["reason"]
    assert by["3"]["panel_tier"] == "none" and "under 1000" in by["3"]["reason"]
    assert by["4"]["panel_tier"] == "best_available" and by["4"]["screen"] == "relaxed" and 30 <= by["4"]["n_ref"] <= 40
    assert by["1"]["skipped_rules"] == ""
    assert set(members["stratum"]) == {"l3:1", "l3:4"}
    ones = members[members["stratum"] == "l3:1"]
    assert ones["huc12"].is_unique and set(ones["fcode_class"]) == {"perennial"}
    assert members["panel_tier"].isin(["complete", "best_available"]).all()
    # the parent level pools the strata: the L2 8.1 panel is stratum 1's reaches plus the 50 HUC12s of the
    # small stratum 3 (2 contributes none)
    out2, members2 = panels.select_panels(frame, "l2")
    two = {r["code"]: r for r in out2.to_dict("records")}
    assert two["8.1"]["panel_tier"] == "complete" and two["8.1"]["n_ref"] == by["1"]["n_ref"] + 50
    assert two["8.2"]["panel_tier"] == "best_available"
    nat, _ = panels.select_panels(frame, "national")
    assert nat.iloc[0]["stratum"] == "national:national" and nat.iloc[0]["panel_tier"] == "complete"
    with pytest.raises(KeyError):
        panels.stratum_of(frame.drop(columns=["l3"]), "l3")


def test_fit_curve_seed_parity_interp_and_usability():
    rising = curves.QUANTITIES["woody_wsrp100"]
    values = RNG.uniform(20, 90, size=400)
    fit = curves.fit_curve(values, rising, "l3:1")
    assert fit["status"] == "complete" and fit["n"] == 400 and 0 < fit["x39"] < fit["x69"] < fit["q75"] + 1e-9
    np.testing.assert_allclose(np.array(fit["points"]), np.array(curves.seed_points(fit["q25"], fit["q75"], True, rising.domain)))
    falling = curves.QUANTITIES["q_cv_monthly"]
    fit_f = curves.fit_curve(RNG.uniform(0.2, 1.5, size=300), falling, "national")
    assert fit_f["status"] == "complete" and fit_f["x69"] < fit_f["x39"]
    np.testing.assert_allclose(np.array(fit_f["points"]), np.array(curves.seed_points(fit_f["q25"], fit_f["q75"], False, falling.domain)))
    from streamcurves import curves as engine
    xs = RNG.uniform(-10, 120, size=200)
    mine = curves.interp(fit["points"], xs)
    theirs = np.array([engine.interp_curve([{"x": x, "y": y} for x, y in fit["points"]], float(v)) for v in xs])
    assert np.max(np.abs(mine - theirs)) < 1e-12
    zero = curves.QUANTITIES["wetland_ws"]
    fit_z = curves.fit_curve(np.concatenate([np.zeros(150), RNG.uniform(0, 5, size=50)]), zero, "l3:9")
    assert fit_z["status"] in ("degenerate_q25", "degenerate_curve")
    assert curves.usable(zero, {**fit_z, "rho_pressure": 0.0}, "complete")[0] is False
    ok, note = curves.usable(rising, {**fit, "rho_pressure": 0.1}, "complete")
    assert ok and note == ""
    assert curves.usable(rising, {**fit, "rho_pressure": 0.5}, "best_available")[0] is False
    assert curves.usable(rising, {**fit, "rho_pressure": 0.5}, "complete") == (True, "pressure-driven inside the panel (rho 0.50), strict tier only")
    assert curves.usable(rising, {**fit, "rho_pressure": 0.0}, "none")[0] is False
    bhr = curves.QUANTITIES["bhr_median"]
    capped = curves.fit_curve(np.clip(RNG.normal(1.9, 0.2, size=300), 0.3, 2.0), bhr, "national")
    assert capped["status"] == "complete" and curves.usable(bhr, {**capped, "rho_pressure": 0.0}, "complete")[0] is False
    fine = curves.fit_curve(np.clip(RNG.normal(1.0, 0.2, size=300), 0.3, 2.0), bhr, "national")
    assert curves.usable(bhr, {**fine, "rho_pressure": 0.0}, "complete")[0] is True


def test_resolve_walks_the_parent_chain_and_honours_splits():
    registry = [
        {"quantity": "woody_wsrp100", "level": "l3", "stratum": "l3:1", "split": "", "usable": True},
        {"quantity": "woody_wsrp100", "level": "l3", "stratum": "l3:2", "split": "", "usable": False},
        {"quantity": "woody_wsrp100", "level": "l2", "stratum": "l2:8.1", "split": "", "usable": True},
        {"quantity": "woody_wsrp100", "level": "national", "stratum": "national:national", "split": "", "usable": True},
        {"quantity": "bfiws", "level": "l3", "stratum": "l3:1", "split": "perennial", "usable": True},
        {"quantity": "bfiws", "level": "national", "stratum": "national:national", "split": "intermittent", "usable": True},
    ]
    frame = pd.DataFrame({"l3": ["1", "2", "3", None], "l2": ["8.1", "8.1", "9.4", "8.1"], "l1": ["8", "8", "9", "8"],
                          "fcode_class": ["perennial", "intermittent", "intermittent", "perennial"]})
    used, depth = curves.resolve_vector(registry, "woody_wsrp100", "l3", frame)
    assert used.tolist() == ["l3:1", "l2:8.1", "national:national", "l2:8.1"] and depth.tolist() == [0, 1, 3, 1]
    used, depth = curves.resolve_vector(registry, "woody_wsrp100", "national", frame)
    assert set(used.tolist()) == {"national:national"} and set(depth.tolist()) == {0}
    used, depth = curves.resolve_vector(registry, "bfiws", "l3", frame, split="fcode_class")
    assert used.tolist() == ["l3:1|perennial", "national:national|intermittent", "national:national|intermittent", None]
    assert depth.tolist() == [0, 3, 3, -1]


def test_panels_and_curves_steps_run_over_the_tables(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    frame = _landscape()
    frame["comid"] = frame["comid"].astype("int64")
    frame.to_parquet(panels.landscape_path(root), index=False)
    scored = frame[frame["in_scored_set"]]
    values = pd.DataFrame({"comid": scored["comid"].to_numpy(), "er_median": RNG.uniform(1.0, 4.0, size=len(scored)),
                           "bhr_median": np.clip(RNG.normal(1.1, 0.3, size=len(scored)), 0.3, 2.0),
                           "v__nutrient_cycling__tn": RNG.uniform(0.1, 2.0, size=len(scored))})
    values.to_parquet(curves.values_path(root), index=False)
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    options = {"levels": ["l3", "l2", "national"]}
    panels.run(root, progress, control, options)
    summary = json.loads(panels.summary_path(root).read_text())
    assert summary["l3"]["tiers"]["complete"] == 1 and summary["l3"]["tiers"]["best_available"] == 1
    assert summary["national"]["tiers"]["complete"] == 1
    curves.run(root, progress, control, options)
    registry = pq.read_table(curves.registry_path(root)).to_pandas()
    woody = registry[(registry["quantity"] == "woody_wsrp100") & (registry["level"] == "l3")]
    assert set(woody["stratum"]) == {"l3:1", "l3:4"} and bool(woody[woody["stratum"] == "l3:1"]["usable"].iloc[0])
    geometry = registry[(registry["quantity"] == "er_median")]
    assert set(geometry["level"]) == {"national"} and set(geometry["split"]) <= {"lt_0.5", "0.5_to_2", "ge_2"}
    assert (registry[registry["quantity"] == "hyd_min"].shape[0] == 0)         # column absent: skipped, not fitted
    national = json.loads(curves.level_json_path(root, "national").read_text())
    assert "woody_wsrp100" in national and "national:national" in national["woody_wsrp100"]
    entry = national["woody_wsrp100"]["national:national"]
    assert entry["x39"] < entry["x69"] and len(entry["points"]) == 5
    points = pq.read_table(curves.points_path(root)).to_pandas()
    assert (points["quantity"] == "woody_wsrp100").sum() >= 5
