from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from builder import REPO_ROOT
from builder.analysis.alternatives import REGIONAL_SETS
from builder.analysis.alternatives.candidates import candidate_assets, copy_candidate_file
from builder.analysis.alternatives.evidence import stratified_sample
from builder.analysis.alternatives.io import fingerprint, read_json, safe_study, write_json
from builder.analysis.alternatives.scorer import flatten
from builder.analysis.alternatives.report import recommendation


def _assets():
    data = REPO_ROOT / "apps/easi/data"
    artifact, catalog = read_json(data / "reference-curves.json"), read_json(data / "screening-methods.json")
    # Candidate transformations start from an A1-shaped snapshot, independently
    # of whichever candidate the live app currently uses. Synthetic strata keep
    # the 62 -> 61 deletion/count contract without inventing historical fits.
    from builder.analysis.alternatives.candidates import replace_stratifiers
    for name in REGIONAL_SETS:
        definition = artifact["sets"][name]
        n = 20 if name == "corridor-woody" else 19
        base = definition["curves"]["national"]
        definition.update(stratifier="l2", curves={
            key: deepcopy(base) for key in ["national", "8.2", *[f"synthetic-{i}" for i in range(n-2)]]})
    replace_stratifiers(catalog, "l2")
    return artifact, catalog


def test_candidate_copy_does_not_inherit_immutable_snapshot_permissions(tmp_path):
    import stat
    source, target = tmp_path / "snapshot.json", tmp_path / "candidate.json"
    source.write_bytes(b'{"value": 1}')
    source.chmod(stat.S_IREAD)
    try:
        copy_candidate_file(source, target)
        assert target.read_bytes() == source.read_bytes()
        target.chmod(stat.S_IREAD)
        copy_candidate_file(source, target)
        write_json(target, {"value": 2})
        copy_candidate_file(source, target)
        assert target.read_bytes() == b'{"value": 1}'
        assert source.read_bytes() == b'{"value": 1}'
    finally:
        source.chmod(stat.S_IREAD | stat.S_IWRITE)
        if target.exists():
            target.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_candidate_resume_preserves_final_asset_stamps(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from builder.analysis.alternatives import candidates
    from builder.analysis.alternatives.io import info, sha
    source = tmp_path / "study/snapshot/app-data"
    source.mkdir(parents=True)
    registry = tmp_path / "review/2026-09-15-regional/baseline/analysis/curves/curve_registry.parquet"
    registry.parent.mkdir(parents=True)
    pq.write_table(pa.table({"unused": pa.array([], pa.int64())}), registry)
    artifact, catalog = _assets()
    artifact["provenance"]["registry"]["sha256"] = sha(registry)
    write_json(source / "reference-curves.json", artifact)
    write_json(source / "screening-methods.json", catalog)
    monkeypatch.setattr(candidates, "ALTERNATIVES", [{"id": "alternative-4", "curve_count": 61}])
    candidates.build(tmp_path, tmp_path / "study")
    folder = tmp_path / "study/candidates/alternative-4/app-data"
    first = [info(path) for path in sorted(folder.glob("*.json"))]
    candidates.build(tmp_path, tmp_path / "study")
    assert first == [info(path) for path in sorted(folder.glob("*.json"))]


def test_sample_fixed_reproducible_weighted_and_order_independent():
    frame = pd.DataFrame({"comid": range(1, 104), "state": ["VA"] * 3 + ["OR"] * 100,
                          "l2": ["8.2"] * 103})
    left, strata = stratified_sample(frame, n=20)
    right, other = stratified_sample(frame.sample(frac=1, random_state=31), n=20)
    pd.testing.assert_frame_equal(left, right)
    assert strata == other
    assert left["sample"].sum() == 20
    assert left.loc[left["sample"], "sample_weight"].sum() == pytest.approx(103)
    assert all(row["sample_n"] > 0 for row in strata)
    assert left.comid.is_unique
    with pytest.raises(ValueError, match="unique"):
        stratified_sample(pd.concat([frame, frame.iloc[:1]]), n=20)


def test_candidate_changes_are_confined_and_source_is_unchanged():
    artifact, catalog = _assets()
    old = deepcopy((artifact, catalog))
    a1, c1 = candidate_assets(artifact, catalog, [], "alternative-1")
    assert (a1, c1) == old
    a4, c4 = candidate_assets(artifact, catalog, [], "alternative-4")
    removed = a4["sets"]["corridor-woody"]["curves"]
    assert "8.2" not in removed
    a4["sets"]["corridor-woody"]["curves"]["8.2"] = artifact["sets"]["corridor-woody"]["curves"]["8.2"]
    assert a4 == artifact and c4 == catalog
    a3, _ = candidate_assets(artifact, catalog, [], "alternative-3")
    for name in REGIONAL_SETS:
        assert a3["sets"][name]["curves"] == {"national": artifact["sets"][name]["curves"]["national"]}
    assert a3["sets"]["entrenchment"] == artifact["sets"]["entrenchment"]
    assert (artifact, catalog) == old


def test_nars9_requires_original_nine_complete_regions():
    artifact, catalog = _assets()
    with pytest.raises(ValueError, match="nine"):
        candidate_assets(artifact, catalog, [], "alternative-2")


def test_fingerprint_requires_all_dependencies_and_invalidates_changes(tmp_path):
    path = tmp_path / "input.json"
    write_json(path, {"v": 1})
    first, _ = fingerprint([path], {"protocol": 1})
    write_json(path, {"v": 2})
    second, _ = fingerprint([path], {"protocol": 1})
    assert first != second
    with pytest.raises(FileNotFoundError):
        fingerprint([tmp_path / "missing"])
    assert safe_study(tmp_path, tmp_path / "review/alternative-studies/a").name == "a"
    with pytest.raises(ValueError):
        safe_study(tmp_path, tmp_path / "analysis")


def test_flatten_uses_raw_rollup_and_actual_composite_result():
    report = {"ecosystemConditionIndexRaw": .617, "ecosystemConditionIndex": .62,
              "subIndicesRaw": {"physical": .5, "chemical": .6, "biological": .75},
              "metricRows": [{"functionId": "habitat-provision", "rating": "Fair", "functionScore": 8,
                              "index": .545, "scoring": {"methodKey": "composite", "curves": {"woody": {"stratum": "national", "fallbackDepth": 1}}}}]}
    row = flatten(report, 17)
    assert row["eci"] == .617
    assert row["score__habitat_provision"] == 8
    assert row["rating__habitat_provision"] == "Fair"
    assert row["route__habitat_provision"] == "composite"


def test_candidate_catalog_actual_engine_selection_and_boundaries(monkeypatch):
    from easi import screening_methods as sm
    points = [[0, 0], [1, .39], [2, .69], [3, 1]]
    curve = {"points": points, "x39": 1, "x69": 2, "n": 120}
    monkeypatch.setattr(sm, "curve_sets", lambda: {"test": {"stratifier": "nars9", "curves": {"NAP": curve, "national": curve}}})
    rule = {"curve": {"set": "test", "stratifier": "nars9", "mode": "banded", "fallback": ["national"]}}
    assert sm.score_quantity(1, rule, {"nars9": "NAP"})[:2] == ("Fair", .545)
    assert sm.score_quantity(2, rule, {"nars9": "NAP"})[:2] == ("Good", .85)
    assert sm.score_quantity(-1, rule, {"nars9": "NAP"})[:2] == ("Poor", .195)
    assert sm.score_quantity(None, rule) == (None, None, None)
    assert sm.score_quantity(2, rule, {})[2]["fallbackDepth"] == 1
    assert sm.score_quantity(2, rule, {"nars9": "NAP"})[2]["stratum"] == "NAP"


def test_recommendation_requires_both_targets_and_checks_regions():
    rows = [{"alternative_id": "alternative-3", "cohort": "latest_visit1", "region": "US", "function": "eci",
             "target": target, "statistic": "auc_good_vs_poor", "noninferiority_status": "noninferior",
             "support_floor_met": True, "alternative": .7, "ci_low": -.005, "ci_high": .01,
             "boot_requested": 1000, "boot_valid": 1000} for target in ("reference_2013", "t__bent_mmi")]
    heldout = [{**r, "cohort": "spatial_refit_latest_visit1"} for r in rows]
    assert recommendation(rows, heldout)["recommended"] == "alternative-3"
    assert recommendation(rows[:1], heldout)["recommended"] == "alternative-1"
    assert recommendation(rows, [])["recommended"] == "alternative-1"
    bad = {**rows[0], "region": "NAP", "function": "light_thermal_regime", "ci_low": -.04, "ci_high": -.02}
    assert recommendation([*rows, bad], heldout)["recommended"] == "alternative-1"
    assert recommendation(rows, heldout, availability_unchanged=False)["recommended"] == "alternative-1"
    unsupported = [{**r, "ci_low": None, "ci_high": None, "boot_valid": 0} for r in heldout]
    assert recommendation(rows, unsupported)["recommended"] == "alternative-1"
