"""Synthetic field cohorts only. No national inputs, scoring jobs or network."""
import csv
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder.analysis.alternatives import field_evaluation as field


def write_parquet(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def cached(tmp_path, monkeypatch):
    root, study = tmp_path / "data", tmp_path / "study"
    archive, raw = tmp_path / "archive", tmp_path / "raw"
    monkeypatch.setattr(field, "NRSA_DIR", archive)
    monkeypatch.setattr(field, "NRSA_RAW", raw)
    monkeypatch.setattr(field.nrsa, "RAW_FILES", {"2324": ("targets.csv",)})
    monkeypatch.setattr(field.nrsa, "SITE_FILE_1314", "reference.csv")
    visits = [{"station_key": f"s{i}", "site_id": f"site{i}", "cycle": "2324", "visit_no": "1",
               "unique_id": f"u{i}", "comid": i, "huc8": f"{i:08d}", "date_col": "2023-06-15",
               "protocol": "wadeable", "ag_eco9": "SAP", "us_l3code": "45"} for i in range(1, 5)]
    write_parquet(archive / "site_visits.parquet", visits)
    values = [{"station_key": f"s{i}", "site_id": f"site{i}", "cycle": "2324", "visit_no": "1",
               "phab_XWIDTH": i, "phab_XBKF_W": 4., "phab_XWD_RAT": 99., "phab_PCT_DR": 100. - i * 20,
               "phab_LRBS_use": float(i)} for i in range(1, 5)]
    write_parquet(archive / "values.parquet", values)
    targets = [{"SITE_ID": f"site{i}", "VISIT_NO": "1", "BENT_MMI_COND": "Good" if i > 2 else "Poor",
                "BEDSED_COND": "Good" if i > 2 else "Poor", "MMI_BENT": i * 10} for i in range(1, 5)]
    write_csv(raw / "targets.csv", targets)
    desktop = [{"station_key": f"s{i}", "comid": i, "huc8": f"{i:08d}", "desktop_source": "stored",
                "erom__q_cv_monthly": 5. - i, "erom__q_min_ratio": i / 10., "sc__bfiws": i * 10.,
                "sc__prg_bmmi0809": i / 5.} for i in range(1, 5)]
    write_parquet(root / "analysis/nrsa/nrsa_desktop.parquet", desktop)
    return root, study, archive, raw, visits, values, targets


def test_ledger_correct_ratio_provenance_and_repeatability(cached):
    root, study, archive, raw, visits, values, targets = cached
    write_csv(raw / "targets.csv", [*targets, targets[0]])
    first = field.build_observations(root, study)
    path = study / "cohorts/observations.parquet"
    before = path.read_bytes()
    rows = pq.read_table(path).to_pylist()
    ratio = next(r for r in rows if r["target"] == "wetted_bankfull_ratio" and r["comid"] == 1)
    assert ratio["target_value"] == .25
    assert ratio["date"] == "2023-06-15"
    assert ratio["visit_no"] == "1" and ratio["protocol"] == "wadeable"
    assert {s["column"] for s in json.loads(ratio["source_json"])} == {"phab_XWIDTH", "phab_XBKF_W"}
    assert all(len(s["sha256"]) == 64 for s in json.loads(ratio["source_json"]))
    target = next(r for r in rows if r["target"] == "t__bent_mmi" and r["comid"] == 1)
    assert target["identical_duplicates"] == 1
    assert sum(r["comid"] == 1 and r["target"] == "t__bent_mmi" for r in rows) == 1
    secondary = next(r for r in rows if r["target"] == "a__phab_XWD_RAT")
    assert secondary["role"] == "secondary_morphology"
    assert field.build_observations(root, study) == first
    assert path.read_bytes() == before


def test_conflicts_excluded_and_ratio_never_crosses_visit(cached):
    root, study, archive, raw, visits, values, targets = cached
    write_csv(raw / "targets.csv", [*targets, {**targets[0], "BENT_MMI_COND": "Good"}])
    visits += [{**visits[0], "visit_no": "2", "date_col": "2023-07-15"}]
    values[0]["phab_XBKF_W"] = None
    values += [{**values[0], "visit_no": "2", "phab_XWIDTH": None, "phab_XBKF_W": 4.}]
    write_parquet(archive / "site_visits.parquet", visits)
    write_parquet(archive / "values.parquet", values)
    field.build_observations(root, study)
    rows = pq.read_table(study / "cohorts/observations.parquet").to_pylist()
    conflicting = next(r for r in rows if r["comid"] == 1 and r["target"] == "t__bent_mmi")
    assert conflicting["status"] == "conflicting_target"
    assert conflicting["target_class"] is None
    ratio = [r for r in rows if r["comid"] == 1 and r["target"] == "wetted_bankfull_ratio"]
    assert len(ratio) == 2 and all(r["target_value"] is None for r in ratio)


def test_conflicting_metadata_not_arbitrarily_chosen(cached):
    root, study, archive, raw, visits, values, targets = cached
    write_parquet(archive / "site_visits.parquet", [*visits, {**visits[0], "comid": 88}])
    field.build_observations(root, study)
    rows = pq.read_table(study / "cohorts/observations.parquet").to_pylist()
    bad = [r for r in rows if r["site_id"] == "site1"]
    assert bad and all(r["status"] == "conflicting_visit" for r in bad)


def test_missing_huc_uses_filtered_strata(cached):
    root, study, archive, raw, visits, values, targets = cached
    visits[0]["huc8"] = None
    write_parquet(archive / "site_visits.parquet", visits)
    path = root / "analysis/nrsa/nrsa_desktop.parquet"
    desktop = pq.read_table(path).to_pylist()
    desktop[0]["huc8"] = None
    write_parquet(path, desktop)
    write_parquet(root / "analysis/strata.parquet", [{"comid": 1, "huc8": "03010101"}, {"comid": 99, "huc8": "01020304"}])
    summary = field.build_observations(root, study)
    rows = pq.read_table(study / "cohorts/observations.parquet").to_pylist()
    assert all(r["huc8"] == "03010101" for r in rows if r["comid"] == 1)
    source = next(i for i in summary["inputs"] if i["path"].endswith("strata.parquet"))
    assert source["selected_rows"] == 1 and source["columns"] == ["comid", "huc8"]
    assert source["sha256"] is None and len(source["selected_sha256"]) == 64


def test_folds_link_shared_stations_comids_and_watersheds():
    rows = [{"station_key": "a", "comid": 1, "huc8": "00000001"},
            {"station_key": "a", "comid": 2, "huc8": "00000002"},
            {"station_key": "b", "comid": 2, "huc8": "00000003"},
            {"station_key": "c", "comid": 3, "huc8": "00000003"},
            {"station_key": "d", "comid": 4, "huc8": None}]
    grouped = field.group_folds(rows)
    assert len({r["fold_group"] for r in grouped[:4]}) == 1
    assert len({r["fold"] for r in grouped[:4]}) == 1
    assert grouped[0]["bootstrap_huc8"] == "00000001"
    assert grouped[4]["fold"] is None
    assert list(reversed(field.group_folds(list(reversed(rows))))) == grouped
    conflicting = {"station_key": "a", "comid": 99, "huc8": "99999999", "status": "conflicting_visit"}
    with_conflict = field.group_folds([*rows, conflicting])
    assert with_conflict[:len(rows)] == grouped
    assert with_conflict[-1]["fold"] is None


def test_paired_bootstrap_resamples_whole_watersheds_and_filters_jointly():
    baseline = np.tile([.1, .4, .6, .9], 10)
    candidate = baseline.copy()
    target = baseline > .5
    huc = np.repeat(["00000001", "00000002", "00000003", "00000004"], 10)
    candidate[0] = np.nan
    result = field.paired_statistics(baseline, candidate, target, huc, boot=100)
    assert result["n"] == 39 and result["n_huc8"] == 4
    assert result["delta"] == result["ci_low"] == result["ci_high"] == 0
    assert result["boot_valid"] == 100 and result["noninferiority_status"] == "noninferior"
    changed = field.paired_statistics(baseline, -baseline, target, huc, boot=100)
    assert changed["delta"] == changed["ci_low"] == changed["ci_high"] == -1
    assert changed["noninferiority_status"] == "inferior"
    assert result == field.paired_statistics(baseline, candidate, target, huc, boot=100)


def test_paired_mask_and_degenerate_support_are_explicit():
    result = field.paired_statistics([1, 2, 3], [1, 2, 3], np.array([False, True, True]),
                                     ["00000001", "00000001", ""], boot=100)
    assert result["n"] == 2 and result["n_huc8"] == 1
    assert result["boot_valid"] == 0 and result["ci_low"] is None
    assert result["noninferiority_status"] == "inconclusive"
    with pytest.raises(ValueError, match="boolean"):
        field.paired_statistics([1, 2], [1, 2], [None, "Good"], ["00000001", "00000002"])
    with pytest.raises(ValueError, match="nonnegative"):
        field.paired_statistics([], [], np.array([], bool), [], boot=-1)


def test_bootstrap_is_whole_cluster_paired_not_independent_arms():
    reference = np.array([.2, .8, .3, .5, .1, .7])
    alternative = np.array([.4, .6, .6, .7, .2, .8])
    target = np.array([False, True, False, True, False, True])
    hucs = ["00000001", "00000001", "00000002", "00000002", "00000003", "00000003"]
    result = field.paired_statistics(reference, alternative, target, hucs, boot=80, seed=123)
    rng = np.random.default_rng(123)
    differences = []
    members = [np.array([0, 1]), np.array([2, 3]), np.array([4, 5])]
    for _ in range(80):
        ids = np.concatenate([members[i] for i in rng.integers(0, 3, size=3)])
        differences.append(field.stats.auc(alternative[ids], target[ids]) - field.stats.auc(reference[ids], target[ids]))
    assert (result["ci_low"], result["ci_high"]) == field.stats.interval(differences)
    assert result["boot_valid"] == 80


def test_observed_target_reuse_excluded_and_routes_preserved():
    rows = [{"comid": i, "huc8": f"{i:08d}", "target_class": "Good" if i % 2 else "Poor"} for i in range(1, 5)]
    base = {i: {"index__" + field.BIO: i / 5., "route__" + field.BIO: "published-bmmi",
                "rating__" + field.BIO: "Good", "observed__" + field.BIO: i == 1} for i in range(1, 5)}
    alternative = {i: dict(r) for i, r in base.items()}
    alternative[2]["observed__" + field.BIO] = True
    result = field._compare(rows, base, alternative, "index__" + field.BIO, "auc_good", boot=10, positive={"Good"})
    assert result["n"] == 2 and result["observed_override_excluded_n"] == 2
    assert json.loads(result["reference_routes"]) == {"published-bmmi": 2}


def test_duplicate_station_target_conflict_is_excluded_across_site_ids():
    common = {"station_key": "same", "cycle": "2324", "visit_no": "1", "target": "t__bent_mmi",
              "comid": 1, "huc8": "00000001", "date": "2023-07-01", "protocol": "wadeable",
              "target_value": None, "status": "eligible", "source_json": "[]", "identical_duplicates": 0}
    rows = [{**common, "site_id": "site-a", "observation_id": "a", "target_class": "Good"},
            {**common, "site_id": "site-b", "observation_id": "b", "target_class": "Poor"}]
    result = field._deduplicate_observations(rows)
    assert len(result) == 2 and all(r["status"] == "conflicting_station_visit_target" for r in result)


def test_contribution_removal_calls_current_mapping_without_modifying_scores():
    from easi import config, scoring
    scores = {"score__" + key.replace("-", "_"): 13 if i % 2 else 3 for i, key in enumerate(config.cwa_mapping())}
    before = dict(scores)
    result = field.contribution_removal(scores, field.BED)
    mapping = {k: v for k, v in config.cwa_mapping().items() if k != field.BED.replace("_", "-")}
    expected = scoring.rollup({k[7:].replace("_", "-"): value for k, value in scores.items()}, mapping=mapping)
    assert result["eci"] == expected.ecosystem_condition_index
    assert scores == before
    with pytest.raises(ValueError, match="integer"):
        field.contribution_removal({"score__" + field.BED: .5}, field.BED)


def test_latest_and_retrospective_target_cohorts_do_not_mix_values():
    rows = [{"station_key": "s", "site_id": "a", "target": target, "status": "eligible",
             "visit_no": "1", "cycle": cycle, "observation_id": cycle + target, "target_value": value}
            for target, cycle, value in [("x", "1314", 1), ("x", "2324", 2), ("y", "1314", 3)]]
    cohorts = dict(field._cohorts(rows))
    assert {(r["target"], r["cycle"]) for r in cohorts["latest_visit1"]} == {("x", "2324"), ("y", "1314")}
    assert [(r["target"], r["target_value"]) for r in cohorts["retrospective_2324"]] == [("x", 2)]


def test_evaluate_small_study_outputs_no_new_bands_and_preserves_inputs(cached):
    root, study, archive, raw, visits, values, targets = cached
    field.build_observations(root, study)
    scores = []
    for i in range(1, 5):
        rating = "Good" if i > 2 else "Poor"
        row = {"comid": i, "eci": i / 5., "physical": i / 5., "chemical": i / 5., "biological": i / 5.}
        for function in (field.BIO, field.LOW, field.BED, field.CATCHMENT):
            row.update({"index__" + function: .85 if i > 2 else .195,
                        "rating__" + function: rating, "score__" + function: 13 if i > 2 else 3,
                        "route__" + function: "predicted-bmmi" if function == field.BIO else "stored"})
        scores.append(row)
    for alternative in field.ALTERNATIVES:
        write_parquet(study / "scores" / (alternative + ".parquet"), scores)
    before = {p: p.read_bytes() for p in study.glob("scores/*")}
    summary = field.evaluate(root, study, boot=4)
    assert summary["training_independence"] == "unknown"
    assert summary["bootstrap_unit"] == "HUC8" and summary["boot_requested"] == 4
    assert all(p.read_bytes() == data for p, data in before.items())
    with (study / "results/field_lowflow.csv").open() as handle:
        low = list(csv.DictReader(handle))
    assert low and {r["candidate_bands"] for r in low} == {"none"}
    assert {r["target"] for r in low} == {"a__phab_PCT_DR", "wetted_bankfull_ratio", "a__phab_XWD_RAT"}
    with (study / "results/field_agreement.csv").open() as handle:
        agreement = list(csv.DictReader(handle))
    assert agreement and all(r["delta"] in ("", "0.0") for r in agreement)
    assert any(r["cohort"] == "retrospective_2324" for r in agreement)
    assert any(r["region"] == "SAP" for r in agreement)
    assert field.evaluate(root, study, boot=4) == summary
    original = (study / "results/field_agreement.csv").read_bytes()
    spatial = study / "spatial"
    for alternative in field.ALTERNATIVES:
        write_parquet(spatial / "scores" / (alternative + ".parquet"), scores)
    heldout = field.evaluate(root, study, boot=4, scores_dir=spatial / "scores", results_dir=spatial / "results",
                            cohort_prefix="spatial_refit:")
    assert heldout["cohort_prefix"] == "spatial_refit:"
    assert (study / "results/field_agreement.csv").read_bytes() == original
    with (spatial / "results/field_agreement.csv").open() as handle:
        assert all(r["cohort"].startswith("spatial_refit:") for r in csv.DictReader(handle))
    with pytest.raises(ValueError, match="must not overwrite"):
        field.evaluate(root, study, cohort_prefix="spatial_refit:")
    with pytest.raises(ValueError, match="inside"):
        field.evaluate(root, study, results_dir=root / "analysis/results")


def test_duplicate_score_comid_fails_closed(tmp_path):
    path = tmp_path / "scores.parquet"
    write_parquet(path, [{"comid": 1}, {"comid": 1}])
    with pytest.raises(ValueError, match="duplicate"):
        field._unique_scores(path)


def test_score_read_filters_field_comids_and_excludes_curve_payload(tmp_path):
    path = tmp_path / "scores.parquet"
    write_parquet(path, [{"comid": i, "eci": i / 5., "index__population_support": .85,
                          "observed__population_support": False, "curves__population_support": "large trace"}
                         for i in range(1, 5)])
    result = field._unique_scores(path, {2, 4})
    assert set(result) == {2, 4}
    assert result[2]["index__population_support"] == .85
    assert "observed__population_support" in result[2]
    assert "curves__population_support" not in result[2]
    assert field._unique_scores(path, set()) == {}


def test_atomic_csv_keeps_previous_output_when_interrupted(tmp_path, monkeypatch):
    path = tmp_path / "result.csv"
    path.write_text("previous complete result\n")

    def interrupted(self, rows):
        raise OSError("interrupted write")

    monkeypatch.setattr(csv.DictWriter, "writerows", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        field._csv(path, [{"value": 1}])
    assert path.read_text() == "previous complete result\n"
