"""Independent acceptance checks for replay, scope and resumed study receipts."""
from copy import deepcopy

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import evidence, report, scorer, study
from builder.analysis.alternatives.io import info, output_files, sha, write_json


def parquet(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.fixture
def replay(tmp_path):
    reaches = [{"comid": 1, "l2": "8.3", "evidence_available": True},
               {"comid": 2, "l2": "8.2", "evidence_available": True}]
    rows = [{"comid": i, "eci": .5, "physical": .5, "chemical": .5, "biological": .5,
             "rating__habitat_provision": "Fair", "score__habitat_provision": 8,
             "index__habitat_provision": .545} for i in (1, 2)]
    def original(row):
        return {"comid": row["comid"], "eci_raw": row["eci"], "phys_raw": row["physical"],
                "chem_raw": row["chemical"], "bio_raw": row["biological"],
                "rating_habitat_provision": row["rating__habitat_provision"],
                "fs_habitat_provision": row["score__habitat_provision"],
                "index_habitat_provision": row["index__habitat_provision"]}
    parquet(tmp_path / "cohorts/reaches.parquet", reaches)
    parquet(tmp_path / "snapshot/analysis/values.parquet", [original(rows[0])])
    parquet(tmp_path / "snapshot/analysis/nrsa/nrsa_desktop.parquet",
            [{**original(rows[1]), "desktop_source": "synthetic"},
             {**original(rows[1]), "desktop_source": "synthetic"}])
    for alternative in range(1, 5):
        parquet(tmp_path / f"scores/alternative-{alternative}.parquet", rows)
    return tmp_path, rows


def test_replay_covers_repeated_nrsa_stations_and_rejects_synthetic_drift(replay):
    folder, rows = replay
    result = scorer.verify(folder)
    assert result["replayed_stored_reaches"] == 1
    assert result["replayed_nrsa_stations"] == 2
    changed = deepcopy(rows)
    changed[1]["index__habitat_provision"] = .85
    parquet(folder / "scores/alternative-1.parquet", changed)
    with pytest.raises(RuntimeError, match="NRSA replay mismatch"):
        scorer.verify(folder)


def test_replay_requires_full_expected_evidence_cohort(replay):
    folder, rows = replay
    parquet(folder / "scores/alternative-1.parquet", rows[:1])
    with pytest.raises(RuntimeError, match="eligible evidence cohort"):
        scorer.verify(folder)


def test_woody_override_cannot_escape_level_ii_82(replay):
    folder, rows = replay
    changed = deepcopy(rows)
    changed[0]["index__habitat_provision"] = .85
    parquet(folder / "scores/alternative-4.parquet", changed)
    with pytest.raises(RuntimeError, match="escaped Level II 8.2"):
        scorer.verify(folder)


def test_weighted_sample_accounts_for_every_stratum_without_census_oversampling():
    frame = pd.DataFrame({"comid": range(1, 24), "state": ["A"] + ["B"] * 2 + ["C"] * 20,
                          "l2": ["1"] * 23})
    sampled, strata = evidence.stratified_sample(frame, n=8)
    assert sampled["sample"].sum() == 8
    for row in strata:
        selected = sampled[sampled.state.eq(row["state"]) & sampled["sample"]]
        assert len(selected) <= row["population_n"]
        assert selected.sample_weight.sum() == pytest.approx(row["population_n"])
    assert sampled.loc[sampled["sample"], "sample_weight"].sum() == pytest.approx(len(frame))


def _snapshot_receipt(folder, monkeypatch):
    source = folder / "source.py"
    source.write_text("version=1\n")
    monkeypatch.setattr(study, "stage_sources", lambda step: [source])
    preserved = folder / "snapshot/engine/module.py"
    preserved.parent.mkdir(parents=True)
    preserved.write_text("preserved=1\n")
    snapshot = folder / "snapshot/manifest.json"
    write_json(snapshot, {"files": [{**info(preserved), "relative_path": "snapshot/engine/module.py"}]})
    manifest = {"input_digest": "fixed-study"}
    digest, inputs = study._digest(folder, "snapshot", manifest)
    write_json(folder / "stages/snapshot.json", {"status": "complete", "input_digest": digest,
               "inputs": inputs, "outputs": output_files(folder, [snapshot])})
    return manifest, preserved, source


def test_snapshot_reuse_checks_preserved_files_not_only_manifest(tmp_path, monkeypatch):
    manifest, preserved, _ = _snapshot_receipt(tmp_path, monkeypatch)
    study._receipt_current(tmp_path, "snapshot", manifest, set())
    preserved.write_text("changed=2\n")
    with pytest.raises(RuntimeError, match="Preserved snapshot changed"):
        study._receipt_current(tmp_path, "snapshot", manifest, set())


def test_corrected_protocol_invalidates_study_without_mutating_snapshot(tmp_path, monkeypatch):
    from easi import config, national
    _, preserved, _ = _snapshot_receipt(tmp_path, monkeypatch)
    artifact = tmp_path / "apps/easi/data/reference-curves.json"
    write_json(artifact, {})
    monkeypatch.setattr(study, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(study, "BASE_REFERENCE", sha(artifact))
    monkeypatch.setattr(config, "criteria_set", lambda: "regional")
    monkeypatch.setattr(national, "method_version", lambda: study.BASE_METHOD)
    write_json(tmp_path / "analysis/local-review/completion.json", {"status": "complete", "method_version": study.BASE_METHOD})
    write_json(tmp_path / "state/queue.json", {"items": []})
    write_json(tmp_path / "manifest.json", {"status": "complete", "protocol": {"fold_seed": 17},
                                          "input_files": [], "input_digest": "old"})
    before = info(preserved)
    study.snapshot(tmp_path, tmp_path)
    after = study.read_json(tmp_path / "manifest.json")
    assert after["status"] == "pending" and after["input_digest"] != "old"
    assert "fold_seed" not in after["protocol"]
    assert "easi-field-fold-v1:" in after["protocol"]["fold_assignment"]
    assert info(preserved) == before


def test_changed_computation_invalidates_completed_receipt(tmp_path, monkeypatch):
    manifest, _, source = _snapshot_receipt(tmp_path, monkeypatch)
    source.write_text("version=2\n")
    with pytest.raises(RuntimeError, match="Stale dependency"):
        study._receipt_current(tmp_path, "snapshot", manifest, set())


def test_changed_dependency_output_is_rejected_before_downstream_read(tmp_path, monkeypatch):
    manifest, _, _ = _snapshot_receipt(tmp_path, monkeypatch)
    output = tmp_path / "cohorts/ledger.json"
    write_json(output, {"rows": 1})
    digest, inputs = study._digest(tmp_path, "observations", manifest)
    write_json(tmp_path / "stages/observations.json", {"status": "complete", "input_digest": digest,
               "inputs": inputs, "outputs": output_files(tmp_path, [output])})
    study._receipt_current(tmp_path, "observations", manifest, set())
    write_json(output, {"rows": 2})
    with pytest.raises(RuntimeError, match="Changed or missing output"):
        study._receipt_current(tmp_path, "observations", manifest, set())


def test_recommendation_needs_supported_spatial_uncertainty_not_just_sample_size():
    frozen = [{"alternative_id": "alternative-3", "cohort": "latest_visit1", "region": "US", "function": "eci",
               "target": target, "statistic": "auc_good_vs_poor", "support_floor_met": True,
               "noninferiority_status": "noninferior", "alternative": .7, "ci_low": -.005, "ci_high": .01,
               "boot_valid": 1000} for target in ("reference_2013", "t__bent_mmi")]
    heldout = [{**row, "cohort": "spatial_refit:latest_visit1"} for row in frozen]
    assert report.recommendation(frozen, heldout)["recommended"] == "alternative-3"
    unsupported = [{**row, "ci_low": None, "ci_high": None, "boot_valid": 0} for row in heldout]
    assert report.recommendation(frozen, unsupported)["recommended"] == "alternative-1"
    inconclusive = [{**row, "ci_low": -.1, "noninferiority_status": "inconclusive"} for row in heldout]
    assert report.recommendation(frozen, inconclusive)["recommended"] == "alternative-1"
