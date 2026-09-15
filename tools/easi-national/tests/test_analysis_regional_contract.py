"""Regional analysis quantities retain their method identity and precision."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis import schemes, values
from builder.paths import DataRoot


def test_curve_class_edges_match_app_interpolation_without_moving_eci_bands(monkeypatch):
    from easi import screening_methods as sm

    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    points = [[0.0, 0.0], [1.0, 1.0]]
    curve = {"points": points, "x39": 0.39, "x69": 0.69, "n": 100}
    monkeypatch.setattr(sm, "curve_sets", lambda: {
        "synthetic": {"stratifier": "l2", "higherIsBetter": True,
                      "curves": {"8.3": curve, "national": curve}}})
    rule = {"curve": {"set": "synthetic", "stratifier": "l2", "fallback": ["national"], "mode": "banded"}}
    x = [np.nextafter(.39, 0), .39, np.nextafter(.39, 1),
         np.nextafter(.69, 0), .69, np.nextafter(.69, 1)]
    expected = [sm.score_quantity(v, rule, {"l2": "8.3"})[0] for v in x]
    assert expected == ["Poor", "Fair", "Fair", "Fair", "Good", "Good"]
    assert schemes.class_of_index([*x, np.nan]).tolist() == [*expected, None]
    fk = "low_flow_baseflow_dynamics"
    table = pa.table({f"rating_{fk}": ["Poor"] * len(x),
                      f"method_{fk}": ["erom-flow-variability"] * len(x), f"c__{fk}": x})
    frame = pd.DataFrame({"l2": ["8.3"] * len(x), "l1": ["8"] * len(x)})
    run = schemes.Run("S2", "l2", table, frame, {fk: schemes.function_rules()[fk]}, [
        {"quantity": "q_cv_monthly", "level": "l2", "stratum": "l2:8.3", "split": "",
         "usable": True, "points": points}])
    run.score()
    assert run.classes[fk].tolist() == expected
    # Aggregate condition bands retain the separate STAF inclusive upper edges.
    summary = schemes.comparison_rows(SimpleNamespace(name="S0", rules={}),
        {"banded": {"eci": np.array([.39, .69])}}, np.array(["VA", "VA"]))
    row = next(r for r in summary if r["view"] == "banded" and r["group"] == "US")
    assert row["share_nf"] == .5 and row["share_ar"] == .5 and row["share_f"] == 0


def test_low_flow_curve_applies_only_to_erom_and_bed_sed_curve_is_retired(monkeypatch):
    monkeypatch.setenv("EASI_CRITERIA_SET", "regional")
    low, bed = "low_flow_baseflow_dynamics", "bed_composition_bedform_dynamics"
    frame = pd.DataFrame({"l2": ["8.3"] * 3, "l1": ["8"] * 3})
    table = pa.table({
        f"rating_{low}": ["Poor"] * 3,
        f"method_{low}": ["erom-flow-variability", "streamcat-hyd-integrity", "nrsa-wetted-channel-condition"],
        f"c__{low}": [0.2] * 3,
        f"rating_{bed}": ["Fair"] * 3,
        f"method_{bed}": ["streamcat-sed-integrity"] * 3,
        f"c__{bed}": [0.2] * 3,
    })
    registry = [{"quantity": quantity, "level": "l2", "stratum": "l2:8.3", "split": "",
                 "usable": True, "points": [[0.0, 1.0], [1.0, 0.0]]}
                for quantity in ["q_cv_monthly", "sed_min"]]
    rules = schemes.function_rules()
    run = schemes.Run("S2", "l2", table, frame, {low: rules[low], bed: rules[bed]}, registry)
    run.score()
    assert run.mode[low].tolist() == ["curve", "s0", "s0"]
    assert run.classes[low].tolist() == ["Good", "Poor", "Poor"]
    assert run.index[low].tolist() == pytest.approx([0.8, 0.195, 0.195])
    assert set(run.mode[bed]) <= {"s0", "line"}
    assert run.classes[bed].tolist() == ["Fair"] * 3


def test_curve_source_and_method_changes_invalidate_runs(tmp_path, monkeypatch):
    root = DataRoot(tmp_path / "data").ensure()
    key = "low_flow_baseflow_dynamics"
    baseline = schemes.inputs(root)
    original = schemes.CURVE_INPUTS[key][0]
    monkeypatch.setitem(schemes.CURVE_INPUTS, key, [(original[0], original[1], "another-method")])
    assert schemes.inputs(root) != baseline
    monkeypatch.setitem(schemes.CURVE_INPUTS, key, [("hyd_min", original[1], original[2])])
    assert schemes.inputs(root) != baseline


def test_landscape_parity_keeps_regional_and_legacy_units_separate(tmp_path):
    root = DataRoot(tmp_path / "data").ensure()
    root.analysis.mkdir()
    low, bed = "low_flow_baseflow_dynamics", "bed_composition_bedform_dynamics"
    pq.write_table(pa.table({
        "comid": [1, 2, 3], f"c__{low}": [0.621233, 0.95, 85.0],
        f"method_{low}": ["erom-flow-variability", "streamcat-hyd-integrity", "nrsa-wetted-channel-condition"],
        f"c__{bed}": [18.23, 0.85, 40.0],
        f"method_{bed}": ["watershed-agriculture-share", "streamcat-sed-integrity", "nrsa-substrate-condition"],
    }), values.values_path(root))
    landscape = pa.table({
        "comid": [1, 2, 3], "erom__q_cv_monthly": [0.6212327, 0.5, 0.4],
        "hyd_min": [0.8, 0.95, 0.2], "agriculture_ws": [18.234, 24.0, 72.0],
        "sed_min": [0.7, 0.85, 0.1],
    })
    report = values.landscape_parity(root, landscape, tolerance=0)
    assert report["pairs_checked"] == 4 and report["failed"] == []
    assert all(pair["compared"] == 1 for pair in report["pairs"].values())
    cv = report["pairs"][f"c__{low} vs erom__q_cv_monthly"]
    assert cv["allowance"] == 0.000001 and cv["restricted_to"] == "erom-flow-variability"
    ag = report["pairs"][f"c__{bed} vs agriculture_ws"]
    assert ag["allowance"] == 0.01 and ag["restricted_to"] == "watershed-agriculture-share"
