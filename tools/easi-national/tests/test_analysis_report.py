"""The report step over a synthetic pipeline: routes, the decision sheet,
the index page with tables and figures, one scorecard per function."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from builder import state
from builder.analysis import diagnostics, report, schemes
from builder.paths import DataRoot
from test_analysis_schemes import _values_table


def test_report_writes_index_routes_scorecards_and_the_sheet(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    table = _values_table(400)
    table["lat"] = np.random.default_rng(2).uniform(36, 39, size=len(table))
    table["lon"] = np.random.default_rng(3).uniform(-80, -76, size=len(table))
    table.to_parquet(schemes.values_path(root), index=False)
    (root.analysis / "curves").mkdir()
    registry = pd.DataFrame([
        {"quantity": "woody_wsrp100", "level": "national", "stratum": "national:national", "split": "", "usable": True,
         "q50": 50.0, "iqr": 30.0, "panel_tier": "complete",
         "points_json": json.dumps([[0.0, 0.0], [20.0, 0.3], [45.0, 0.7], [80.0, 1.0], [90.0, 1.0]])}])
    registry.to_parquet(schemes.registry_path(root), index=False)
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    schemes.run(root, progress, control, {})
    diagnostics.run(root, progress, control, {})
    index = report.run(root, progress, control, {})
    text = index.read_text(encoding="utf-8")
    assert "Paradigm views" in text and "T-L1" in text and "Routes per function" in text and "data:image/png;base64" in text
    routes = pd.read_csv(report.routes_path(root))
    assert len(routes) == 20 and set(routes["route"]).issubset({
        "keep (guidance bands)", "substitute", "substitute (unvalidated candidate)", "re-criteria (regional curve)",
        "re-criteria (regional curve, unvalidated)", "keep"})
    assert (routes.loc[routes["function"] == "catchment_hydrology", "route"] == "keep (guidance bands)").all()
    sheet = (report.report_dir(root) / "decision_sheet.md").read_text(encoding="utf-8")
    assert "Recommended level" in sheet and "Recommended paradigm" in sheet and "habitat_provision" in sheet
    for output in (text, sheet):
        assert "S0 represents the loaded values method, not the saved historical baseline" in output
        assert "incomplete guidance composites retain class anchors" in output
        assert "S0 function AUC and rank correlations use those diagnostic indices; class agreement uses the retained classes" in output
        assert "SN, S9, S2 and S3 are diagnostic refits" in output
        assert "incumbent_* refers to the displayed diagnostic run, while s0_auc_poor refers to S0" in output
        assert "do not change the accepted criteria" in output
    cards = list((report.report_dir(root) / "scorecards").glob("*.html"))
    assert len(cards) == 20
    maps = list((report.report_dir(root) / "maps").glob("*.png"))
    assert len(maps) == len(schemes.RUNS) * 2
    level, notes = report.recommend_level(diagnostics._read_csv(diagnostics.stats_dir(root) / "level_T_L1.csv"))
    assert level in report.LEVEL_LABEL and notes


def test_recommend_level_applies_every_c15_clause():
    t_l1 = [{"quantity": "woody_wsrp100", "level": level, "share_reaches_material": material, "nrsa_rho_pooled": rho,
             "pinned_cells": pinned}
            for level, material, rho, pinned in (("national", 0.2, 0.30, 12), ("nars9", 0.1, 0.30, 4),
                                                 ("l2", 0.0, 0.29, 4), ("l3", 0.0, 0.30, 2))]
    t_p3 = [{"run": run, "view": "banded", "region": "US", "auc_rt_r_vs_im": auc}
            for run, auc in (("SN", 0.831), ("S9", 0.831), ("S2", 0.862), ("S3", 0.862))]
    level, notes = report.recommend_level(t_l1, t_p3)
    assert level == "l2" and len(notes) == 3          # national and NARS-9 lose 0.031 ECI AUC against Level III
    assert report.recommend_level(t_l1)[0] == "nars9"  # without the ECI table: national pins 10 cells more than Level III
    t_l1[2]["pinned_cells"] = 9                        # Level II would pin 7 cells more than Level III
    assert report.recommend_level(t_l1, t_p3)[0] == "l3"
    t_l1[0]["share_reaches_material"] = 0.6            # one of one curved quantity material at national: over a third
    assert report.recommend_level(t_l1[:1] + t_l1[3:], None)[0] == "l3"
