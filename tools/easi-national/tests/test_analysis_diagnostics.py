"""The stats step over the runs of a synthetic values table: distributions
and flags, variance shares, the level and paradigm tables, border excess."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from builder import state
from builder.analysis import diagnostics, schemes
from builder.paths import DataRoot
from test_analysis_schemes import _values_table


def test_distribution_flags_and_eta_squared():
    constant = diagnostics.distribution(np.full(500, 0.95), 0.05, 1.0)
    assert constant["c1_constant"] and constant["share_cap"] == 0.0 and constant["p50"] == 0.95
    capped = diagnostics.distribution(np.concatenate([np.full(300, 2.0), np.linspace(0.5, 1.9, 200)]), 0.1, 2.0)
    assert capped["c2_censored"] and capped["share_cap"] == pytest.approx(0.6)
    zeros = diagnostics.distribution(np.concatenate([np.zeros(700), np.linspace(0.1, 5, 300)]), 1.0, None)
    assert zeros["c3_zero_inflated"] and zeros["share_zero"] == pytest.approx(0.7)
    spread = diagnostics.distribution(np.linspace(0, 100, 1000), 5.0, 100.0)
    assert not (spread["c1_constant"] or spread["c2_censored"] or spread["c3_zero_inflated"])
    values = np.concatenate([np.random.default_rng(1).normal(0, 1, 500), np.random.default_rng(2).normal(5, 1, 500)])
    groups = np.array(["a"] * 500 + ["b"] * 500, dtype=object)
    assert diagnostics.eta_squared(values, groups) > 0.7
    assert diagnostics.eta_squared(values, np.array(["a"] * 1000, dtype=object)) is None
    assert diagnostics.eta_squared(values[:50], groups[:50]) is None


def test_stats_step_writes_the_tables(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    table = _values_table(400)
    table["tocomid"] = np.roll(table["comid"].to_numpy(), 1).astype(float)          # a chain of adjacent reaches
    table.to_parquet(schemes.values_path(root), index=False)
    (root.analysis / "curves").mkdir()
    registry = pd.DataFrame([
        {"quantity": "woody_wsrp100", "level": "national", "stratum": "national:national", "split": "", "usable": True,
         "q50": 50.0, "iqr": 30.0, "panel_tier": "complete",
         "points_json": json.dumps([[0.0, 0.0], [20.0, 0.3], [45.0, 0.7], [80.0, 1.0], [90.0, 1.0]])},
        {"quantity": "woody_wsrp100", "level": "l3", "stratum": "l3:45", "split": "", "usable": True,
         "q50": 70.0, "iqr": 20.0, "panel_tier": "complete",
         "points_json": json.dumps([[0.0, 0.0], [30.0, 0.3], [60.0, 0.7], [85.0, 1.0], [95.0, 1.0]])},
        {"quantity": "woody_wsrp100", "level": "l2", "stratum": "l2:8.3", "split": "", "usable": True,
         "q50": 55.0, "iqr": 25.0, "panel_tier": "best_available",
         "points_json": json.dumps([[0.0, 0.0], [22.0, 0.3], [50.0, 0.7], [82.0, 1.0], [92.0, 1.0]])},
    ])
    registry.to_parquet(schemes.registry_path(root), index=False)
    (root.analysis / "l3_to_l2_l1.csv").write_text("l3,l2,l1\n45,8.3,8\n64,8.3,8\n", encoding="utf-8")
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    schemes.run(root, progress, control, {})
    diagnostics.run(root, progress, control, {})
    out = root.analysis / "stats"
    diag = pd.read_csv(out / "diag_metric_stratum.csv")
    assert set(diag["level"]) >= {"state", "l3", "national"} and "c3_zero_inflated" in diag.columns
    dor = diag[(diag["quantity"] == "dor") & (diag["level"] == "national")].iloc[0]
    assert dor["share_zero"] > 0.5
    var = pd.read_csv(out / "variance_decomp.csv")
    assert {"eta2_state", "eta2_l3"} <= set(var.columns) and len(var) >= 5
    shifts = pd.read_csv(out / "median_shift.csv")
    # the Level III panel (l3:45) against each candidate level's panel: national (50, IQR 30) and Level II (55, IQR 25);
    # no NARS-9 or Level I panel exists and l3:64 has no Level III panel, so those pairs have no row
    assert len(shifts) == 2 and set(shifts["level"]) == {"national", "l2"} and (shifts["stratum"] == "l3:45").all()
    assert shifts[shifts["level"] == "l2"].iloc[0]["h"] == pytest.approx(15 / 25)
    assert shifts[shifts["level"] == "national"].iloc[0]["h"] == pytest.approx(20 / 30)
    assert shifts["n_total"].sum() == 2 * int((table["l3_code"] == "45").sum())
    t_l1 = pd.read_csv(out / "level_T_L1.csv")
    assert set(t_l1["quantity"]) >= {"woody_wsrp100"} and set(t_l1["level"]) == {"national", "nars9", "l2", "l3"}
    row = t_l1[(t_l1["quantity"] == "woody_wsrp100") & (t_l1["level"] == "l3")].iloc[0]
    assert row["criteria_sets"] == 1 and row["run"] == "S3" and row["share_reaches_material"] == 0.0
    national = t_l1[(t_l1["quantity"] == "woody_wsrp100") & (t_l1["level"] == "national")].iloc[0]
    assert national["h_mean_weighted"] == pytest.approx(20 / 30) and national["share_reaches_material"] == 1.0
    t_l2 = pd.read_csv(out / "level_T_L2.csv")
    assert set(t_l2["group"]) == {"VA", "KS"} and (t_l2["view"] == "banded").all()
    t_p2 = pd.read_csv(out / "paradigm_T_P2.csv")
    assert set(t_p2["view"]) == {"continuous", "mix"} and "banded_1_to_2" in t_p2.columns
    border = pd.read_csv(out / "border_excess.csv")
    assert set(border["run"]) == set(schemes.RUNS) and border["pairs_cross"].max() > 0
    # a rerun that reuses the expensive parts reads their files instead of recomputing them
    (out / "variance_decomp.csv").unlink()
    (out / "level_T_L1.csv").unlink()
    diagnostics.run(root, progress, control, {"reuse": ["distributions", "validation", "stability"]})
    assert not (out / "variance_decomp.csv").exists() and (out / "level_T_L1.csv").exists()
