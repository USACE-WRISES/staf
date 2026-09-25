"""The stability test (O5): the HUC12-cluster bootstrap class-flip rate at reference
stations through the shipping engine, the ACC-04 drop-one shift, the evidence package
read for the pools, and the end-to-end run on a synthetic campaign."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves import round2

APP = Path(__file__).resolve().parents[1]
SCRIPTS = APP / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_stability_test as st  # noqa: E402

ENTRY = {"higher_is_better": False, "metric_family": "continuous"}
PROTOCOL = """\
schema: evaluation-protocol/1
protocol_version: "1.0"
campaign: test-campaign
candidates:
  B1: {family: pool_rule, config: {reference_pool.ladder_rule: narrowest_adequate}, primary_outcome: O1, decision: accuracy_change}
"""


def _pool(n: int = 36, seed: int = 4):
    rng = np.random.default_rng(seed)
    ids = [f"R{i:03d}" for i in range(n)]
    values = pd.Series(np.exp(rng.normal(np.log(150.0), 0.4, size=n)), index=ids)
    clusters = pd.Series([f"huc{i % 6}" for i in range(n)], index=ids)
    return values, clusters


def test_flip_rate_is_a_share_and_deterministic():
    values, clusters = _pool()
    one = st.flip_rate(values, ENTRY, clusters, n_boot=40, seed=11)
    two = st.flip_rate(values, ENTRY, clusters, n_boot=40, seed=11)
    assert one == two
    assert one["n_values"] == 36 and one["n_clusters"] == 6 and one["curve_status"] == "complete"
    assert 0.0 <= one["flip_median"] <= 1.0 and one["flip_median"] <= one["flip_p90"] <= 1.0
    assert 0 < one["n_boot_valid"] <= 40
    other = st.flip_rate(values, ENTRY, clusters, n_boot=40, seed=12)
    assert other["n_boot_valid"] == one["n_boot_valid"] and other != one or one["flip_median"] == 0.0


def test_flip_rate_needs_a_curve():
    values, clusters = _pool(n=3)
    got = st.flip_rate(values, ENTRY, clusters, n_boot=5, seed=1)
    assert got["flip_median"] is None and got["n_boot_valid"] == 0
    empty = st.flip_rate(pd.Series(dtype=float), ENTRY, clusters, n_boot=5, seed=1)
    assert empty["n_values"] == 0


def test_acc04_fields():
    values, _ = _pool()
    got = st.acc04(values, ENTRY)
    assert set(got) == {"acc04_max_shift_iqr", "acc04_max_param_change_frac", "acc04_decision_flip", "acc04_flagged"}
    assert got["acc04_max_shift_iqr"] >= 0.0 and got["acc04_decision_flip"] is False


def _campaign(root: Path, ids: list[str]) -> Path:
    run = root / "l3-55"
    v = run / "library" / "assessments" / "test-region" / "v1"
    v.mkdir(parents=True)
    (v.parent / "manifest.json").write_text(json.dumps({"latestVersion": 1}), encoding="utf-8")
    bundle = {"region": {"code": "55"}, "metricsByFunction": [
        {"functionId": "water-soil-quality", "metrics": [
            {"metricId": "spring-chem-cond", "criteriaBasis": "reference", "curve": {"points": [{"x": 50, "y": 1}, {"x": 800, "y": 0}]}}]},
        {"functionId": "catchment-hydrology", "metrics": [
            {"metricId": "spring-pctimp2019ws", "criteriaBasis": "fixed", "curve": {"points": [{"x": 0, "y": 1}, {"x": 44.5, "y": 0}]}}]}]}
    (v / "assessment.deep.json").write_text(json.dumps(bundle), encoding="utf-8")
    data = run / "evidence" / "deep-dev-l3-55" / "data"
    data.mkdir(parents=True)
    pools = {"chem_COND": {"record": {"status": "local"}, "option": "local", "stationIds": ids},
             "chem_PH": {"record": {"status": "insufficient"}, "option": None, "stationIds": []},
             "pctimp2019ws": {"record": {"status": "fixed"}, "option": None, "stationIds": ids}}
    (data / "pools.json").write_text(json.dumps(pools), encoding="utf-8")
    return root


def test_pools_and_scored_keys_read_the_run_folder(tmp_path):
    values, _ = _pool()
    campaign = _campaign(tmp_path / "campaign", list(values.index))
    pools = st.pools_for_run(campaign / "l3-55")
    assert set(pools) == {"chem_COND", "pctimp2019ws"} and pools["chem_COND"]["option"] == "local"
    bundle = st.staged_bundle(campaign / "l3-55")
    assert st.scored_metric_keys(bundle, list(pools)) == {"chem_COND"}, "fixed criteria are not resampled"


def test_run_end_to_end_on_a_synthetic_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr("streamcurves.code_identity.fingerprint", lambda app_root=None: "f" * 64)
    values, clusters = _pool()
    campaign = _campaign(tmp_path / "campaign", list(values.index))
    protocol = tmp_path / "protocol.yaml"
    protocol.write_text(PROTOCOL, encoding="utf-8")
    wide = pd.DataFrame({"site_id": values.index, "chem_COND": values.to_numpy()}).set_index("site_id", drop=False)
    inputs = {"frame": pd.DataFrame({"station_key": values.index}), "values": wide, "clusters": clusters,
              "metric_config": {"chem_COND": {**ENTRY, "column_name": "chem_COND"}}, "value_policy": "newest-nonnull-v2"}
    got = st.run(campaign=campaign, out_dir=tmp_path / "out", protocol=protocol, n_boot=30, seed=11,
                 inputs=inputs, config_root=tmp_path / "cfg", progress=lambda *a: None)
    table = got["table"]
    assert list(table.columns) == st.COLUMNS and len(table) == 1
    row = table.iloc[0]
    assert row["l3"] == "55" and row["metric"] == "chem_COND" and row["n_pool"] == 36
    assert 0.0 <= row["flip_median"] <= 1.0 and row["n_boot_valid"] > 0
    assert row["seed"] == round2.seed_for("55", "chem_COND", "stability", seed=11)
    summary = json.loads((tmp_path / "out" / st.SUMMARY_FILE).read_text(encoding="utf-8"))
    assert summary["stamp"]["protocol"]["sha256"] == round2.sha256_of(protocol)
    assert summary["stamp"]["configRoot"].endswith("cfg") and summary["n_cells"] == 1
    assert summary["flip_rate"]["median"] == pytest.approx(row["flip_median"])
    again = st.run(campaign=campaign, out_dir=tmp_path / "out2", protocol=protocol, n_boot=30, seed=11,
                   inputs=inputs, progress=lambda *a: None)
    assert again["table"].equals(table)
