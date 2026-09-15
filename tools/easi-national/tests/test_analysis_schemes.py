"""The runs step: the vectorised rollup against the app's own, the guidance
line and class edges, the catalog-derived rules, and an end-to-end run over
a synthetic values table with one usable national curve."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import state
from builder.analysis import schemes
from builder.paths import DataRoot

RNG = np.random.default_rng(11)


def test_rollup_frame_matches_the_app_rollup_row_by_row():
    from easi import scoring
    rules = schemes.function_rules()
    keys = list(rules)
    n = 200
    scores = {fk: RNG.integers(0, 16, size=n).astype(float) for fk in keys}
    for fk in keys[:6]:                                           # some unrated functions
        scores[fk][RNG.random(n) < 0.3] = np.nan
    roll = schemes.rollup_frame(scores)
    for i in range(n):
        per_function = {fk.replace("_", "-"): int(scores[fk][i]) for fk in keys if np.isfinite(scores[fk][i])}
        expected = scoring.rollup(per_function)
        assert roll["eci"][i] == pytest.approx(expected.ecosystem_condition_index, abs=1e-12)
        for outcome in schemes.OUTCOMES:
            value = expected.sub_indices[outcome]
            if value is None:
                assert np.isnan(roll[outcome][i])
            else:
                assert roll[outcome][i] == pytest.approx(value, abs=1e-12)


def test_line_class_and_score_helpers():
    lower = schemes.line_index([0.0, 10.0, 17.5, 25.0, 40.0, 60.0, np.nan], 10.0, 25.0, False)
    assert lower[:6] == pytest.approx([1.0, 0.69, 0.54, 0.39, 0.0, 0.0]) and np.isnan(lower[6])
    higher = schemes.line_index([0.0, 0.003, 0.006, 0.009, 0.02], 0.006, 0.003, True)
    assert higher == pytest.approx([0.0, 0.39, 0.69, 1.0, 1.0])
    bounded = schemes.line_index([0.0, 0.25, 0.5, 1.0], 0.5, 0.25, True, (0.0, 1.0))
    assert bounded == pytest.approx([0.0, 0.39, 0.69, 1.0])
    assert schemes.class_of_index([0.39, 0.3900001, 0.69, 0.7, np.nan]).tolist() == ["Fair", "Fair", "Good", "Good", None]
    scores = schemes.function_scores([0.85, 0.545, 0.195, np.nan, 0.7])
    assert scores[:3].tolist() == [13.0, 8.0, 3.0] and np.isnan(scores[3]) and scores[4] == 10.0
    indices = schemes.midpoint_index(["Good", "Fair", "Poor", None])
    assert indices[:3].tolist() == [0.85, 0.545, 0.195] and np.isnan(indices[3])


def test_rating_mapping_pins_python_numpy_and_app_scores():
    from easi import config, scoring

    assert schemes.MIDPOINT is config.RATING_INDEX
    indices = schemes.midpoint_index(["Good", "Fair", "Poor"])
    assert (indices * 15).tolist() == pytest.approx([12.75, 8.175, 2.925])
    assert [round(float(index) * 15) for index in indices] == [13, 8, 3]
    assert np.rint(indices * 15).tolist() == [13.0, 8.0, 3.0]
    assert schemes.function_scores(indices).tolist() == [13.0, 8.0, 3.0]
    assert [scoring.function_score(float(index)) for index in indices] == [13, 8, 3]


def test_candidate_count_ladders_use_the_easi_rating_mapping():
    frame = pd.DataFrame({
        "sc__rdcrsws": [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, np.nan],
        "sc__nabd_densws": [0.0, 1.0, 3.0, np.nan, np.nan, np.nan, np.nan],
        "sc__npdesdenscat": [0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0],
        "sc__canaldenscat": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
        "sc__rdcrscat": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, np.nan],
    })
    run = schemes.Run("S0", None, pa.Table.from_pandas(frame), frame, {}, [])
    candidates = schemes.candidate_indices(run)
    for key in ("cand__watershed_connectivity__crossings_nabd", "cand__reach_inflow__catchment_ladder"):
        indices = candidates[key]
        assert indices[:3].tolist() == [0.85, 0.545, 0.195]
        assert schemes.function_scores(indices[:3]).tolist() == [13.0, 8.0, 3.0]
        assert np.isnan(indices[-1])


def test_runs_digest_includes_the_rating_mapping(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data")
    current = schemes.inputs(root)
    monkeypatch.setitem(schemes.MIDPOINT, "Good", 0.90)
    assert schemes.inputs(root) != current


def test_function_rules_read_the_catalog_edges():
    rules = schemes.function_rules()
    assert len(rules) == 20
    imp = next(i for i in rules["catchment_hydrology"]["inputs"] if i["key"] == "impervious")
    assert rules["catchment_hydrology"]["operator"] == "worst_index" and imp["edges"] == (10.0, 25.0) and imp["higher"] is False
    slope = next(i for i in rules["hyporheic_connectivity"]["inputs"] if i["key"] == "slope")
    assert rules["hyporheic_connectivity"]["operator"] == "best_index" and slope["edges"] == (0.006, 0.003) and slope["higher"] is True
    tn = next(i for i in rules["nutrient_cycling"]["inputs"] if i["key"] == "tn")
    assert tn["regional"]["SAP"] == (0.24, 0.456)
    assert rules["surface_water_storage"]["method_edges"] == (5.0, 1.0) and rules["surface_water_storage"]["method_higher"] is True
    assert rules["reach_inflow"]["method_edges"] == (1.0, 3.0) and rules["reach_inflow"]["method_higher"] is False
    assert rules["community_dynamics"]["integer"] and rules["water_soil_quality"]["operator"] == "categorical_lookup"


def _values_table(n=300):
    rules = schemes.function_rules()
    frame = {"comid": np.arange(1, n + 1, dtype="int64"), "state": RNG.choice(["VA", "KS"], size=n),
             "huc8": ["02080204"] * n, "huc12": [f"0208020401{i % 20:02d}" for i in range(n)],
             "l3_code": RNG.choice(["45", "64"], size=n), "l2": ["8.3"] * n, "l1": ["8"] * n, "nars9": ["SAP"] * n,
             "slope_class": RNG.choice(["lt_0.5", "0.5_to_2", "ge_2"], size=n),
             "fcode_class": RNG.choice(["perennial", "intermittent", "canal"], size=n, p=[0.8, 0.15, 0.05]),
             "nid_dam_count": RNG.integers(0, 3, size=n).astype(float)}
    for fk in rules:
        rating = RNG.choice(["Good", "Fair", "Poor"], size=n).astype(object)
        rating[RNG.random(n) < 0.05] = None
        frame[f"rating_{fk}"] = rating
        frame[f"index_{fk}"] = np.array([schemes.MIDPOINT.get(r, np.nan) if r else np.nan for r in rating])
        frame[f"method_{fk}"] = np.array([{"low_flow_baseflow_dynamics": "streamcat-hyd-integrity",
                                          "channel_floodplain_dynamics": "bank-height-ratio"}.get(fk, "x")] * n, dtype=object)
    frame["v__catchment_hydrology__impervious"] = RNG.uniform(0, 30, size=n)
    frame["v__catchment_hydrology__agriculture"] = RNG.uniform(0, 60, size=n)
    frame["c__streamflow_regime"] = np.where(RNG.random(n) < 0.7, 0.0, RNG.uniform(0, 30, size=n))
    frame["c__surface_water_storage"] = RNG.uniform(0, 10, size=n)
    frame["v__habitat_provision__woodyRiparian"] = RNG.uniform(0, 100, size=n)
    frame["v__light_thermal_regime__woodyRiparian"] = frame["v__habitat_provision__woodyRiparian"]
    frame["v__light_thermal_regime__impervious"] = frame["v__catchment_hydrology__impervious"]
    frame["c__carbon_processing"] = RNG.uniform(0, 100, size=n)
    frame["v__reach_inflow__roadDensity"] = RNG.uniform(0, 4, size=n)
    frame["c__reach_inflow"] = frame["v__reach_inflow__roadDensity"]
    frame["c__low_flow_baseflow_dynamics"] = RNG.uniform(0.8, 1.0, size=n)
    frame["v__nutrient_cycling__tn"] = RNG.uniform(0.1, 1.0, size=n)
    frame["v__nutrient_cycling__tp"] = RNG.uniform(0.01, 0.1, size=n)
    frame["ctx__nutrient_cycling__region"] = ["SAP"] * n
    return pd.DataFrame(frame)


def test_runs_score_the_views_and_write_the_comparison(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    table = _values_table()
    table.to_parquet(schemes.values_path(root), index=False)
    (root.analysis / "curves").mkdir()
    registry = pd.DataFrame([
        {"quantity": "woody_wsrp100", "level": "national", "stratum": "national:national", "split": "", "usable": True,
         "points_json": json.dumps([[0.0, 0.0], [20.0, 0.3], [45.0, 0.7], [80.0, 1.0], [90.0, 1.0]])},
        {"quantity": "wetland_ws", "level": "l3", "stratum": "l3:45", "split": "", "usable": True,
         "points_json": json.dumps([[0.0, 0.0], [1.0, 0.3], [3.0, 0.7], [6.0, 1.0], [7.0, 1.0]])},
        {"quantity": "wetland_ws", "level": "national", "stratum": "national:national", "split": "", "usable": False,
         "points_json": json.dumps([])},
    ])
    registry.to_parquet(schemes.registry_path(root), index=False)
    progress, control = state.Progress(root, quiet=True), state.Control(root)
    schemes.run(root, progress, control, {})
    s0 = pq.read_table(schemes.run_path(root, "S0")).to_pandas()
    sn = pq.read_table(schemes.run_path(root, "SN")).to_pandas()
    s3 = pq.read_table(schemes.run_path(root, "S3")).to_pandas()
    # S0 preserves harvested classes; its banded view uses the app's active rating mapping
    assert (s0["cls_catchment_hydrology"].fillna("x") == table["rating_catchment_hydrology"].fillna("x")).all()
    from easi import scoring
    row = 0
    per = {fk.replace("_", "-"): scoring.function_score(schemes.MIDPOINT[table[f"rating_{fk}"][row]])
           for fk in schemes.function_rules() if isinstance(table[f"rating_{fk}"][row], str)}
    assert s0["eci_banded"][row] == pytest.approx(scoring.rollup(per).ecosystem_condition_index, abs=1e-12)
    # pressure metrics become lines only in the continuous view; classes never change under S0
    assert set(s0["mode_catchment_hydrology"]) <= {"line", "s0"} and (s0["cls_reach_inflow"].fillna("x") == table["rating_reach_inflow"].fillna("x")).all()
    assert (s0["eci_continuous"].dropna() != s0["eci_banded"].dropna()).any()
    # SN scores habitat provision on the national woody curve; wetland cover has no usable national curve
    rated_today = table["rating_habitat_provision"].apply(lambda v: isinstance(v, str)).to_numpy()
    assert (sn.loc[rated_today, "mode_habitat_provision"] == "curve").all()          # unrated rows stay unrated
    assert set(sn["stratum_habitat_provision"].dropna()) == {"national:national"}
    rated = sn["idx_habitat_provision"].notna()
    expected = np.interp(table["v__habitat_provision__woodyRiparian"][rated], [0, 20, 45, 80, 90], [0, 0.3, 0.7, 1, 1])
    assert sn["idx_habitat_provision"][rated].to_numpy() == pytest.approx(expected)
    assert set(sn["mode_surface_water_storage"]) <= {"line", "s0"}
    # S3 uses the Level III wetland curve where it exists and the national woody curve elsewhere
    in45 = table["l3_code"] == "45"
    assert set(s3.loc[in45, "mode_surface_water_storage"].dropna()) <= {"curve", "s0"}
    assert (s3.loc[in45 & s3["idx_surface_water_storage"].notna(), "stratum_surface_water_storage"] == "l3:45").all()
    assert set(s3.loc[~in45, "mode_surface_water_storage"]) <= {"line", "s0"}
    assert (s3["depth_habitat_provision"].isin([-1, 3])).all()
    cands = pq.read_table(schemes.candidates_run_path(root, "SN")).to_pandas()
    assert "cand__light_thermal_regime__woody_only__idx" in cands.columns
    assert cands["cand__light_thermal_regime__corridor_conversion__cls"].isna().all()   # no corridor column in the fixture
    comparison = pd.read_csv(root.analysis / "schemes" / "scheme_comparison.csv")
    assert set(comparison["run"]) == set(schemes.RUNS) and set(comparison["view"]) == set(schemes.VIEWS)
    assert set(comparison["group"]) == {"US", "VA", "KS"}
    pinned = pd.read_csv(root.analysis / "schemes" / "pinned_cells.csv")
    assert len(pinned) == 5 * 2 * 20
    gradients = pd.read_csv(root.analysis / "schemes" / "sanity_gradients.csv")
    assert set(gradients["gradient"]) == {"urban_vs_rural", "agricultural_vs_forested", "regulated_vs_free", "dammed_vs_free", "canal_vs_natural"}
