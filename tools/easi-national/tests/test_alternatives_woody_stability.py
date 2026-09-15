import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import woody_stability as w
from builder.analysis.alternatives.io import sha


def test_classes_are_live_inclusive_edges_and_clamped():
    points = [[0, 0], [1, 1]]
    values = [-1, np.nextafter(.39, 0), .39, np.nextafter(.69, 0), .69, 2, np.nan, np.inf]
    assert w.runtime_classes(points, values).tolist() == [0, 0, 1, 1, 2, 2, -1, -1]


def test_cluster_draws_keep_groups_and_record_invalid_draws(monkeypatch):
    values = np.array([10., 11., 20., 21., 30., 31.])
    population = np.array([.2, .5, .8, np.nan])
    samples = []
    def fit(sample):
        samples.append(sample.copy())
        if np.mean(sample) < 20:
            return {"status": "degenerate_q25"}
        return {"status": "complete", "points": [[0, 0], [1, 1]]}
    monkeypatch.setattr(w, "_rounded_fit", fit)
    result = w.bootstrap(values, ["a", "a", "b", "b", "c", "c"], population,
                         {"points": [[0, 0], [1, 1]]}, n_boot=20, seed=3)
    assert all(len(s) == 6 and all(np.sum(s == lo) == np.sum(s == lo + 1) for lo in [10, 20, 30]) for s in samples)
    assert 0 < result["n_valid"] < 20
    assert result["n_valid"] + sum(result["invalid_draws"].values()) == 20
    assert result["n_clusters"] == 3 and result["n_members"] == 6
    assert result["n_population"] == 3 and result["n_population_total"] == 4
    assert result["mean_flip"] == 0 and result["x39_lo"] == pytest.approx(.39)


def test_six_place_fit_linkage_and_wrong_reference_rejected():
    values = np.linspace(10, 90, 40)
    fit = w._rounded_fit(values)
    assert w.fit_linkage(values, fit)["status"] == "canonical_match"
    wrong = dict(fit, q25=fit["q25"] + .000001)
    assert w.fit_linkage(values, wrong)["status"] == "mismatch"
    assert "q25" in w.fit_linkage(values, wrong)["mismatches"]
    assert all(x == round(x, 6) for p in fit["points"] for x in p)


def test_transitions_use_identical_full_population_including_missing():
    result = w.transitions([0, 1, 2, -1], [1, 1, 0, -1])
    assert result["population_n"] == 4 and result["changed_n"] == 2
    assert result["design"] == "full-l2-8.2" and result["weighted"] is False
    assert sum(r["n"] for r in result["rows"]) == 4
    assert any(r["from"] == r["to"] == "Not rated" for r in result["rows"])
    with pytest.raises(ValueError): w.transitions([0], [0, 1])


def fixture_study(root):
    from builder.analysis.artifact import _curve
    original = root / "review/2026-09-15-regional/baseline/analysis"
    study = root / "review/alternative-studies/test"
    (study / "snapshot/app-data").mkdir(parents=True)
    (original / "curves").mkdir(parents=True)
    (original / "panels").mkdir()
    (root / "analysis").mkdir()
    values = np.linspace(10, 90, 40)
    rows = [{"comid": i+1, "l2": "8.2" if i < 20 else "8.3", "woody_wsrp100": float(v)} for i, v in enumerate(values)]
    pq.write_table(pa.Table.from_pylist(rows), original / "landscape.parquet")
    # Extra population reaches are deliberately absent from the reference panel.
    current = rows + [{"comid": 100, "l2": "8.2", "woody_wsrp100": 1.}, {"comid": 101, "l2": "8.2", "woody_wsrp100": None}]
    pq.write_table(pa.Table.from_pylist(current), root / "analysis/landscape.parquet")
    registry, members = [], []
    for key, n, level, stratum in [("8.2", 20, "l2", "l2:8.2"), ("national", 40, "national", "national:national")]:
        registry.append({"quantity": "woody_wsrp100", "level": level, "stratum": stratum, "split": "", "usable": True,
                         "panel_tier": "complete", "screen": "strict", "n_members": n, **w._rounded_fit(values[:n])})
        members.extend({"comid": i+1, "huc12": str(i//2), "level": level, "stratum": stratum} for i in range(n))
    rp = original / "curves/curve_registry.parquet"
    pq.write_table(pa.Table.from_pylist(registry), rp)
    pq.write_table(pa.Table.from_pylist(members), original / "panels/panel_members.parquet")
    artifact = {"provenance": {"registry": {"sha256": sha(rp)}}, "sets": {"corridor-woody": {"curves": {
        "8.2": _curve(registry[0]), "national": _curve(registry[1])}}}}
    (study / "snapshot/app-data/reference-curves.json").write_text(json.dumps(artifact))
    # A current snapshot registry must not accidentally replace the original.
    (study / "snapshot/analysis/curves").mkdir(parents=True)
    (study / "snapshot/analysis/curves/curve_registry.parquet").write_bytes(b"wrong-current-registry")
    return study, original


def test_run_uses_original_panels_and_same_full_population_only_writes_study(tmp_path):
    study, original = fixture_study(tmp_path)
    paths = list(original.rglob("*.parquet")) + [tmp_path / "analysis/landscape.parquet"]
    before = {str(p): sha(p) for p in paths}
    result = w.run(tmp_path, study, n_boot=4)
    assert result["status"] == "complete"
    assert [r["n_population_total"] for r in result["curves"]] == [22, 22]
    assert [r["n_population"] for r in result["curves"]] == [21, 21]
    assert [r["n_panel_members"] for r in result["curves"]] == [20, 40]
    assert result["comparison"]["population_n"] == 22
    assert result["provenance"]["protected_inputs"]["registry"]["path"].startswith(str(original))
    assert before == {str(p): sha(p) for p in paths}
    assert w.run(tmp_path, study, n_boot=4)["input_digest"] == result["input_digest"]
    with pytest.raises(RuntimeError, match="different inputs"):
        w.run(tmp_path, study, n_boot=5)


def test_original_registry_tamper_fails_before_bootstrap(tmp_path):
    study, original = fixture_study(tmp_path)
    (original / "curves/curve_registry.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="frozen artifact provenance"):
        w.run(tmp_path, study, n_boot=2)
    assert not (study / "woody-stability/result.json").exists()
