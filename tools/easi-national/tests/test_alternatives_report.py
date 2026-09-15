"""Bounded report integration tests with synthetic sources and score tables."""
import hashlib
import json
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import report
from builder.analysis.alternatives.io import info, read_json, sha, write_json


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    root, workspace = tmp_path / "data", tmp_path / "workspace"
    study = root / "review/alternative-studies/synthetic"
    package = workspace / "tools/alternatives"
    package.mkdir(parents=True)
    (package / "report.py").write_text("version = 1\n")
    for name in ("local_review.py", "alternative_review.py"):
        path = workspace / "apps/easi" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version = 1\n")
    monkeypatch.setattr(report, "REPO_ROOT", workspace)
    monkeypatch.setattr(report, "__file__", str(package / "report.py"))
    original = root / "analysis/reference.json"
    write_json(original, {"reference": "frozen"})
    copy = study / "snapshot/analysis/reference.json"
    copy.parent.mkdir(parents=True)
    shutil.copy2(original, copy)
    receipt = {**info(original), "relative_path": "snapshot/analysis/reference.json"}
    write_json(study / "snapshot/manifest.json", {"files": [receipt]})
    # Repeated source references should not inflate the resulting inventory.
    write_json(study / "stages/snapshot.json", {"inputs": [info(original)], "source_inputs": []})
    daily = study / "acquisition/daily.json"
    write_json(daily, {"observations": []})
    write_json(study / "acquisition/manifest.json", {"sources": [], "daily": [info(daily)]})
    return root, study, workspace, original, copy


def test_source_inventory_preserves_original_and_snapshot_copy_bindings(inventory):
    root, study, workspace, original, copy = inventory
    rows, digest = report.source_inventory(root, study)
    original_entry = next(row for row in rows if row["scope"] == "data" and row["path"] == "analysis/reference.json")
    copy_entry = next(row for row in rows if row["scope"] == "data" and row["path"] == copy.relative_to(root).as_posix())
    assert original_entry["sha256"] == copy_entry["sha256"] == sha(original)
    assert len({(r["scope"], r["path"]) for r in rows}) == len(rows)
    assert rows == sorted(rows, key=lambda r: (r["scope"], r["path"]))
    assert digest == hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                               allow_nan=False).encode()).hexdigest()
    assert read_json(study / "source-inventory.json")["count"] == len(rows)
    for row in rows:
        assert row["scope"] in ("data", "workspace")
        assert not Path(row["path"]).is_absolute()
        assert type(row["size"]) is type(row["mtime_ns"]) is int
        assert len(row["sha256"]) == 64
    assert report.source_inventory(root, study) == (rows, digest)


@pytest.mark.parametrize("which", ["original", "copy"])
def test_changed_captured_source_fails_closed_preserving_previous_inventory(inventory, which):
    root, study, workspace, original, copy = inventory
    report.source_inventory(root, study)
    previous = (study / "source-inventory.json").read_bytes()
    (original if which == "original" else copy).write_text("changed captured source with a different size\n")
    with pytest.raises(RuntimeError, match="Captured study source changed"):
        report.source_inventory(root, study)
    assert (study / "source-inventory.json").read_bytes() == previous


def test_inventory_rejects_captured_paths_outside_declared_roots(inventory, tmp_path):
    root, study, workspace, original, copy = inventory
    outside = tmp_path / "outside.json"
    write_json(outside, {"value": 1})
    write_json(study / "acquisition/manifest.json", {"sources": [info(outside)], "daily": []})
    with pytest.raises(RuntimeError, match="outside"):
        report.source_inventory(root, study)


def test_empty_field_targets_do_not_make_a_positive_recommendation(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("status\n")
    assert report.csv_rows(path) == []
    result = report.recommendation([], [])
    assert result["recommended"] == "alternative-1"
    assert not any(row["eligible"] for row in result["candidates"])
    assert result["automatic_scoring_change"] is False


def _noninferior_endpoints(aid="alternative-3"):
    frozen = [{"alternative_id": aid, "cohort": "latest_visit1", "region": "US", "function": "eci",
               "target": target, "statistic": "auc_good_vs_poor", "support_floor_met": True,
               "noninferiority_status": "noninferior", "alternative": .7, "ci_low": -.005, "ci_high": .01,
               "boot_requested": 1000, "boot_valid": 1000} for target in report.TARGETS]
    return frozen, [{**row, "cohort": "spatial_refit:latest_visit1"} for row in frozen]


def _decline(design, statistic="spearman", **overrides):
    return {"alternative_id": "alternative-3", "cohort": "latest_visit1" if design == "frozen" else "spatial_refit:latest_visit1",
            "region": "NAP", "function": "low_flow_baseflow_dynamics", "target": "wetted_bankfull_ratio",
            "statistic": statistic, "support_floor_met": True, "boot_requested": 1000, "boot_valid": 1000,
            "ci_low": -.04, "ci_high": -.02, **overrides}


@pytest.mark.parametrize("design", ["frozen", "watershed-held-out"])
@pytest.mark.parametrize("statistic", ["spearman", "weighted_kappa"])
@pytest.mark.parametrize("region", ["US", "NAP", "woody_L2_8.2"])
def test_supported_signed_or_class_decline_requires_review(design, statistic, region):
    frozen, spatial = _noninferior_endpoints()
    rows = frozen if design == "frozen" else spatial
    rows.append(_decline(design, statistic, region=region))
    result = report.recommendation(frozen, spatial)
    assert result["recommended"] == "alternative-1"
    candidate = next(row for row in result["candidates"] if row["alternative_id"] == "alternative-3")
    assert not candidate["deterioration_findings"]
    finding, = candidate["unresolved_review_findings"]
    assert finding["comparison_design"] == design and finding["statistic"] == statistic
    assert "no equivalence margin" in finding["reason"]
    assert any("require review" in reason for reason in candidate["reasons"])


@pytest.mark.parametrize("design", ["frozen", "watershed-held-out"])
def test_supported_regional_auc_decline_blocks_in_both_designs(design):
    frozen, spatial = _noninferior_endpoints()
    (frozen if design == "frozen" else spatial).append(_decline(design, "auc_good", function="habitat_provision"))
    result = report.recommendation(frozen, spatial)
    assert result["recommended"] == "alternative-1"
    candidate = next(row for row in result["candidates"] if row["alternative_id"] == "alternative-3")
    finding, = candidate["deterioration_findings"]
    assert finding["comparison_design"] == design and "-0.01" in finding["reason"]
    assert len(candidate["spatial_conflicts"]) == (design == "watershed-held-out")


@pytest.mark.parametrize("override", [
    {"boot_valid": 799}, {"boot_valid": 1001}, {"boot_requested": 999}, {"support_floor_met": False},
    {"ci_high": None}, {"ci_low": None}, {"ci_low": float("nan")}, {"ci_low": -.01}, {"ci_high": 0},
    {"cohort": "retrospective_2324"}, {"function": "population_support"},
])
def test_small_invalid_or_out_of_scope_descriptives_do_not_block(override):
    frozen, spatial = _noninferior_endpoints()
    frozen.append(_decline("frozen", **override))
    assert report.recommendation(frozen, spatial)["recommended"] == "alternative-3"


def test_review_boundary_and_simplification_sort_are_preserved():
    frozen, spatial = _noninferior_endpoints()
    extra_frozen, extra_spatial = _noninferior_endpoints("alternative-2")
    frozen += extra_frozen
    spatial += extra_spatial
    frozen.append(_decline("frozen", "auc_good", ci_high=-.01))
    assert report.recommendation(frozen, spatial)["recommended"] == "alternative-3"
    spatial.append(_decline("watershed-held-out", boot_valid=800))
    assert report.recommendation(frozen, spatial)["recommended"] == "alternative-2"


def test_report_uses_raw_eci_and_keeps_empty_field_results_explicit(tmp_path, monkeypatch):
    root, study = tmp_path / "data", tmp_path / "data/review/alternative-studies/test"
    write_json(study / "manifest.json", {"input_digest": "test-digest", "parent_binding": {"method_version": "fixed"}})
    write_json(study / "scores/verification.json", {"status": "passed"})
    write_json(study / "cohorts/sampling.json", {"population_n": 200})
    write_json(study / "cohorts/cohort_summary.json", {"rows": 0})
    write_json(study / "woody-stability/result.json", {"curves": [], "comparison": {"rows": []}})
    cohort = [{"comid": 1, "sample": True, "sample_weight": 100., "woody_82": True},
              {"comid": 2, "sample": True, "sample_weight": 100., "woody_82": False}]
    pq.write_table(pa.Table.from_pylist(cohort), study / "cohorts/reaches.parquet")
    for alternative in report.ALTERNATIVES:
        aid = alternative["id"]
        rows = [{"comid": comid, "eci": .617, **{"rating__" + name: "Fair" for name in report.CHANGED}}
                for comid in (1, 2)]
        pq.write_table(pa.Table.from_pylist(rows), study / "scores" / (aid + ".parquet"))
        write_json(study / "candidates" / aid / "app-data/reference-curves.json",
                   {"sets": {"test": {"curves": {"national": {"points": [[0, 0], [1, 1]]}}}}})
    monkeypatch.setattr(report, "source_inventory", lambda root, study: ([], "test-sources"))
    result = report.build(root, study)
    summary = read_json(study / "summary.json")
    assert result["recommended"] == "alternative-1"
    assert all(row["differences"]["eci_mean"] == .617 for row in summary["alternatives"])
    assert all(row["field_agreement"] == [] for row in summary["alternatives"])
    assert all(row["differences"]["eci_mean_delta"] == 0 for row in summary["alternatives"])
    assert all(row["coverage"]["rated_n"] == 200 for row in summary["alternatives"])
    completion = read_json(study / "completion.json")
    assert completion["output_hashes"]["summary.json"] == sha(study / "summary.json")
