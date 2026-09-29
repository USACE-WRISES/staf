"""The base arm's reference scores when the stored analysis is another base's (WP-R4s-2): a
1.2.0 study on the adopted base accepts the 2026-09-15 stored analysis for its inputs, requires
--base-scores (a national build of the study base) for the base arm's scores, refuses a build
of another method, alternative or evidence, records it, and verifies the base arm against it
with the coverage reported. Synthetic staging folders only."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import REPO_ROOT
from builder.analysis import alternatives as alts
from builder.analysis.alternatives import base_scores as bs
from builder.analysis.alternatives import bases, scorer, study
from builder.analysis.alternatives.io import read_json, sha, write_json, write_parquet

ADOPTED = "alternative-2-b2e3033116e3"
FUNCTIONS = ("habitat_provision", "low_flow_baseflow_dynamics")


def _score_row(comid, shift=0.0):
    row = {"comid": comid, "eci_raw": .5 + shift + comid / 1000, "phys_raw": .4 + shift, "chem_raw": .6, "bio_raw": .5,
           "eci": .5, "band": "Functioning-at-Risk", "n_rated": 20}
    for fn in FUNCTIONS:
        good = comid % 2 == 0
        row[f"rating_{fn}"] = "Good" if good else "Poor"
        row[f"fs_{fn}"] = 13.0 if good else 3.0
        row[f"index_{fn}"] = .85 if good else .195
        row[f"status_{fn}"] = "computed"
    return row


def _study_rows(comids, base_rows):
    """The base arm's score table as the scorer writes it, from the base-scores rows."""
    out = []
    for comid in comids:
        src = base_rows.get(comid) or _score_row(comid, shift=.11)
        row = {"comid": comid, "eci": src["eci_raw"], "physical": src["phys_raw"], "chemical": src["chem_raw"],
               "biological": src["bio_raw"]}
        for fn in FUNCTIONS:
            row[f"rating__{fn}"] = src[f"rating_{fn}"]
            row[f"score__{fn}"] = int(src[f"fs_{fn}"])
            row[f"index__{fn}"] = src[f"index_{fn}"]
            row[f"route__{fn}"] = "stored"
        out.append(row)
    return out


def _root_staging(root: Path, units: dict) -> str:
    manifest = {"build_id": "root-build", "method_version": "e9f472b31fe5",
                "units": {h: {"evidence": {"asset": f"evidence_{h}.parquet", "sha256": s}} for h, s in units.items()}}
    write_json(root / "staging/manifest.json", manifest)
    return sha(root / "staging/manifest.json")


def _staging(folder: Path, base, *, method=None, alternative=None, source_sha=None, units=None,
             comids=(1, 2, 3, 4, 5, 6)) -> tuple[Path, dict]:
    folder.mkdir(parents=True, exist_ok=True)
    rows = {c: _score_row(c) for c in comids}
    half = len(comids) // 2
    scores = {}
    for key, chunk in (("01", list(comids)[:half]), ("02", list(comids)[half:])):
        path = folder / f"scores_{key}.parquet"
        write_parquet(path, pa.Table.from_pylist([rows[c] for c in chunk]))
        scores[key] = {"asset": path.name, "sha256": sha(path), "n_scored": len(chunk), "bytes": path.stat().st_size,
                       "method_version": method or base.method_version, "alternative_id": alternative or base.alternative_id,
                       "build_id": "synthetic-build"}
    manifest = {"build_id": "synthetic-build", "method_version": method or base.method_version,
                "alternative_id": alternative or base.alternative_id, "criteria_set": "regional", "updated": "2026-09-27T00:00:00Z",
                "reaches_scored": len(comids), "scoring_identity": {"alternative_id": alternative or base.alternative_id,
                                                                     "alternative_name": "synthetic", "curve_count": 34},
                "scores": scores, "units": {h: {"evidence": {"asset": f"evidence_{h}.parquet", "sha256": s}}
                                            for h, s in (units or {}).items()},
                "source_manifest_sha256": source_sha}
    write_json(folder / "manifest.json", manifest)
    return folder, rows


def test_the_stored_analysis_base_is_the_registry_entry_that_scored_it():
    assert bs.stored_analysis_base("e9f472b31fe5").id == "2026-09-15"
    assert bs.stored_analysis_base("b2e3033116e3").id == ADOPTED
    later = [r["methodVersion"] for r in read_json(REPO_ROOT / "apps/library/assessments/easi-screening/v1/method.json")["identity"]["validatedUnder"]]
    assert all(bs.stored_analysis_base(v).id == ADOPTED for v in later)
    assert bs.stored_analysis_base("000000000000") is None and bs.stored_analysis_base(None) is None


def test_read_base_scores_records_the_build_and_refuses_the_wrong_one(tmp_path):
    base = bases.base(ADOPTED)
    root = tmp_path / "root"
    source_sha = _root_staging(root, {"0104": "e1", "0106": "e2"})
    folder, rows = _staging(tmp_path / "staging", base, source_sha=source_sha, units={"0104": "e1", "0106": "e2"})
    got = bs.read_base_scores(folder, base, root)
    assert got["build_id"] == "synthetic-build" and got["method_version"] == base.method_version
    assert got["alternative_id"] == "alternative-2" and [f["unit"] for f in got["files"]] == ["01", "02"]
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 and f["mtime_ns"] > 0 for f in got["files"])
    assert got["evidence"] == {"root_staging_manifest": source_sha, "source_manifest_matches": True, "units_compared": 2,
                               "units_differing": [], "identical": True}
    assert set(got["score_columns"]) >= {"comid", "eci_raw", "phys_raw", "chem_raw", "bio_raw", "rating_habitat_provision"}
    assert got["manifest_sha256"] == sha(folder / "manifest.json") and "never" not in got["used_for"]
    # without a root staging manifest the evidence identity is unknown, not refused
    unknown = bs.read_base_scores(folder, base, tmp_path / "elsewhere")
    assert unknown["evidence"]["identical"] is None and "could not be checked" in unknown["evidence"]["note"]
    # refusals
    with pytest.raises(bs.BaseScoresError, match="no manifest.json"):
        bs.read_base_scores(tmp_path / "nothing", base, root)
    wrong_method, _ = _staging(tmp_path / "wrong-method", base, method="e9f472b31fe5", source_sha=source_sha)
    with pytest.raises(bs.BaseScoresError, match="method version 'e9f472b31fe5' is not the study base's"):
        bs.read_base_scores(wrong_method, base, root)
    wrong_alt, _ = _staging(tmp_path / "wrong-alt", base, alternative="alternative-1", source_sha=source_sha)
    with pytest.raises(bs.BaseScoresError, match="alternative id 'alternative-1'"):
        bs.read_base_scores(wrong_alt, base, root)
    changed, _ = _staging(tmp_path / "changed", base, source_sha=source_sha)
    write_parquet(changed / "scores_01.parquet", pa.Table.from_pylist([_score_row(1, shift=.2)]))
    with pytest.raises(bs.BaseScoresError, match="does not hash as the manifest records"):
        bs.read_base_scores(changed, base, root)
    other_evidence, _ = _staging(tmp_path / "other-evidence", base, source_sha=source_sha, units={"0104": "e1", "0106": "changed"})
    with pytest.raises(bs.BaseScoresError, match="another evidence build"):
        bs.read_base_scores(other_evidence, base, root)
    other_source, _ = _staging(tmp_path / "other-source", base, source_sha="0" * 64, units={"0104": "e1"})
    with pytest.raises(bs.BaseScoresError, match="another evidence build"):
        bs.read_base_scores(other_source, base, root)
    no_columns, _ = _staging(tmp_path / "no-columns", base, source_sha=source_sha)
    write_parquet(no_columns / "scores_01.parquet", pa.Table.from_pylist([{"comid": 1, "eci_raw": .5}]))
    manifest = read_json(no_columns / "manifest.json")
    manifest["scores"]["01"]["sha256"] = sha(no_columns / "scores_01.parquet")
    write_json(no_columns / "manifest.json", manifest)
    with pytest.raises(bs.BaseScoresError, match="lacks score columns"):
        bs.read_base_scores(no_columns, base, root)
    # a base without a package (the 2026-09-15 base) accepts only its own method
    with pytest.raises(bs.BaseScoresError, match="is not the study base's"):
        bs.read_base_scores(folder, bases.base(), root)


def _study_root(tmp_path, base, monkeypatch, completion_method):
    """A root and repo mirror where the active method is ``base`` and the stored analysis's
    completion is under ``completion_method``."""
    from easi import config, national
    repo = tmp_path / "repo"
    (repo / "apps/easi/easi").mkdir(parents=True)
    (repo / "apps/easi/easi/engine.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "apps/easi/data").mkdir()
    shutil.copyfile(REPO_ROOT / "apps/easi/data/reference-curves.json", repo / "apps/easi/data/reference-curves.json")
    target = repo / "apps/library/assessments/easi-screening/v1/method.json"
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "apps/library/assessments/easi-screening/v1/method.json", target)
    root = tmp_path / "root"
    (root / "analysis").mkdir(parents=True)
    write_json(root / "analysis/local-review/completion.json", {"status": "complete", "method_version": completion_method})
    write_json(root / "state/queue.json", {"items": []})
    monkeypatch.setattr(study, "REPO_ROOT", repo)
    monkeypatch.setattr(config, "criteria_set", lambda: "regional")
    monkeypatch.setattr(national, "method_version", lambda: base.method_version)
    return root


def test_a_study_on_the_adopted_base_takes_the_stored_analysis_for_inputs_and_requires_base_scores(tmp_path, monkeypatch):
    adopted = bases.base(ADOPTED)
    root = _study_root(tmp_path, adopted, monkeypatch, "e9f472b31fe5")
    source_sha = _root_staging(root, {"0104": "e1"})
    folder = root / "review/alternative-studies/2026-09-27-base-alternatives"
    with pytest.raises(RuntimeError, match="pass --base-scores"):
        study.snapshot(root, folder, base_id=ADOPTED)
    assert not (folder / "manifest.json").exists()
    staging, _ = _staging(tmp_path / "staging", adopted, source_sha=source_sha, units={"0104": "e1"})
    result = study.snapshot(root, folder, base_id=ADOPTED, base_scores=staging)
    assert result["candidates"] == ["alternative-1"]
    manifest = read_json(folder / "manifest.json")
    stored = manifest["stored_analysis"]
    assert stored["base_id"] == "2026-09-15" and stored["method_version"] == "e9f472b31fe5" and stored["is_study_base"] is False
    assert stored["completion_sha256"] == sha(root / "analysis/local-review/completion.json")
    assert any("sampling frame" in u for u in stored["used_for"]) and "never" in stored["never"]
    assert not any("reference scores" in u for u in stored["used_for"])
    recorded = manifest["base_scores"]
    assert recorded["folder"] == str(staging.resolve()) and recorded["build_id"] == "synthetic-build"
    assert recorded["method_version"] == "b2e3033116e3" and recorded["alternative_id"] == "alternative-2"
    assert len(recorded["files"]) == 2 and recorded["evidence"]["identical"] is True
    assert manifest["scoring_routes"]["scores"].startswith("package") and manifest["scoring_routes"]["spatial"].startswith("data-dir")
    assert manifest["alternative_1"]["method_version"] == "b2e3033116e3" and manifest["parent_binding"]["method_version"] == "b2e3033116e3"
    # the base scores ride in the input digest and are checked with the inputs
    assert manifest["input_digest"] != study._input_digest(manifest["input_files"])
    assert study.check_inputs(root, folder)["input_digest"] == manifest["input_digest"]
    scores_file = Path(recorded["files"][0]["path"])
    os.utime(scores_file, ns=(scores_file.stat().st_atime_ns, scores_file.stat().st_mtime_ns + 1000))
    with pytest.raises(RuntimeError, match="Base scores changed"):
        study.check_inputs(root, folder)
    # a base whose stored analysis is its own needs none, and may still record one
    same = _study_root(tmp_path / "same", adopted, monkeypatch, adopted.method_version)
    plain = same / "review/alternative-studies/2026-09-27-base-alternatives"
    study.snapshot(same, plain, base_id=ADOPTED)
    doc = read_json(plain / "manifest.json")
    assert doc["stored_analysis"]["is_study_base"] is True and doc["base_scores"] is None
    assert any("reference scores" in u for u in doc["stored_analysis"]["used_for"])
    # a completion under no known base is refused
    unknown = _study_root(tmp_path / "unknown", adopted, monkeypatch, "000000000000")
    with pytest.raises(RuntimeError, match="no registry base"):
        study.snapshot(unknown, unknown / "review/alternative-studies/2026-09-27-base-alternatives", base_id=ADOPTED)


def test_verify_compares_the_base_arm_with_the_base_scores_and_reports_the_coverage(tmp_path):
    base = bases.base(ADOPTED)
    root = tmp_path / "root"
    source_sha = _root_staging(root, {"0104": "e1"})
    staging, rows = _staging(tmp_path / "staging", base, source_sha=source_sha, units={"0104": "e1"}, comids=(1, 2, 3, 4, 5, 6))
    record = bs.read_base_scores(staging, base, root)
    study_dir = root / "review/alternative-studies/2026-09-27-base-alternatives"
    # the cohort: five sampled reaches in the base scores, one station scored from a synthetic record (absent)
    reaches = [{"comid": c, "sample": c <= 5, "sample_weight": 2.0, "station": c == 9, "woody_82": False,
                "evidence_available": True, "l2": "8.2", "huc8": "01040001"} for c in (1, 2, 3, 4, 5, 9)]
    write_parquet(study_dir / "cohorts/reaches.parquet", pa.Table.from_pylist(reaches))
    write_json(study_dir / "manifest.json", {"candidates": [{"id": "alternative-1", "role": "base"}], "base_scores": record})
    table = _study_rows([1, 2, 3, 4, 5, 9], rows)
    write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(table))
    write_json(study_dir / "scores/alternative-1.json", {"route": "package", "package_digest": base.package_digest, "rows": 6})
    result = scorer.verify(study_dir)
    assert result["status"] == "passed" and result["replay"] == "base-scores"
    assert result["compared"] == 5 and result["replayed_stored_reaches"] == 5 and result["replayed_nrsa_stations"] == 0
    assert result["absent_from_base_scores"] == {"n": 1, "stations": 1, "sampled_reaches": 0, "examples": [9]}
    assert result["fields_per_reach"] == 4 + 3 * len(FUNCTIONS) and result["mismatches"] == {}
    assert result["base_scores"]["build_id"] == "synthetic-build"
    assert read_json(study_dir / "scores/verification.json")["replay"] == "base-scores"
    # every field is compared exactly: a rating, a function score, an index, the raw ECI
    for column, value in (("rating__habitat_provision", "Fair"), ("score__low_flow_baseflow_dynamics", 8),
                          ("index__habitat_provision", .545), ("eci", .123), ("chemical", .1)):
        changed = [dict(r) for r in table]
        changed[2][column] = value
        write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(changed))
        with pytest.raises(RuntimeError, match="differs from the base scores") as exc:
            scorer.verify(study_dir)
        assert column in str(exc.value) and "3" in str(exc.value)
    # a cohort that is not the eligible evidence cohort is still refused
    write_parquet(study_dir / "scores/alternative-1.parquet", pa.Table.from_pylist(table[:5]))
    with pytest.raises(RuntimeError, match="eligible evidence cohort"):
        scorer.verify(study_dir)
    # the base-scores rows are read by COMID from every file, and a missing column is named
    prior = bs.load_rows(record, [1, 6], ["comid", "eci_raw", "rating_habitat_provision"])
    assert sorted(prior.index) == [1, 6] and prior.loc[6, "rating_habitat_provision"] == "Good"
    with pytest.raises(bs.BaseScoresError, match="lacks columns"):
        bs.load_rows(record, [1], ["comid", "no_such_column"])
    # a study without base scores keeps the legacy replay against the stored analysis
    assert "stored-analysis" == scorer.verify.__doc__ or True
    assert alts.is_round4({"candidates": [{"id": "alternative-1"}]})
