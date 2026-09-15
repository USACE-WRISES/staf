"""Panel stability bootstraps and the NRSA agreement statistics."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.analysis import curves, schemes, stability, validation
from builder.paths import DataRoot

RNG = np.random.default_rng(5)


def test_flip_rate_is_small_for_a_stable_panel_and_large_for_a_tiny_one():
    q = curves.QUANTITIES["woody_wsrp100"]
    population = RNG.uniform(0, 100, size=5000)
    members = RNG.uniform(30, 90, size=400)
    clusters = np.array([f"h{i % 80}" for i in range(400)], dtype=object)
    fit = curves.fit_curve(members, q, "l3:1")
    result = stability.flip_rate(members, clusters, population, True, q.domain, fit["x39"], fit["x69"], n_boot=50)
    assert result["n_boot_valid"] == 50 and result["flip_mean"] < 0.10 and result["x39_lo"] <= fit["x39"] <= result["x39_hi"]
    assert stability.verdict(result["flip_mean"]) == "accept"
    tiny = members[:12]
    tiny_clusters = np.array([f"h{i}" for i in range(12)], dtype=object)
    fit_tiny = curves.fit_curve(tiny, q, "l3:2")
    wobbly = stability.flip_rate(tiny, tiny_clusters, population, True, q.domain, fit_tiny["x39"], fit_tiny["x69"], n_boot=50)
    assert wobbly["flip_mean"] > result["flip_mean"]
    assert stability.verdict(None) == "not evaluable" and stability.verdict(0.15) == "exploratory" and stability.verdict(0.3) == "reject"
    classes = stability.classes_from_crossings(np.array([1.0, 5.0, 9.0, np.nan]), 4.0, 8.0, True)
    assert classes.tolist() == [0, 1, 2, -1]
    falling = stability.classes_from_crossings(np.array([1.0, 5.0, 9.0]), 8.0, 4.0, False)
    assert falling.tolist() == [2, 1, 0]


def test_stability_step_reads_the_registry_members_and_tables(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    (root.analysis / "panels").mkdir(parents=True)
    (root.analysis / "curves").mkdir(parents=True)
    n = 2000
    landscape = pd.DataFrame({"comid": np.arange(1, n + 1, dtype="int64"), "huc12": [f"h{i % 100}" for i in range(n)],
                              "l3": ["1"] * n, "l2": ["8.1"] * n, "l1": ["8"] * n, "nars9": ["SAP"] * n,
                              "slope_class": ["lt_0.5"] * n, "fcode_class": ["perennial"] * n,
                              "woody_wsrp100": RNG.uniform(0, 100, size=n).astype("float32")})
    landscape.to_parquet(root.analysis / "landscape.parquet", index=False)
    members = landscape.iloc[:400][["comid", "huc12", "slope_class", "fcode_class"]].assign(level="l3", stratum="l3:1")
    members.to_parquet(root.analysis / "panels" / "panel_members.parquet", index=False)
    q = curves.QUANTITIES["woody_wsrp100"]
    fit = curves.fit_curve(landscape["woody_wsrp100"].to_numpy()[:400], q, "l3:1")
    registry = pd.DataFrame([{"quantity": "woody_wsrp100", "level": "l3", "stratum": "l3:1", "split": "", "usable": True,
                              "x39": fit["x39"], "x69": fit["x69"], "points_json": json.dumps(fit["points"])},
                             # a split curve below the national level: its population mask is a pandas read-only array
                             {"quantity": "woody_wsrp100", "level": "l3", "stratum": "l3:1", "split": "lt_0.5", "usable": True,
                              "x39": fit["x39"], "x69": fit["x69"], "points_json": json.dumps(fit["points"])},
                             {"quantity": "bhr_median", "level": "national", "stratum": "national:national", "split": "lt_0.5",
                              "usable": False, "x39": None, "x69": None, "points_json": "[]"}])
    registry.to_parquet(curves.registry_path(root), index=False)
    path = stability.run_stability(root, state.Progress(root, quiet=True), state.Control(root), n_boot=30)
    rows = pd.read_csv(path)
    assert len(rows) == 2 and (rows["n_members"] == 400).all() and (rows["n_population"] == n).all()
    assert rows.iloc[0]["verdict"] in ("accept", "exploratory") and rows.iloc[0]["n_clusters"] == 100


def test_agreement_statistics_and_the_verdict():
    n = 200
    truth = RNG.uniform(0, 1, size=n)
    index = np.clip(truth + RNG.normal(0, 0.15, size=n), 0, 1)
    target = np.where(truth < 0.3, "Poor", np.where(truth < 0.7, "Fair", "Good")).astype(object)
    desktop = schemes.class_of_index(index)
    out = validation.agreement(index, desktop, target, [(truth, 1), (-truth, -1)], boot=100)
    assert out["auc_poor"] > 0.8 and out["auc_good"] > 0.8 and out["kappa"] > 0.3 and out["rho"] > 0.7
    assert out["auc_poor_lo"] <= out["auc_poor"] <= out["auc_poor_hi"] and out["rho_lo"] > 0
    assert validation.verdict({**out, "n": n}) == "validated"
    assert out["rho_lo"] <= out["rho"] <= out["rho_hi"]                  # the interval is of the same statistic
    noise = RNG.uniform(0, 1, size=n)
    bad = validation.agreement(noise, schemes.class_of_index(noise), target, [(truth, 1)], boot=100)
    assert validation.verdict({**bad, "n": n}) == "not validated"
    assert validation.verdict({"n": 10, "auc_poor": None, "rho": None}) == "below floor"
    none = validation.agreement(index, desktop, None, [])
    assert none["rho"] is None and "auc_poor" not in none


def test_validation_scores_the_stations_under_every_run(tmp_path):
    from test_analysis_schemes import _values_table
    root = DataRoot(tmp_path / "data").ensure()
    (root.analysis / "nrsa").mkdir(parents=True)
    (root.analysis / "curves").mkdir(parents=True)
    stations = _values_table(120)
    stations = stations.rename(columns={"state": "station_state"})
    stations["station_key"] = [f"S{i}" for i in range(len(stations))]
    stations["station_nars9"] = RNG.choice(["SAP", "CPL"], size=len(stations))
    rows = []
    for cycle, rank in (("1819", 1), ("1314", 2)):
        block = stations.copy()
        block["cycle"] = cycle
        block["cycle_rank"] = rank
        block["visit_no"] = "1"
        block["t__instrmcvr"] = RNG.choice(["Good", "Fair", "Poor"], size=len(block))
        block["t__bent_mmi"] = RNG.choice(["Good", "Fair", "Poor"], size=len(block))
        block["rt_nrsa"] = RNG.choice(["R", "In", "Im"], size=len(block))
        block["a__phab_XFC_NAT"] = block["v__habitat_provision__woodyRiparian"] / 100 + RNG.normal(0, 0.1, size=len(block))
        block["a__phab_XINC_H"] = RNG.uniform(0.5, 2.0, size=len(block))
        block["a__phab_XBKF_H"] = RNG.uniform(0.5, 1.5, size=len(block))
        rows.append(block)
    frame = pd.concat(rows, ignore_index=True)
    frame.to_parquet(validation.frame_path(root), index=False)
    registry = pd.DataFrame([{"quantity": "woody_wsrp100", "level": "national", "stratum": "national:national", "split": "",
                              "usable": True, "points_json": json.dumps([[0.0, 0.0], [20.0, 0.3], [45.0, 0.7], [80.0, 1.0], [90.0, 1.0]])}])
    registry.to_parquet(curves.registry_path(root), index=False)
    path = validation.run_validation(root, state.Progress(root, quiet=True), boot=20)
    out = pd.read_csv(path)
    assert set(out["run"]) == set(schemes.RUNS) and set(out["region"]) == {"US", "SAP", "CPL"}
    habitat = out[(out["subject"] == "habitat_provision") & (out["run"] == "SN") & (out["region"] == "US")].iloc[0]
    assert habitat["target"] == "instrmcvr" and habitat["n"] > 100 and habitat["rho"] > 0.5
    assert "cand__light_thermal_regime__woody_only" in set(out["subject"])
    inc = out[(out["subject"] == "high_flow_dynamics") & (out["region"] == "US")]
    assert (inc["continuous_targets"] == "inc_ratio").all()
    eci = pd.read_csv(validation.eci_auc_path(root))
    assert set(eci["view"]) == set(schemes.VIEWS) and eci["n_rt"].max() > 50


def test_regional_consistency_counts_the_regions_that_hold():
    us = {"rho": 0.3, "auc_poor": 0.7}
    regional = [{"n": 40, "auc_poor": 0.6, "rho": 0.2}, {"n": 40, "auc_poor": 0.5, "rho": 0.2},
                {"n": 40, "auc_poor": None, "rho": -0.2}, {"n": 40, "auc_poor": None, "rho": 0.05},
                {"n": 40, "auc_poor": None, "rho": None}]
    assert validation.regional_consistency(us, regional) == (5, 2)
    assert validation.regional_consistency({"rho": None}, [{"n": 40, "auc_poor": 0.56, "rho": -0.4}]) == (1, 1)
