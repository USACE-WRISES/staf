"""Study runner 1.2.0 (WP-R4s): a study takes built candidate packages, scores each arm through
its method package, keeps the knobs' outputs, adds the addendum's targets, refits a candidate's
added set per fold with its split, measures P4 for a refitted family, writes P1 to P6 and the
decision by the frozen margins, and exports the case set for the EA1 replay.

Synthetic panels and a handful of library cases only: no study, no national scoring."""
from __future__ import annotations

from copy import deepcopy
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import REPO_ROOT
from builder.analysis import artifact, curves
from builder.analysis import alternatives as alts
from builder.analysis.alternatives import (bases, candidates, cases, field_evaluation, outcomes, report,
                                           round4 as r4, round4_finalist, scorer, set_stability, spatial, study,
                                           woody_stability)
from builder.analysis.alternatives.io import info, read_json, sha, write_json, write_parquet

ADOPTED = "alternative-2-b2e3033116e3"
DATA = REPO_ROOT / "apps/easi/data"
LIBRARY_V1 = REPO_ROOT / "apps/library/assessments/easi-screening/v1"
LOW = "low_flow_baseflow_dynamics"


def _study_paths(tmp_path, name="2026-09-27-low-flow-intermittence-alternatives"):
    root = tmp_path / "root"
    study_dir = root / "review/alternative-studies" / name
    (study_dir / "snapshot/app-data").mkdir(parents=True)
    for path in DATA.iterdir():
        if path.is_file():
            shutil.copyfile(path, study_dir / "snapshot/app-data" / path.name)
    return root, study_dir


def _base_zip(tmp_path) -> tuple[Path, str]:
    from easi import method_package as mp
    pkg = mp.package_from_dir(DATA, version=1, label="base")
    path = tmp_path / "base.easi-method.zip"
    path.write_bytes(mp.to_zip(pkg))
    return path, pkg.digest


def _candidate_folder(tmp_path, family, curve_sets=None) -> Path:
    """A built candidate package folder as build_easi_candidate_package.py writes it, from the
    library's base files through StreamCurves' Round 4 builder (the vendored evaluator is the
    same bytes as apps/easi, so the digests agree)."""
    from streamcurves._vendor.easi import method_package as vmp
    from streamcurves.easi_method import round4 as sc
    files = sc.base_files(LIBRARY_V1)
    recorded = sc.recorded_base_identity(LIBRARY_V1)
    built = sc.build_candidate(files, family, curve_sets=curve_sets)
    pkg = sc.package(built["files"], family, built["spec"])
    blob = vmp.to_zip(pkg)
    folder = tmp_path / "candidates" / family
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{family}.easi-method.zip").write_bytes(blob)
    record = {"schema": "staf-easi-candidate", "family": family, "familyName": built["spec"].get("family"),
              "base": {"packageDigest": vmp.package_digest({n: vmp._sha(b) for n, b in files.items()}),
                       "methodVersion": recorded.get("methodVersion"), "recorded": recorded},
              "candidate": {**built["identity"], "zip": f"{family}.easi-method.zip"},
              "edits": built["edits"], "respecified": built.get("respecified"), "label": "rehearsal"}
    (folder / "candidate.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return folder


def _records(n=3):
    path = LIBRARY_V1 / "cases.json"
    if not path.is_file():
        pytest.skip("the library's EASI preview cases are not in this copy")
    doc = json.loads(path.read_text(encoding="utf-8"))
    # the preview cases vary one base reach, so they share its COMID; a study keys evidence by
    # COMID, so each record here gets its own (the identity is not a scoring input)
    return [{**c["record"], "comid": 7000001 + i} for i, c in enumerate(doc["cases"][:n])]


def _evidence(study_dir, records):
    from easi.national import records as rec
    rows = [rec.to_row(dict(r)) for r in records]
    write_parquet(study_dir / "evidence/stored/part.parquet", pa.Table.from_pylist(rows))
    return rows


def _expected_rows(rows):
    from easi.national import client, records as rec
    out = [scorer.flatten(client.score_record(rec.from_row(dict(r)), cross_section=False), int(r["comid"])) for r in rows]
    return sorted(out, key=lambda r: r["comid"])


def _normal(rows):
    return json.loads(json.dumps(report.clean(rows), sort_keys=True))


# --------------------------------------------------------------------------- #
# the manifest's candidates block and the candidates step
# --------------------------------------------------------------------------- #
def test_study_candidates_reads_the_block_and_defaults_to_the_alternatives():
    assert alts.study_candidates({}) == alts.ALTERNATIVES and alts.study_candidates(None) == alts.ALTERNATIVES
    assert alts.study_candidates({"alternatives": alts.ALTERNATIVES}) == alts.ALTERNATIVES
    block = [{"id": "alternative-1", "role": "base"}, {"id": "E4", "role": "candidate"}]
    assert [row["id"] for row in alts.study_candidates({"candidates": block})] == ["alternative-1", "E4"]
    assert alts.study_candidates({"candidates": block})[0] is not block[0]
    assert alts.is_round4({"candidates": block}) and not alts.is_round4({}) and not alts.is_round4({"candidates": []})
    assert alts.study_candidates({}, [{"id": "x"}]) == [{"id": "x"}]
    assert alts.STUDY_VERSION == "1.2.0"


def test_the_addendum_helpers_agree_with_the_frozen_yaml_and_the_builder():
    from streamcurves.easi_method import round4 as sc
    assert r4.ADDENDUM_SHA256 == sc.ADDENDUM_SHA256 == r4.addendum_sha256()
    assert all(r4.margins_in_addendum().values()), r4.margins_in_addendum()
    assert r4.deciding_targets(r4.family_spec("E3")) == ("T1", ["T1"])
    assert r4.deciding_targets(r4.family_spec("E2")) == ("T1", ["T1", "T2"])
    assert r4.comparison_scope(r4.family_spec("E2")) == {"function": LOW, "rule": "candidate-rated",
                                                        "reason": "Addendum 1 (2026-09-27)"}
    assert r4.comparison_scope(r4.family_spec("E1")) is None
    assert r4.function_ids(r4.family_spec("E8")) is None
    assert r4.function_ids(r4.family_spec("E6")) == ["sediment_continuity", "bed_composition_bedform_dynamics"]
    # the coordinator's answers of 2026-09-27: the development cohort decides, the width to
    # depth ratio and the T3 targets never block
    assert r4.DECIDING_COHORT == "development_1314_1819" and r4.RETROSPECTIVE_COHORT == "retrospective_2324"
    assert r4.REPORTED_COHORTS == ("latest_visit1", "retrospective_2324")
    assert set(r4.NEVER_BLOCK_TARGETS) == {"t__fish_mmi", "t__oe", "a__phab_BFWD_RAT"}
    with pytest.raises(r4.AddendumError, match="unknown family"):
        r4.family_spec("E9")
    assert study.parse_candidates(["E1=a", "E8=b"]) == {"E1": "a", "E8": "b"}
    with pytest.raises(RuntimeError, match="given twice"):
        study.parse_candidates(["E1=a", "E1=b"])
    with pytest.raises(RuntimeError, match="names E1"):
        study.parse_candidates(["E9=a"])
    protocol = study.protocol()
    assert protocol["version"] == "1.2.0" and protocol["addendum"]["sha256"] == r4.ADDENDUM_SHA256
    assert protocol["paired_bootstrap_seed"] == 20260915 and protocol["reference_bootstrap_seed"] == 7
    assert protocol["deciding_cohort"] == "development_1314_1819"
    assert protocol["reported_cohorts"] == ["latest_visit1", "retrospective_2324"]
    assert "reads it once" in protocol["retrospective_rule"] and "--base-scores" in protocol["base_scores"]


def test_the_candidates_block_refuses_the_wrong_base_evaluator_and_family(tmp_path):
    from easi import method_package as mp
    base = bases.base(ADOPTED)
    running = mp.evaluator_digest()
    folder = _candidate_folder(tmp_path, "E1")
    found = r4.read_candidate_source(folder)
    assert found["family"] == "E1" and found["curve_count"] == 34 and found["evaluator_digest"] == running
    assert set(found["files"]) == set(mp.METHOD_FILES) and found["requires"]["behaviors"][-1] == "applicability-rules"
    block = study.candidate_block(base, {"E1": str(folder)}, running)
    assert [row["id"] for row in block] == ["alternative-1", "E1"]
    assert block[0]["label"] == base.label and block[0]["package_digest"] == base.package_digest
    entry = block[1]
    assert entry["package_digest"] == found["package_digest"] and entry["family"] == "low_flow_intermittence"
    assert entry["decision"] == "simplification" and entry["curve_count"] == 34 and entry["respecified"] is None
    assert entry["base"]["packageDigest"] == base.package_digest and entry["curve_set_findings"] == {}
    # a refit family's own findings ride into the block (E2: XER's degenerate perennial pool)
    e2_set = {"flow-min-ratio": {"higherIsBetter": True, "quantity": "q_min_ratio", "stratifier": "nars9",
                                 "split": {"fcode_class": "perennial"},
                                 "curves": {key: {"points": [[0.0, 0.0], [0.2, 0.39], [0.5, 0.69], [1.0, 1.0]], "n": 40,
                                                  "nMembers": 40, "q25": .2, "q50": .35, "q75": .5, "x39": .2, "x69": .5,
                                                  "status": "complete", "panelTier": "complete", "screen": "strict"}
                                            for key in ("national", "TPL")}}}
    e2 = _candidate_folder(tmp_path, "E2", curve_sets=e2_set)
    record = json.loads((e2 / "candidate.json").read_text(encoding="utf-8"))
    record["curveSets"] = {"provenance": {"diagnostics": {"flow-min-ratio": {
        "notUsable": {"XER": "engine status degenerate_q25"}, "split": {"fcode_class": "perennial"}}}}}
    (e2 / "candidate.json").write_text(json.dumps(record), encoding="utf-8")
    found_e2 = r4.read_candidate_source(e2)
    assert found_e2["curve_set_findings"] == {"flow-min-ratio": {"not_usable": {"XER": "engine status degenerate_q25"},
                                                                 "split": {"fcode_class": "perennial"},
                                                                 "served_by": "the national fallback"}}
    assert found_e2["respecified"] == "2026-09-27" and found_e2["curve_sets"]["flow-min-ratio"]["split"] == {"fcode_class": "perennial"}
    e2_entry = study.candidate_block(base, {"E2": str(e2)}, running)[1]
    assert e2_entry["curve_set_findings"]["flow-min-ratio"]["not_usable"] == {"XER": "engine status degenerate_q25"}
    assert e2_entry["comparison_scope"]["function"] == LOW
    # the same zip, as a bare file: the record beside it is read
    assert r4.read_candidate_source(folder / "E1.easi-method.zip")["family"] == "E1"
    # refusals: another base, another evaluator, the wrong family, a base without a package
    wrong_base = deepcopy(entry)
    wrong_base["base"] = {**entry["base"], "packageDigest": "sha256:" + "0" * 64}
    with pytest.raises(study.CandidateRefused, match="not the study's base"):
        study.check_candidate(wrong_base, base, running)
    wrong_version = deepcopy(entry)
    wrong_version["base"] = {**entry["base"], "methodVersion": "000000000000"}
    with pytest.raises(study.CandidateRefused, match="base method version"):
        study.check_candidate(wrong_version, base, running)
    with pytest.raises(study.CandidateRefused, match="not the running evaluator"):
        study.check_candidate({**entry, "evaluator_digest": "sha256:" + "f" * 64}, base, running)
    with pytest.raises(study.CandidateRefused, match="no method package"):
        study.check_candidate(entry, bases.base(), running)
    mislabeled = tmp_path / "candidates" / "E3"
    shutil.copytree(folder, mislabeled)
    with pytest.raises(study.CandidateRefused, match="holds candidate E1"):
        study.candidate_block(base, {"E3": str(mislabeled)}, running)
    with pytest.raises(ValueError, match="candidate.json names package"):
        record = json.loads((mislabeled / "candidate.json").read_text(encoding="utf-8"))
        record["candidate"]["packageDigest"] = "sha256:" + "1" * 64
        (mislabeled / "candidate.json").write_text(json.dumps(record), encoding="utf-8")
        r4.read_candidate_source(mislabeled)


def test_the_candidates_step_assembles_app_data_and_writes_the_base_zip(tmp_path):
    from easi import method_package as mp
    base = bases.base(ADOPTED)
    root, study_dir = _study_paths(tmp_path)
    folder = _candidate_folder(tmp_path, "E1")
    e8 = _candidate_folder(tmp_path, "E8")
    block = study.candidate_block(base, {"E1": str(folder), "E8": str(e8)}, mp.evaluator_digest())
    manifest = {"base_id": ADOPTED, "alternative_1": {"method_version": base.method_version, "source_commit": base.commit,
                                                    "frozen_sha": base.reference_sha256},
                "protocol": {"version": "1.2.0"}, "candidates": block}
    write_json(study_dir / "manifest.json", manifest)
    result = candidates.build(root, study_dir)
    assert [row["id"] for row in result["candidates"]] == ["alternative-1", "E1", "E8"]
    written = read_json(study_dir / "candidates/manifest.json")
    assert written["scoring_route"].startswith("EASI_METHOD_PACKAGE") and len(written["sources"]) == 2
    # the base: the snapshot's app-data unchanged and its own zip with the base's package digest
    base_zip = study_dir / "candidates/alternative-1/alternative-1.easi-method.zip"
    pkg = mp.read_package(base_zip)
    assert pkg.digest == base.package_digest == written["candidates"][0]["package_digest"]
    assert written["candidates"][0]["published_zip_sha256"].startswith("3ae27405")
    for name in mp.METHOD_FILES:
        assert (study_dir / "candidates/alternative-1/app-data" / name).read_bytes() == (DATA / name).read_bytes()
    # a candidate: the eight method files are the package's, every other file the snapshot's
    e1 = mp.read_package(folder / "E1.easi-method.zip")
    for name in mp.METHOD_FILES:
        assert (study_dir / "candidates/E1/app-data" / name).read_bytes() == e1.files[name]
    for path in (study_dir / "snapshot/app-data").iterdir():
        if path.is_file() and path.name not in mp.METHOD_FILES:
            assert (study_dir / "candidates/E1/app-data" / path.name).read_bytes() == path.read_bytes()
    assert mp.read_package(study_dir / "candidates/E1/E1.easi-method.zip").digest == e1.digest
    assert written["candidates"][1]["curve_count"] == 34 and written["candidates"][1]["family"] == "low_flow_intermittence"
    # a resumed step leaves identical files with their stamps
    before = {str(p): info(p) for p in sorted((study_dir / "candidates").rglob("*")) if p.is_file()}
    candidates.build(root, study_dir)
    assert before == {str(p): info(p) for p in sorted((study_dir / "candidates").rglob("*")) if p.is_file()}
    # a candidate zip changed since the snapshot recorded it is refused
    (folder / "E1.easi-method.zip").write_bytes((e8 / "E8.easi-method.zip").read_bytes())
    with pytest.raises(study.CandidateRefused, match="changed since the snapshot"):
        candidates.build(root, study_dir)


# --------------------------------------------------------------------------- #
# flatten and the package route
# --------------------------------------------------------------------------- #
def test_flatten_keeps_the_old_columns_and_adds_completeness_gap_and_rollup_fields():
    rated = {"functionId": "habitat-provision", "rating": "Fair", "functionScore": 8, "index": .545,
             "completeness": "complete", "scoring": {"methodKey": "composite", "curves": {}}}
    withheld = {"functionId": "low-flow-baseflow-dynamics", "rating": None, "functionScore": None, "index": None,
                "completeness": "withheld", "scoring": {"methodKey": "erom-flow-variability", "completeness": "withheld",
                                                        "statement": "Naturally intermittent (FCODE 46003)",
                                                        "applicability": {"rule": {"input": "fcodeContext", "exclude": ["46003"]},
                                                                          "withheld": True,
                                                                          "matched": {"input": "fcodeContext", "value": 46003}}}}
    missing = {"functionId": "nutrient-cycling", "rating": None, "functionScore": None, "index": None,
               "completeness": "not_assessed", "scoring": {"methodKey": "wqp"}}
    report_doc = {"ecosystemConditionIndexRaw": .5, "subIndicesRaw": {"physical": .5, "chemical": .6, "biological": .4},
                  "metricRows": [rated, withheld, missing]}
    row = scorer.flatten(report_doc, 7)
    assert row["rating__habitat_provision"] == "Fair" and row["route__habitat_provision"] == "composite"
    assert row["completeness__habitat_provision"] == "rated" and row["gap__habitat_provision"] is None
    assert row["completeness__low_flow_baseflow_dynamics"] == "withheld"
    gap = json.loads(row["gap__low_flow_baseflow_dynamics"])
    assert gap == {"matched": {"input": "fcodeContext", "value": 46003}, "rule": {"exclude": ["46003"], "input": "fcodeContext"},
                   "statement": "Naturally intermittent (FCODE 46003)"}
    assert row["completeness__nutrient_cycling"] == "missing" and row["gap__nutrient_cycling"] is None
    assert row["functions_rated"] is None and row["eci_interval_lower"] is None and row["eci_interval_upper"] is None
    e8 = scorer.flatten({**report_doc, "functionsRated": 1, "ecosystemConditionIndexInterval": [.3, .8]}, 7)
    assert (e8["functions_rated"], e8["eci_interval_lower"], e8["eci_interval_upper"]) == (1, .3, .8)
    # the old columns keep their names and values
    legacy = {k: v for k, v in row.items() if not k.startswith(("completeness__", "gap__")) and
              k not in ("functions_rated", "eci_interval_lower", "eci_interval_upper")}
    assert set(legacy) == {"comid", "eci", "physical", "chemical", "biological"} | {
        f"{p}__{f}" for p in ("rating", "score", "index", "route", "fallback", "curves", "observed")
        for f in ("habitat_provision", "low_flow_baseflow_dynamics", "nutrient_cycling")}


def test_asset_fallbacks_repoint_a_missing_or_changed_asset_to_the_snapshot_copy(tmp_path, monkeypatch):
    from easi import bieger, geo
    from easi import method_package as mp
    from easi.datasources import nrsa
    served, preserved = tmp_path / "served", tmp_path / "app-data"
    served.mkdir(), preserved.mkdir()
    for name in mp.EVALUATOR_ASSETS:
        (preserved / name).write_bytes(b"snapshot " + name.encode())
        if name != "ecoregions_l3.geojson":
            (served / name).write_bytes(b"snapshot " + name.encode())
    (served / "physio_divisions.geojson").write_bytes(b"other bytes")
    monkeypatch.setattr(mp, "_reset_all_caches", lambda: None)
    before = (geo.ECOREGIONS_PATH, bieger._GEOJSON, nrsa.DATA_PATH)
    try:
        out = scorer.asset_fallbacks(served, preserved)
        assert {o["file"]: o["reason"] for o in out} == {
            "ecoregions_l3.geojson": "missing from the materialized package folder",
            "physio_divisions.geojson": "the materialized copy is not the snapshot's bytes"}
        assert geo.ECOREGIONS_PATH == preserved / "ecoregions_l3.geojson"
        assert bieger._GEOJSON == preserved / "physio_divisions.geojson"
        assert nrsa.DATA_PATH == before[2]
        assert scorer.asset_fallbacks(served, tmp_path / "empty") == []
    finally:
        geo.ECOREGIONS_PATH, bieger._GEOJSON, nrsa.DATA_PATH = before


def test_the_worker_scores_through_the_package_route_and_records_it(tmp_path):
    """A child scores three library records with EASI_METHOD_PACKAGE (EASI_DATA_DIR unset),
    asserts the active package digest, records the route, and gives the rows the in-process
    evaluator and the data-dir route give."""
    root, study_dir = _study_paths(tmp_path)
    rows = _evidence(study_dir, _records(3))
    expected = _expected_rows(rows)
    zip_path, digest = _base_zip(tmp_path)
    data_dir = study_dir / "candidates/alternative-1/app-data"
    shutil.copytree(study_dir / "snapshot/app-data", data_dir)
    destination = study_dir / "scores/alternative-1.parquet"
    scorer.launch(study_dir, data_dir, destination, package=zip_path, expected_digest=digest)
    got = sorted(pq.read_table(destination).to_pylist(), key=lambda r: r["comid"])
    assert _normal(got) == _normal(expected)
    record = read_json(destination.with_suffix(".json"))
    assert record["route"] == "package" and record["package_digest"] == digest
    assert record["asset_fallbacks"] == [] and record["same_evaluator"] is True
    assert record["rows"] == 3 and record["seconds"] > 0 and record["data_directory"] != str(data_dir)
    assert Path(record["data_directory"]).is_relative_to(study_dir / "method-cache")
    assert (study_dir / "traces/alternative-1.json").is_file()
    assert {row[f"completeness__{LOW}"] for row in got} <= {"rated", "withheld", "missing"}
    # the data-dir route gives the same rows
    other = study_dir / "scores/data-dir.parquet"
    scorer.launch(study_dir, data_dir, other)
    assert _normal(sorted(pq.read_table(other).to_pylist(), key=lambda r: r["comid"])) == _normal(expected)
    assert read_json(other.with_suffix(".json"))["route"] == "data-dir"
    # the wrong expected digest is refused before a row is scored
    with pytest.raises(Exception):
        scorer.launch(study_dir, data_dir, study_dir / "scores/wrong.parquet", package=zip_path,
                      expected_digest="sha256:" + "0" * 64)
    assert not (study_dir / "scores/wrong.parquet").exists()


# --------------------------------------------------------------------------- #
# the targets, the scope and the paired statistics
# --------------------------------------------------------------------------- #
def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    root, study_dir = tmp_path / "data", tmp_path / "study"
    archive, raw = tmp_path / "archive", tmp_path / "raw"
    monkeypatch.setattr(field_evaluation, "NRSA_DIR", archive)
    monkeypatch.setattr(field_evaluation, "NRSA_RAW", raw)
    monkeypatch.setattr(field_evaluation.nrsa, "RAW_FILES", {"2324": ("targets.csv", "fish.csv")})
    monkeypatch.setattr(field_evaluation.nrsa, "SITE_FILE_1314", "reference.csv")
    visits = [{"station_key": f"s{i}", "site_id": f"site{i}", "cycle": "2324", "visit_no": "1", "unique_id": f"u{i}",
               "comid": i, "huc8": f"{i:08d}", "date_col": "2023-06-15", "protocol": "wadeable", "ag_eco9": "SAP",
               "us_l3code": "45"} for i in range(1, 5)]
    write_parquet(archive / "site_visits.parquet", pa.Table.from_pylist(visits))
    values = [{"station_key": f"s{i}", "site_id": f"site{i}", "cycle": "2324", "visit_no": "1",
               "phab_XWIDTH": float(i), "phab_XBKF_W": 4., "phab_XWD_RAT": 99., "phab_PCT_DR": 100. - i * 20,
               "phab_LRBS_use": float(i), "phab_XFC_NAT": i / 10., "phab_PCT_FAST": 10. * i, "phab_XBKF_H": .5 + i / 10.,
               "phab_BFWD_RAT": 8. + i, "phab_XINC_H": 1.} for i in range(1, 5)]
    write_parquet(archive / "values.parquet", pa.Table.from_pylist(values))
    _write_csv(raw / "targets.csv", [{"SITE_ID": f"site{i}", "VISIT_NO": "1", "BENT_MMI_COND": "Good" if i > 2 else "Poor",
                                      "OE_COND": "O/E>=0.9" if i > 2 else "O/E<0.8", "MMI_BENT": i * 10} for i in range(1, 5)])
    _write_csv(raw / "fish.csv", [{"SITE_ID": f"site{i}", "VISIT_NO": "1", "FISH_MMI_COND": "Good" if i % 2 else "Poor",
                                   "MMI_FISH": i * 5} for i in range(1, 5)])
    desktop = [{"station_key": f"s{i}", "comid": i, "huc8": f"{i:08d}", "desktop_source": "stored",
                "erom__q_cv_monthly": 5. - i, "erom__q_min_ratio": i / 10., "sc__bfiws": i * 10., "sc__prg_bmmi0809": i / 5.}
               for i in range(1, 5)]
    write_parquet(root / "analysis/nrsa/nrsa_desktop.parquet", pa.Table.from_pylist(desktop))
    return root, study_dir


def test_the_ledger_carries_the_addendum_targets_with_their_roles(ledger):
    root, study_dir = ledger
    summary = field_evaluation.build_observations(root, study_dir)
    rows = pq.read_table(study_dir / "cohorts/observations.parquet").to_pylist()
    by_target = {}
    for row in rows:
        by_target.setdefault(row["target"], []).append(row)
    for target in ("a__phab_XFC_NAT", "a__phab_PCT_FAST", "a__phab_XBKF_H", "a__phab_BFWD_RAT", "wetted_bankfull_ratio"):
        assert target in by_target, target
    fast = next(r for r in by_target["a__phab_PCT_FAST"] if r["comid"] == 2)
    assert fast["target_value"] == 20. and fast["role"] == "field_target" and fast["status"] == "eligible"
    assert {s["column"] for s in json.loads(fast["source_json"])} == {"phab_PCT_FAST"}
    height = next(r for r in by_target["a__phab_XBKF_H"] if r["comid"] == 2)
    ratio = next(r for r in by_target["a__phab_BFWD_RAT"] if r["comid"] == 2)
    assert height["sign"] == -1 and height["role"] == "field_target"
    assert ratio["sign"] == 1 and ratio["role"] == "exploratory_field"
    fish = next(r for r in by_target["t__fish_mmi"] if r["comid"] == 2)
    assert fish["target_class"] == "Poor" and fish["role"] == "exploratory_t3" and fish["kind"] == "class"
    oe = next(r for r in by_target["t__oe"] if r["comid"] == 3)
    assert oe["target_class"] == "Good" and oe["role"] == "exploratory_t3"
    assert next(r for r in by_target["t__bent_mmi"] if r["comid"] == 3)["role"] == "deciding_target"
    roles = summary["target_roles"]
    assert roles["deciding"] == {"T1": "reference_2013", "T2": "t__bent_mmi"}
    assert roles["field_targets"]["habitat_provision"] == [["a__phab_XFC_NAT", 1], ["a__phab_PCT_FAST", 1]]
    assert roles["field_targets"]["channel_evolution"] == [["a__phab_XBKF_H", -1], ["a__phab_BFWD_RAT", 1]]
    assert "t__fish_mmi" in roles["exploratory_t3"] and roles["exploratory_field"] == ["a__phab_BFWD_RAT"]
    assert set(roles["never_block"]) == {"t__fish_mmi", "t__oe", "a__phab_BFWD_RAT"}
    assert roles["deciding_cohort"] == "development_1314_1819"
    # the function table carries the targets where the agreement rows read them
    assert ("a__phab_PCT_FAST", 1) in field_evaluation.FUNCTION_TARGETS["habitat_provision"][1]
    assert ("a__phab_BFWD_RAT", 1) in field_evaluation.FUNCTION_TARGETS["floodplain_connectivity"][1]
    assert ("inc_ratio", -1) in field_evaluation.FUNCTION_TARGETS["high_flow_dynamics"][1]


def _scores_rows(rated_low=True):
    out = []
    for i in range(1, 5):
        rating = "Good" if i > 2 else "Poor"
        row = {"comid": i, "eci": i / 5., "physical": i / 5., "chemical": i / 5., "biological": i / 5.}
        for function in (field_evaluation.BIO, LOW, field_evaluation.BED, field_evaluation.CATCHMENT, "habitat_provision"):
            row.update({"index__" + function: .85 if i > 2 else .195, "rating__" + function: rating,
                        "score__" + function: 13 if i > 2 else 3, "route__" + function: "stored",
                        "completeness__" + function: "rated"})
        if not rated_low and i == 1:
            row.update({"index__" + LOW: None, "rating__" + LOW: None, "score__" + LOW: None,
                        "completeness__" + LOW: "withheld"})
        out.append(row)
    return out


def test_evaluate_reads_the_arms_from_the_manifest_and_applies_the_e2_scope(ledger):
    root, study_dir = ledger
    field_evaluation.build_observations(root, study_dir)
    manifest = {"candidates": [{"id": "alternative-1", "role": "base"},
                               {"id": "E2", "role": "candidate", "comparison_scope": r4.comparison_scope(r4.family_spec("E2"))}]}
    write_json(study_dir / "manifest.json", manifest)
    write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(_scores_rows()))
    write_parquet(study_dir / "scores/E2.parquet", pa.Table.from_pylist(_scores_rows(rated_low=False)))
    summary = field_evaluation.evaluate(root, study_dir, boot=4)
    assert summary["alternatives"] == ["alternative-1", "E2"] and summary["compared"] == ["E2"]
    assert summary["comparison_scopes"]["E2"]["function"] == LOW
    with (study_dir / "results/field_agreement.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert {r["alternative_id"] for r in rows} == {"E2"}
    eci_rows = [r for r in rows if r["function"] == "eci" and r["region"] == "US" and r["cohort"] == "latest_visit1"]
    assert {r["target"] for r in eci_rows} >= {"t__bent_mmi", "t__fish_mmi", "t__oe"}
    fish = next(r for r in eci_rows if r["target"] == "t__fish_mmi")
    assert fish["role"] == "exploratory_t3" and fish["statistic"] == "auc_good_vs_poor"
    bent = next(r for r in eci_rows if r["target"] == "t__bent_mmi")
    # the perennial scope: station 1 (withheld under E2) leaves every ECI comparison
    assert bent["scope_excluded_n"] == "1" and json.loads(bent["comparison_scope"])["rule"] == "candidate-rated"
    assert "delta_median" in rows[0] and "p_two_sided" in rows[0] and "reference_ci_low" in rows[0]
    # a base-only study pairs the base with itself and reports its own values
    write_json(study_dir / "manifest.json", {"candidates": [{"id": "alternative-1", "role": "base"}]})
    base_only = tmp = study_dir / "base-only"
    tmp.mkdir()
    shutil.copytree(study_dir / "cohorts", base_only / "cohorts")
    shutil.copyfile(study_dir / "manifest.json", base_only / "manifest.json")
    write_parquet(base_only / "scores/alternative-1.parquet", pa.Table.from_pylist(_scores_rows()))
    summary = field_evaluation.evaluate(root, base_only, boot=4)
    assert summary["compared"] == ["alternative-1"]
    with (base_only / "results/field_agreement.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows and {r["alternative_id"] for r in rows} == {"alternative-1"}
    assert all(r["delta"] in ("", "0.0") for r in rows)


def test_paired_statistics_report_the_median_p_and_value_intervals():
    rng = np.random.default_rng(3)
    base = rng.uniform(0, 1, 60)
    target = base + rng.normal(0, .3, 60) > .5
    candidate = np.clip(base + np.where(target, .15, -.15) + rng.normal(0, .05, 60), 0, 1)
    hucs = [f"{i % 12:08d}" for i in range(60)]
    got = field_evaluation.paired_statistics(base, candidate, target, hucs, "auc", boot=60)
    assert got["boot_valid"] == 60 and got["delta"] > 0 and got["delta_median"] is not None
    assert got["ci_low"] <= got["delta_median"] <= got["ci_high"]
    assert 0 < got["p_two_sided"] <= 1
    assert got["reference_ci_low"] <= got["reference"] <= got["reference_ci_high"]
    assert got["alternative_ci_low"] <= got["alternative"] <= got["alternative_ci_high"]
    same = field_evaluation.paired_statistics(base, base, target, hucs, "auc", boot=60)
    assert same["delta_median"] == 0 and same["p_two_sided"] == 1.0
    assert same["reference_ci_low"] == same["alternative_ci_low"] and same["reference_ci_low"] < same["reference"]
    few = field_evaluation.paired_statistics(base[:5], candidate[:5], target[:5], hucs[:5], "auc", boot=60)
    assert few["delta_median"] is None and few["p_two_sided"] is None and few["reference_ci_low"] is None


# --------------------------------------------------------------------------- #
# the split-aware fold refit
# --------------------------------------------------------------------------- #
def _reference_frame():
    rows = []
    rng = np.random.default_rng(5)
    for i in range(160):
        rows.append({"comid": 1000 + i, "level": "nars9" if i < 80 else "national",
                     "stratum": "nars9:CPL" if i < 80 else "national:national",
                     "slope_class": "lt_0.5", "fcode_class": "perennial" if i % 2 else "intermittent",
                     "screen": "strict", "_hucs": ("00000001",) if i % 5 == 0 else ("00000002",),
                     "woody_wsrp100": float(rng.uniform(5, 95)),
                     "q_min_ratio": float(rng.uniform(.2, .8)) if i % 2 else float(rng.uniform(0, .3)),
                     "er_median": 1 + i / 10, "pressure__agriculture_ws": float(i % 7)})
    return pd.DataFrame(rows)


def test_fit_keys_carry_a_candidate_sets_split_and_the_fold_filters_by_its_column():
    definition = {"quantity": "q_min_ratio", "stratifier": "nars9", "split": {"fcode_class": "perennial"},
                  "curves": {"CPL": {}, "national": {}}}
    assert spatial._fit_key(definition, "CPL") == ("q_min_ratio", "nars9", "nars9:CPL", "perennial")
    assert spatial._fit_key(definition, "national") == ("q_min_ratio", "national", "national:national", "perennial")
    plain = {"quantity": "q_cv_monthly", "stratifier": "nars9", "curves": {"CPL": {}, "national": {}}}
    assert spatial._fit_key(plain, "CPL") == ("q_cv_monthly", "nars9", "nars9:CPL", "")
    entrenchment = {"quantity": "er_median", "stratifier": "slope_class", "curves": {"national": {}, "ge_2": {}}}
    assert spatial._fit_key(entrenchment, "ge_2") == ("er_median", "national", "national:national", "ge_2")
    with pytest.raises(ValueError, match="registry split"):
        spatial._fit_key({"quantity": "q_min_ratio", "stratifier": "nars9", "split": {"slope_class": "ge_2"}, "curves": {}}, "CPL")
    assert spatial.split_column("q_min_ratio") == "fcode_class" and spatial.split_column("er_median") == "slope_class"
    assert spatial.set_quantities(["q_min_ratio", "woody_wsrp100"])[-1] == "q_min_ratio"
    with pytest.raises(ValueError, match="registry lacks"):
        spatial.set_quantities(["no_such_quantity"])
    frame = _reference_frame()
    definitions = {("q_min_ratio", "nars9", "nars9:CPL", "perennial"): True,
                   ("q_min_ratio", "national", "national:national", "perennial"): True,
                   ("woody_wsrp100", "nars9", "nars9:CPL", ""): True,
                   ("er_median", "national", "national:national", "lt_0.5"): True}
    # a fold with nothing excluded reproduces the candidate's own curve for the added set:
    # the perennial members' values, fitted under the builder's default endpoints
    fitted, records, _ = spatial.refit_fold(frame, definitions, set(), set())
    perennial = frame[(frame.stratum == "nars9:CPL") & (frame.fcode_class == "perennial")]
    own = curves.fit_curve(perennial.q_min_ratio.to_numpy(), curves.QUANTITIES["q_min_ratio"], "own")
    record = next(r for r in records if r["stratum"] == "nars9:CPL" and r["quantity"] == "q_min_ratio")
    assert record["split"] == "perennial" and record["split_column"] == "fcode_class"
    assert record["n_members"] == len(perennial) == 40 and record["points"] == own["points"]
    assert record["tail_near_iqr"] == .3 and record["curve_method_version"] == "iqr-seed-2"
    shipped = {"points": own["points"], "n": own["n"], "nMembers": len(perennial),
               **{k: own[k] for k in ("q25", "q50", "q75", "x39", "x69")}, "status": own["status"],
               "panelTier": "exploratory", "screen": "strict"}
    assert fitted[("q_min_ratio", "nars9", "nars9:CPL", "perennial")] == artifact._rounded(shipped)
    # the shipped sets' refits are unchanged by the split logic: whole strata, slope class split
    woody = next(r for r in records if r["quantity"] == "woody_wsrp100")
    assert woody["n_members"] == 80 and woody["split"] == "" and woody["split_column"] == ""
    expected = curves.fit_curve(frame[frame.stratum == "nars9:CPL"].woody_wsrp100.to_numpy(), curves.QUANTITIES["woody_wsrp100"], "x")
    assert woody["points"] == expected["points"]
    er = next(r for r in records if r["quantity"] == "er_median")
    assert er["split"] == "lt_0.5" and er["split_column"] == "slope_class" and er["n_members"] == 80
    # the excluded watershed leaves the perennial fit, and a frame without the column is refused
    fitted2, records2, _ = spatial.refit_fold(frame, definitions, {"00000001"}, set())
    assert next(r for r in records2 if r["stratum"] == "nars9:CPL" and r["quantity"] == "q_min_ratio")["n_members"] == 32
    with pytest.raises(ValueError, match="no 'fcode_class' column"):
        spatial.refit_fold(frame.drop(columns=["fcode_class"]), definitions, set(), set())
    # the candidate artifact takes the split fit under its key
    frozen = {"sets": {"flow-min-ratio": {"quantity": "q_min_ratio", "stratifier": "nars9",
                                          "split": {"fcode_class": "perennial"},
                                          "curves": {"CPL": {"points": [[0, 0]]}, "national": {"points": [[0, 0]]}}}}}
    candidate, unavailable, missing = spatial._candidate_artifact(frozen, fitted, 0)
    assert set(candidate["sets"]["flow-min-ratio"]["curves"]) == {"CPL", "national"} and not missing and not unavailable
    assert candidate["sets"]["flow-min-ratio"]["split"] == {"fcode_class": "perennial"}


def test_read_reference_reads_the_candidate_quantities(tmp_path):
    original = tmp_path / "original"
    ids = list(range(1, 41))
    write_parquet(original / "landscape.parquet", pa.Table.from_pylist(
        [{"comid": c, "huc8": "01020304", "woody_wsrp100": 10. + c, "natural_wsrp100": 20. + c,
          "erom__q_cv_monthly": .1 + c / 100, "erom__q_min_ratio": c / 50, "agriculture_ws": c % 5} for c in ids]))
    write_parquet(original / "values.parquet", pa.Table.from_pylist(
        [{"comid": c, "er_median": 1 + c / 10, "bankfull_width_cv": c / 100} for c in ids]))
    members = pd.DataFrame([{"comid": c, "huc12": "010203040101", "level": "national", "stratum": "national:national",
                             "slope_class": "ge_2", "fcode_class": "perennial", "screen": "strict"} for c in ids])
    frame, receipts = spatial._read_reference(original, members, ["q_min_ratio", "bankfull_width_cv"])
    assert {"q_min_ratio", "bankfull_width_cv", "woody_wsrp100", "er_median"} <= set(frame.columns)
    assert frame.q_min_ratio.iloc[0] == pytest.approx(1 / 50) and frame.bankfull_width_cv.iloc[-1] == pytest.approx(.4)
    assert "erom__q_min_ratio" in receipts[0]["selected_columns"] and "bankfull_width_cv" in receipts[1]["selected_columns"]
    plain, _ = spatial._read_reference(original, members)
    assert "q_min_ratio" not in plain.columns


# --------------------------------------------------------------------------- #
# set stability (P4)
# --------------------------------------------------------------------------- #
def _curve_from(values, quantity, n_members):
    fit = curves.fit_curve(np.asarray(values, float), curves.QUANTITIES[quantity], "syn")
    return artifact._curve({**fit, "n_members": n_members, "panel_tier": "complete", "screen": "strict"})


@pytest.fixture
def stability_study(tmp_path):
    root = tmp_path / "root"
    study_dir = root / "review/alternative-studies/2026-09-27-low-flow-ratio-alternatives"
    original = root / set_stability.ORIGINAL
    rng = np.random.default_rng(9)
    members, landscape = [], []
    comid = 1
    for level, stratum in (("nars9", "nars9:CPL"), ("national", "national:national")):
        for i in range(60):
            perennial = i % 2 == 1
            members.append({"comid": comid, "huc12": f"0102030401{i % 6:02d}", "level": level, "stratum": stratum,
                            "slope_class": "ge_2", "fcode_class": "perennial" if perennial else "intermittent",
                            "screen": "strict", "panel_tier": "complete"})
            landscape.append({"comid": comid, "huc8": "01020304", "fcode_class": "perennial" if perennial else "intermittent",
                              "erom__q_cv_monthly": float(rng.uniform(.2, 2.)),
                              "erom__q_min_ratio": float(rng.uniform(.2, .8)) if perennial else float(rng.uniform(0, .2)),
                              "woody_wsrp100": float(rng.uniform(5, 95))})
            comid += 1
    write_parquet(original / "panels/panel_members.parquet", pa.Table.from_pylist(members))
    write_parquet(original / "landscape.parquet", pa.Table.from_pylist(landscape))
    write_parquet(original / "values.parquet", pa.Table.from_pylist([{"comid": r["comid"], "er_median": 1.5} for r in landscape]))
    write_parquet(original / "curves/curve_registry.parquet", pa.Table.from_pylist([{"source": "registry"}]))
    frame = pd.DataFrame(landscape).merge(pd.DataFrame(members)[["comid", "level", "stratum", "fcode_class"]], on=["comid", "fcode_class"])
    base_sets, cand_sets = {}, {}
    for key, stratum in (("CPL", "nars9:CPL"), ("national", "national:national")):
        panel = frame[frame.stratum == stratum]
        base_sets[key] = _curve_from(panel.erom__q_cv_monthly, "q_cv_monthly", len(panel))
        perennial = panel[panel.fcode_class == "perennial"]
        cand_sets[key] = _curve_from(perennial.erom__q_min_ratio, "q_min_ratio", len(perennial))
    base_artifact = {"schemaVersion": 1, "provenance": {"registry": {"sha256": sha(original / "curves/curve_registry.parquet")}},
                     "sets": {"flow-variability": {"quantity": "q_cv_monthly", "stratifier": "nars9", "higherIsBetter": False,
                                                   "curves": base_sets}}}
    cand_artifact = deepcopy(base_artifact)
    cand_artifact["sets"]["flow-min-ratio"] = {"quantity": "q_min_ratio", "stratifier": "nars9", "higherIsBetter": True,
                                               "split": {"fcode_class": "perennial"}, "curves": cand_sets}
    write_json(study_dir / "candidates/alternative-1/app-data/reference-curves.json", base_artifact)
    write_json(study_dir / "candidates/E2/app-data/reference-curves.json", cand_artifact)
    write_json(study_dir / "candidates/E8/app-data/reference-curves.json", base_artifact)
    # the stored sample: the population, half in CPL, half elsewhere (the national fallback)
    population = []
    for i in range(200):
        perennial = i % 3 != 0
        population.append({"comid": 5000 + i, "nars9": "CPL" if i % 2 else "NAP", "l2": "8.2", "huc8": f"{i % 7:08d}",
                           "state": "VA", "sample": True, "sample_weight": 3.0, "woody_82": False, "station": False,
                           "evidence_available": True})
        landscape.append({"comid": 5000 + i, "huc8": f"{i % 7:08d}", "fcode_class": "perennial" if perennial else "intermittent",
                          "erom__q_cv_monthly": float(rng.uniform(.2, 2.)),
                          "erom__q_min_ratio": float(rng.uniform(.1, .9)) if perennial else float(rng.uniform(0, .2)),
                          "woody_wsrp100": float(rng.uniform(5, 95))})
    write_parquet(study_dir / "cohorts/reaches.parquet", pa.Table.from_pylist(population))
    write_parquet(root / "analysis/landscape.parquet", pa.Table.from_pylist(landscape))
    write_parquet(root / "analysis/values.parquet", pa.Table.from_pylist([{"comid": r["comid"], "er_median": 1.5} for r in landscape]))
    manifest = {"candidates": [{"id": "alternative-1", "role": "base"}, {"id": "E2", "role": "candidate", "family": "low_flow_proxy"},
                               {"id": "E8", "role": "candidate", "family": "completeness"}]}
    write_json(study_dir / "manifest.json", manifest)
    return root, study_dir


def test_set_stability_measures_the_replaced_set_side_by_side_with_its_margin(stability_study):
    root, study_dir = stability_study
    before = {str(p): sha(p) for p in root.rglob("*.parquet")}
    result = set_stability.run(root, study_dir, n_boot=4)
    assert result["status"] == "complete" and result["applicable"] is True and result["not_applicable"] == ["E8"]
    family, = result["families"]
    assert (family["alternative"], family["base_set"], family["candidate_set"], family["function"]) == (
        "E2", "flow-variability", "flow-min-ratio", LOW)
    assert family["margin"] == .02 and family["difference"] == pytest.approx(family["candidate_flip"] - family["base_flip"])
    assert family["within_margin"] == (family["difference"] <= .02)
    base, cand = family["base"], family["candidate"]
    assert base["split"] is None and cand["split"] == {"fcode_class": "perennial"}
    assert {c["key"] for c in base["curves"]} == {"CPL", "national"} == {c["key"] for c in cand["curves"]}
    assert all(c["fit_linkage"] == "canonical_match" for c in base["curves"] + cand["curves"])
    assert all(c["seed"] == 7 and c["n_requested"] == 4 for c in base["curves"] + cand["curves"])
    # each set on its own population: the base rates every sample reach, the candidate the
    # perennial ones (133 of the 200: i % 3 != 0)
    assert base["population"]["n"] == 200 and cand["population"]["n"] == 133
    assert base["pooled"]["n_population"] == 200 and cand["pooled"]["n_population"] == 133
    assert cand["linkage"]["CPL"]["n_members"] == 30 and base["linkage"]["CPL"]["n_members"] == 60
    assert family["comparison"]["population_n"] == 133 and family["comparison"]["design"] == "stored-sample"
    assert before == {p: sha(Path(p)) for p in before}
    # a rerun returns the same result; different settings are refused
    assert set_stability.run(root, study_dir, n_boot=4)["input_digest"] == result["input_digest"]
    with pytest.raises(RuntimeError, match="different inputs"):
        set_stability.run(root, study_dir, n_boot=5)
    # P4 reads it, and a family without a refitted set is not applicable
    p4 = outcomes.p4(result, "E2")
    assert p4["status"] == "computed" and p4["families"][0]["candidate_set"] == "flow-min-ratio"
    assert outcomes.p4(result, "E8")["status"] == "not applicable"
    # the woody study's helpers are what it reuses, and the legacy one-argument fit still works
    assert woody_stability._fit(np.linspace(10, 90, 40), "woody_wsrp100")["status"] == "complete"


def test_set_stability_records_not_applicable_without_a_refitted_family(stability_study):
    root, study_dir = stability_study
    write_json(study_dir / "manifest.json", {"candidates": [{"id": "alternative-1", "role": "base"}, {"id": "E8", "role": "candidate"}]})
    result = set_stability.run(root, study_dir, n_boot=4)
    assert result["applicable"] is False and result["families"] == [] and result["not_applicable"] == ["E8"]
    assert result["note"].startswith("not applicable")


# --------------------------------------------------------------------------- #
# the outcomes and the decision
# --------------------------------------------------------------------------- #
def _reaches(n=40):
    rows = [{"comid": i, "sample": True, "sample_weight": 2.0 if i % 2 else 1.0, "l2": "8.2" if i < 20 else "8.3",
             "huc8": f"{i % 5:08d}"} for i in range(n)]
    return pd.DataFrame(rows).set_index("comid")


def test_p3_counts_withheld_ratings_as_documented_gaps_never_as_favorable():
    reaches = _reaches()
    base = pd.DataFrame({"comid": range(40), "eci": np.linspace(.2, .8, 40), f"rating__{LOW}": ["Poor"] * 40,
                         "rating__habitat_provision": ["Good"] * 40, f"completeness__{LOW}": ["rated"] * 40,
                         "completeness__habitat_provision": ["rated"] * 40}).set_index("comid")
    cand = base.copy()
    cand.loc[:9, f"rating__{LOW}"] = None
    cand.loc[:9, f"completeness__{LOW}"] = "withheld"
    cand[f"gap__{LOW}"] = None
    cand.loc[:9, f"gap__{LOW}"] = json.dumps({"statement": "intermittent", "rule": {}, "matched": {}})
    cand.loc[:9, "eci"] = base.loc[:9, "eci"] + .1
    got = outcomes.p3(base, cand, reaches)
    low = got["functions"][LOW]
    assert low["availability_base"] == 1.0 and low["availability_candidate"] == pytest.approx(0.75)
    assert low["withheld_share"] == pytest.approx(.25) and low["lost_withheld_share"] == low["lost_share"]
    assert low["documented_gaps_only"] is True and low["n_lost"] == 10 and low["gained_share"] == 0
    assert got["documented_gaps_only"] is True and got["availability_unchanged"] is False
    assert got["gaps"][LOW] == {"intermittent": 15.0}
    assert got["eci_mean_delta_paired"] == pytest.approx(.1 * 15 / 60)
    assert got["changed_ratings_share"] == pytest.approx(.25)
    gained = cand.copy()
    gained.loc[30, "rating__habitat_provision"] = None
    base2 = base.copy()
    base2.loc[30, "rating__habitat_provision"] = None
    gained.loc[30, "rating__habitat_provision"] = "Fair"
    assert outcomes.p3(base2, gained, reaches)["documented_gaps_only"] is False
    same = outcomes.p3(base, base, reaches)
    assert same["availability_unchanged"] and same["changed_ratings_share"] == 0 and same["eci_mean_delta_paired"] == 0


def test_p5_completeness_optimism_within_strata_weighted_and_bootstrapped():
    reaches = _reaches(40)
    rated = np.where(np.arange(40) % 4 == 0, 12, 20)
    eci = np.where(rated < 15, .7, .5) + np.where(np.arange(40) < 20, 0, .1)
    cand = pd.DataFrame({"comid": range(40), "eci": eci, "functions_rated": rated}).set_index("comid")
    got = outcomes.p5(cand, reaches, boot=60)
    assert got["status"] == "computed" and got["difference"] == pytest.approx(.2)
    assert got["exceeds_margin"] is True and got["margin"] == .02
    assert {s["l2"] for s in got["strata"]} == {"8.2", "8.3"} and all(s["difference"] == pytest.approx(.2) for s in got["strata"])
    assert got["n_incomplete"] == 10 and got["n_complete"] == 30 and got["boot_valid"] == 60
    assert got["ci_low"] == pytest.approx(.2) and got["p_two_sided"] < .05
    absent = outcomes.p5(pd.DataFrame({"comid": [1], "eci": [.5]}).set_index("comid"), reaches)
    assert absent["status"] == "not applicable"


def _p1_block(t1, t2, held=None, t1_median=None, t2_median=None):
    def row(lo, hi, median):
        return {"supported": lo is not None, "ci_low": lo, "ci_high": hi, "delta_median": median,
                "delta": median, "present": True}
    held = held or (t1, t2)
    # a retrospective row that would fail every rule rides in the cohorts block: no decision reads it
    failing = row(-.5, -.4, -.45)
    return {"deciding_cohort": r4.DECIDING_COHORT, "designs": {
        "frozen": {"T1": row(*t1, t1_median), "T2": row(*t2, t2_median),
                   "T3_fish_mmi": row(None, None, None), "T3_oe": row(None, None, None)},
        "watershed-held-out": {"T1": row(*held[0], t1_median), "T2": row(*held[1], t2_median),
                               "T3_fish_mmi": row(None, None, None), "T3_oe": row(None, None, None)}},
        "cohorts": {"retrospective_2324": {"frozen": {"T1": failing, "T2": failing},
                                           "watershed-held-out": {"T1": failing, "T2": failing}}}}


def _outcomes(p1, *, findings=(), documented=True, unchanged=False, p4=None, p5=None):
    return {"P1": p1, "P2": {"findings": list(findings), "blocks": bool(findings)},
            "P3": {"documented_gaps_only": documented, "availability_unchanged": unchanged},
            "P4": p4 or {"status": "not applicable"}, "P5": p5 or {"status": "not applicable"}}


def test_the_decision_applies_the_yaml_margins_for_each_rule():
    accuracy = r4.family_spec("E2")
    passing = _p1_block((.02, .06), (-.005, .03), t1_median=.03, t2_median=.01)
    decision = outcomes.decide(accuracy, _outcomes(passing))
    assert decision["adopted"] and decision["decision_rule"] == "accuracy_change" and decision["primary_target"] == "T1"
    assert decision["readings"]["primary_T1"]["passes"] and not decision["readings"]["primary_T2"]["passes"]
    small = _p1_block((.002, .06), (-.005, .03), t1_median=.008, t2_median=.01)
    failing = outcomes.decide(accuracy, _outcomes(small))
    assert not failing["adopted"] and any("median delta is below 0.01" in r for r in failing["reasons"])
    other_low = _p1_block((.02, .06), (-.02, .03), t1_median=.03)
    assert any("T2 lower bound is below -0.01" in r for r in outcomes.decide(accuracy, _outcomes(other_low))["reasons"])
    held_out_only = _p1_block((.02, .06), (-.005, .03), held=((-.01, .05), (-.005, .03)), t1_median=.03)
    assert any(r.startswith("watershed-held-out") for r in outcomes.decide(accuracy, _outcomes(held_out_only))["reasons"])
    simplification = r4.family_spec("E1")
    ok = _p1_block((-.008, .01), (-.005, .01), t1_median=0, t2_median=0)
    assert outcomes.decide(simplification, _outcomes(ok))["adopted"]
    assert outcomes.decide(simplification, _outcomes(ok, documented=False))["reasons"] == ["rating availability changed other than by documented gaps"]
    bad = _p1_block((-.012, .01), (-.005, .01), t1_median=0, t2_median=0)
    verdict = outcomes.decide(simplification, _outcomes(bad))
    assert not verdict["adopted"] and any("T1 lower bound is below -0.01" in r for r in verdict["reasons"])
    e3 = r4.family_spec("E3")
    t2_bad = _p1_block((-.005, .01), (-.05, -.02), t1_median=0, t2_median=-.03)
    assert outcomes.decide(e3, _outcomes(t2_bad))["adopted"], "T2 never decides for population support"
    assert outcomes.decide(e3, _outcomes(t2_bad))["deciding_targets"] == ["T1"]
    presentation = r4.family_spec("E8")
    p5 = {"status": "computed", "difference": .05, "exceeds_margin": True}
    assert outcomes.decide(presentation, _outcomes(ok, p5=p5))["adopted"]
    flat = outcomes.decide(presentation, _outcomes(ok, p5={"status": "computed", "difference": .01, "exceeds_margin": False}))
    assert not flat["adopted"] and "does not exceed 0.02" in flat["reasons"][0]
    # P2 blocks and the P4 margin block every rule
    finding = {"design": "frozen", "function": LOW, "statistic": "spearman", "ci_high": -.01}
    blocked = outcomes.decide(simplification, _outcomes(ok, findings=[finding]))
    assert not blocked["adopted"] and blocked["p2_blocks"] and "P2 retains the base" in blocked["reasons"][0]
    unstable = outcomes.decide(accuracy, _outcomes(passing, p4={"status": "computed", "within_margin": False}))
    assert not unstable["adopted"] and any(r.startswith("P4") for r in unstable["reasons"])
    assert decision["margins"] == r4.MARGINS
    assert decision["deciding_cohort"] == "development_1314_1819" and "never the retrospective rows" in decision["reads"]


def _agreement_row(alternative, function, target, statistic, region="US", cohort=r4.DECIDING_COHORT, **values):
    row = {"alternative_id": alternative, "function": function, "target": target, "statistic": statistic, "region": region,
           "cohort": cohort, "support_floor_met": True, "boot_requested": 1000, "boot_valid": 950,
           "reference": .7, "alternative": .72, "delta": .02, "delta_median": .02, "ci_low": .005, "ci_high": .04,
           "p_two_sided": .01, "n": 300}
    row.update(values)
    return row


def test_p1_and_p2_read_the_development_cohort_and_never_block_on_exploratory_rows():
    field = [_agreement_row("E4", "eci", "reference_2013", "auc_reference_vs_impaired"),
             _agreement_row("E4", "eci", "t__bent_mmi", "auc_good_vs_poor", ci_low=-.005),
             _agreement_row("E4", "eci", "t__fish_mmi", "auc_good_vs_poor", ci_low=-.03, ci_high=.01),
             _agreement_row("E4", "eci", "reference_2013", "auc_reference_vs_impaired", cohort="retrospective_2324", delta=.05),
             _agreement_row("E4", "eci", "reference_2013", "auc_reference_vs_impaired", cohort="latest_visit1", delta=.07),
             _agreement_row("E4", "habitat_provision", "a__phab_XFC_NAT", "spearman", region="SAP", ci_low=-.3, ci_high=-.1),
             _agreement_row("E4", "population_support", "t__bent_mmi", "auc_good", ci_low=-.3, ci_high=-.1),
             _agreement_row("E4", "habitat_provision", "t__instrmcvr", "auc_good", ci_low=-.3, ci_high=-.02, boot_valid=700),
             _agreement_row("E4", "light_thermal_regime", "t__ripveg", "auc_good", ci_low=-.3, ci_high=-.02),
             _agreement_row("E5", "channel_evolution", "a__phab_BFWD_RAT", "spearman", ci_low=-.3, ci_high=-.1),
             _agreement_row("E5", "channel_evolution", "a__phab_XBKF_H", "spearman", ci_low=-.3, ci_high=-.1),
             # a latest-visit finding that would block: not the deciding cohort
             _agreement_row("E5", "channel_evolution", "inc_ratio", "spearman", cohort="latest_visit1", ci_low=-.3, ci_high=-.1)]
    spatial_rows = [_agreement_row("E4", "eci", "reference_2013", "auc_reference_vs_impaired", cohort="spatial_refit:development_1314_1819"),
                    _agreement_row("E4", "eci", "t__bent_mmi", "auc_good_vs_poor", cohort="spatial_refit:development_1314_1819")]
    p1 = outcomes.p1(field, spatial_rows, "E4")
    assert p1["deciding_cohort"] == "development_1314_1819"
    assert p1["designs"]["frozen"]["T1"]["supported"] and p1["designs"]["frozen"]["T1"]["delta_median"] == .02
    assert p1["designs"]["frozen"]["T3_fish_mmi"]["role"] == "exploratory" and p1["designs"]["frozen"]["T3_oe"]["present"] is False
    assert p1["designs"]["watershed-held-out"]["T2"]["supported"]
    assert p1["cohorts"]["retrospective_2324"]["frozen"]["T1"]["delta"] == .05
    assert p1["cohorts"]["latest_visit1"]["frozen"]["T1"]["delta"] == .07
    assert p1["cohorts"]["retrospective_2324"]["watershed-held-out"]["T1"]["present"] is False
    p2 = outcomes.p2(field, spatial_rows, "E4", ["habitat_provision"])
    assert p2["blocks"] and len(p2["findings"]) == 1 and p2["cohort"] == "development_1314_1819"
    finding = p2["findings"][0]
    assert finding["region"] == "SAP" and finding["statistic"] == "spearman" and "below zero" in finding["reason"]
    # supported: T1 and T2 in both designs and the SAP Spearman row; the 700-draw row is reviewed, not
    # supported; the fish MMI row is exploratory (the population-support row is another function's)
    assert p2["rows_reviewed"] == 6 and p2["rows_supported"] == 5 and p2["rows_exploratory"] == 1
    assert outcomes.p2(field, spatial_rows, "E4", None)["rows_exploratory"] == 2
    assert "light_thermal_regime" not in p2["functions"]
    every = outcomes.p2(field, spatial_rows, "E4", None)
    assert len(every["findings"]) == 2 and {f["function"] for f in every["findings"]} == {"habitat_provision", "light_thermal_regime"}
    # E5: the width to depth ratio never blocks, the bankfull height may; latest-visit rows never decide
    e5 = outcomes.p2(field, spatial_rows, "E5", ["channel_evolution"])
    assert [f["target"] for f in e5["findings"]] == ["a__phab_XBKF_H"] and e5["rows_exploratory"] == 1
    assert not outcomes.p2(field, spatial_rows, "E1", ["low_flow_baseflow_dynamics"])["blocks"]


def test_p6_counts_inputs_and_sources_from_the_catalog():
    catalog = read_json(DATA / "screening-methods.json")
    metrics = read_json(DATA / "easi-metrics.json")
    got = outcomes.p6(catalog, metrics, {"seconds": 12.5, "rows": 3, "unrated_reaches": 0, "route": "package"}, 40.0)
    assert len(got["inputs_per_function"]) == 20 and got["inputs_total"] >= 20
    assert got["distinct_sources_n"] == len(got["distinct_sources"]) > 5
    assert got["runtime_seconds_arm"] == 12.5 and got["runtime_seconds_scores_step"] == 40.0 and got["failures"] == 0
    assert got["route"] == "package" and got["unavailable_services"].startswith("none")


def test_the_finalist_report_gives_bh_q_values_across_the_primaries():
    summaries = []
    for family, p in (("E1", .01), ("E2", .04), ("E3", .2), ("E8", .03)):
        block = {"P1": {"designs": {"frozen": {"T1": {"p_two_sided": p}}}}, "P5": {"p_two_sided": p},
                 "decision": {"primary_target": "T1", "adopted": p < .05, "reasons": []},
                 "decision_rule": "presentation" if family == "E8" else "simplification", "family": family}
        summaries.append({"study_id": f"2026-09-27-{family.lower()}-alternatives", "candidates": [{"id": family, **block}]})
    got = round4_finalist.report(summaries)
    q = {row["family"]: row["q"] for row in got["rows"]}
    assert q == {"E1": pytest.approx(.04), "E2": pytest.approx(.0533333333), "E3": pytest.approx(.2), "E8": pytest.approx(.0533333333)}
    assert [row["q_supports"] for row in got["rows"]] == [True, True, False, True]
    assert got["families_missing"] == ["E4", "E5", "E6", "E7"] and got["complete"] is False
    assert next(row for row in got["rows"] if row["family"] == "E8")["primary"].startswith("P5")
    assert round4_finalist.benjamini_hochberg({"a": .01, "b": None}) == {"a": .01}


def test_the_finalist_reads_the_retrospective_cohort_once_and_apart(tmp_path):
    retro = {"frozen": {"T1": {"delta": .03, "ci_low": .01, "ci_high": .05, "supported": True, "present": True, "n": 200},
                        "T2": {"delta": -.01, "ci_low": -.03, "ci_high": .01, "supported": True, "present": True, "n": 200}},
             "watershed-held-out": {"T1": {"present": False}, "T2": {"present": False}}}
    summaries = [{"study_id": "2026-09-27-low-flow-intermittence-alternatives", "candidates": [
        {"id": "E1", "family": "low_flow_intermittence", "decision_rule": "simplification",
         "decision": {"adopted": True, "deciding_cohort": "development_1314_1819", "primary_target": "T1", "reasons": []},
         "P1": {"designs": {"frozen": {"T1": {"p_two_sided": .02}}}, "cohorts": {"retrospective_2324": retro}}}]}]
    got = round4_finalist.retrospective_report(summaries)
    assert got["cohort"] == "retrospective_2324" and "read once" in got["note"]
    row, = got["rows"]
    assert row["family"] == "E1" and row["adopted_by_margins"] is True and row["deciding_cohort"] == "development_1314_1819"
    assert row["retrospective"]["frozen"]["T1"]["delta"] == .03 and row["retrospective"]["watershed-held-out"]["T2"] == {"present": False}
    # the multiplicity report never carries the retrospective rows
    assert "retrospective" not in json.dumps(round4_finalist.report(summaries))
    # the CLI writes it apart and refuses a second write
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summaries[0]), encoding="utf-8")
    out = tmp_path / "retro.json"
    assert round4_finalist.main(["--summary", str(summary_path), "--out", str(tmp_path / "bh.json"), "--retrospective", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["rows"][0]["family"] == "E1"
    assert "retrospective" not in json.loads((tmp_path / "bh.json").read_text(encoding="utf-8"))["rows"][0]
    with pytest.raises(SystemExit, match="read once"):
        round4_finalist.main(["--summary", str(summary_path), "--retrospective", str(out)])


# --------------------------------------------------------------------------- #
# EA1: the case set and the campaign replay
# --------------------------------------------------------------------------- #
def test_export_cases_writes_chunked_case_files_with_an_index(tmp_path):
    root, study_dir = _study_paths(tmp_path)
    rows = _evidence(study_dir, _records(3))
    index = cases.export_cases(study_dir, chunk=2)
    assert index["cases"] == 3 and [c["rows"] for c in index["chunks"]] == [2, 1]
    first = json.loads(Path(index["chunks"][0]["path"]).read_text(encoding="utf-8"))
    assert first["schema"] == cases.SCHEMA and [c["id"] for c in first["cases"]] == [str(int(r["comid"])) for r in rows[:2]]
    assert isinstance(first["cases"][0]["record"]["streamcat"], dict)
    assert sha(Path(index["chunks"][0]["path"])) == index["chunks"][0]["sha256"]
    assert read_json(study_dir / "cases/index.json") == index
    write_parquet(study_dir / "evidence/stored/dup.parquet", pa.Table.from_pylist(rows[:1]))
    with pytest.raises(ValueError, match="duplicate COMID"):
        cases.export_cases(study_dir, tmp_path / "again", chunk=2)


def test_replay_base_scores_the_case_set_through_the_campaign_and_matches_the_study(tmp_path):
    """EA1 on three library records: the base package scored through evaluation_campaign
    (one worker, the package active in its process) gives the study's rating, index and ECI
    per COMID; a changed study row is listed."""
    from builder.analysis.alternatives import replay_base
    root, study_dir = _study_paths(tmp_path)
    rows = _evidence(study_dir, _records(3))
    expected = _expected_rows(rows)
    write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(expected))
    zip_path, digest = _base_zip(tmp_path)
    write_json(study_dir / "scores/alternative-1.json", {"package_digest": digest, "route": "package"})
    report_doc = replay_base.replay(study_dir, zip_path, tmp_path / "campaign", workers=1)
    assert report_doc["identical"] is True and report_doc["compared"] == 3 and report_doc["differences_n"] == 0
    assert report_doc["scored_package_digests"] == [digest] and report_doc["same_package_digest"] is True
    assert report_doc["chunks"][0]["jobs"]["completed"] == 1 and report_doc["cases"] == 3
    # a changed study row is listed as a difference (the campaign's job is reused, not rescored)
    changed = [dict(r) for r in expected]
    changed[0]["eci"] = (changed[0]["eci"] or 0) + .1
    write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(changed))
    again = replay_base.replay(study_dir, zip_path, tmp_path / "campaign", workers=1)
    assert again["identical"] is False and again["differences"][0]["field"] == "eci"
    assert again["chunks"][0]["jobs"]["skipped"] == 1
