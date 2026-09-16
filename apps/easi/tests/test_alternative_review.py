"""Stored alternative comparisons never score, change defaults or expose paths."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from urllib.parse import unquote, urlsplit

import pytest
from starlette.applications import Starlette

import alternative_review as alternatives
import local_review


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal(study):
    folder, manifest, summary = study["folder"], study["manifest"], study["summary"]
    write_json(folder / "manifest.json", manifest)
    write_json(folder / "summary.json", summary)
    outputs = {path: digest(folder / path) for path in alternatives.DOWNLOADS.values() if (folder / path).is_file()}
    completion = {"schema_version": 1, "study_id": manifest["study_id"], "status": "complete",
                  "input_digest": manifest["input_digest"], "parent_binding": copy.deepcopy(manifest["parent_binding"]),
                  "output_hashes": outputs}
    if "source_digest" in manifest:
        completion["source_digest"] = manifest["source_digest"]
    write_json(folder / "completion.json", completion)
    return completion


@pytest.fixture
def study(tmp_path):
    root, data = tmp_path / "national", tmp_path / "workspace/apps/easi/data"
    sid = "2026-09-15-four-alternatives"
    folder = root / "review/alternative-studies" / sid
    curve = {"points": [[0, 0], [50, .7], [100, 1]], "n": 117, "nMembers": 118,
             "q25": 20, "q50": 50, "q75": 80, "x39": 27.85, "x69": 49.28,
             "status": "usable", "panelTier": "strict", "screen": "least-disturbed-v1"}
    keys = ["national", "8.2", *[f"s{i}" for i in range(60)]]
    artifact = {"schemaVersion": 1, "sets": {"corridor-woody": {"quantity": "woody_wsrp100", "stratifier": "l2",
                "higherIsBetter": True, "curves": {key: curve for key in keys}}}}
    write_json(data / "reference-curves.json", artifact)
    for aid, count in alternatives.ALTERNATIVES.items():
        candidate = copy.deepcopy(artifact)
        candidate["sets"]["corridor-woody"]["curves"] = {
            key: curve for key in (keys if aid == "alternative-1" else [k for k in keys if k != "8.2"] if aid == "alternative-4" else keys[:count])}
        write_json(folder / alternatives.DOWNLOADS[f"{aid}-curves"], candidate)
    parent_path = root / "analysis/local-review/completion.json"
    parent = {"status": "complete", "method_version": "current-method", "source_commit": "captured-parent-source",
              "frozen_artifact_sha256": digest(data / "reference-curves.json")}
    write_json(parent_path, parent)
    values_meta = root / "analysis/values_meta.json"
    write_json(values_meta, {"method_version": "current-method", "built_at": "2026-09-15"})
    inputs = [{"path": str(p.relative_to(root)), "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns,
               "sha256": digest(p)} for p in (values_meta, parent_path)]
    binding = {"completion_sha256": digest(parent_path), "method_version": "current-method",
               "source_commit": "captured-parent-source", "frozen_sha256": digest(data / "reference-curves.json")}
    manifest = {"schema_version": 1, "study_id": sid, "status": "complete", "created_at": "2026-09-15T23:00:00Z",
                "input_digest": "current-input-digest", "input_files": inputs, "parent_binding": binding,
                "alternative_1": {"method_version": "current-method", "source_commit": "captured-parent-source", "frozen_sha": binding["frozen_sha256"]},
                "alternatives": [{"id": aid, "label": aid, "curve_count": count, "changes": ["Stored study comparison"]}
                                 for aid, count in alternatives.ALTERNATIVES.items()]}
    rows = []
    for aid in alternatives.ALTERNATIVES:
        rows.append({"id": aid, "coverage": {"population_n": 1357265, "rated_n": 1332456, "missing_n": 24809, "fallback_n": 3},
                     "differences": {"changed_curves": 1, "changed_ratings_n": 200, "changed_ratings_share": .02, "eci_mean": .625, "eci_mean_delta": -.001},
                     "field_agreement": [{"function": "Thermal", "target": "bent_mmi", "statistic": "AUC", "region": "US",
                         "cohort": "same paired stations", "reference": .6, "alternative": .61, "delta": .01, "ci_low": -.01, "ci_high": .03,
                         "n": 2066, "n_huc8": 117, "boot_requested": 200, "boot_valid": 200,
                         "noninferiority_margin": .01, "noninferiority_status": "inconclusive", "interpretation": "Paired diagnostic"}],
                     "transitions": [{"label": "Stratified sample", "design": "weighted-sample", "sample_n": 100000,
                         "population_n": 1357265, "weighted": True, "rows": [{"from": "Good", "to": "Fair", "n": 30.5, "share": .01}]},
                         {"label": "Every available 8.2 reach", "design": "full-l2-8.2", "sample_n": 74686,
                          "population_n": 74686, "weighted": False, "rows": [{"from": "Good", "to": "Fair", "n": 30, "share": .0004}]}],
                     "curve_uncertainty": [{"set_id": "corridor-woody", "key": "8.2", "x39_lo": 9.44, "x39_hi": 36.69,
                         "mean_flip": .14, "n_members": 117, "n_clusters": 118, "n_population": 74686, "n_valid": 200,
                         "n_requested": 200, "fit_linkage": "canonical_match"}],
                     "low_flow": {"note": "No new raw-flow bands", "rows": [{"measure": "CV", "reference": .4, "alternative": .5, "n": 20}]},
                     "agriculture": {"note": "Shared evidence", "rows": []}, "model": {"note": "Model and fallback", "rows": []},
                     "notes": ["Study-only result"]})
    summary = {"schema_version": 1, "study_id": sid, "input_digest": "current-input-digest", "reference_id": "alternative-1",
               "alternatives": rows, "limitations": ["No ecological validation claim"]}
    write_json(folder / "traces/alternative-4.json", {"trace": "stored only"})
    csv = folder / "results/field_agreement.csv"
    csv.parent.mkdir(parents=True, exist_ok=True)
    csv.write_text("alternative_id,delta,n\nalternative-4,0.01,2066\n", encoding="utf-8")
    result = {"root": root, "data": data, "folder": folder, "manifest": manifest, "summary": summary, "id": sid}
    seal(result)
    return result


def load(study):
    return alternatives.load_study(study["root"], study["data"], "current-method", study["id"])


def render(study, **kwargs):
    return alternatives.render_page(study["root"], study["data"], "current-method", study["id"], **kwargs)


def archive(study):
    parent = study["root"] / "analysis/local-review/completion.json"
    archived_parent = study["folder"] / "snapshot/analysis/local-review/completion.json"
    archived_parent.parent.mkdir(parents=True)
    archived_parent.write_bytes(parent.read_bytes())
    promotion = {"operation": "promote-frozen-alternative-2", "source_study_id": study["id"],
                 "source_completion_sha256": digest(study["folder"] / "completion.json"),
                 "preserved_alternative_1": {"method_version": "current-method",
                     "curves_sha256": study["manifest"]["alternative_1"]["frozen_sha"]}}
    write_json(study["data"] / "source/alternative-2-promotion.json", promotion)


def test_promoted_archive_remains_readable_after_live_method_and_inputs_change(study):
    archive(study)
    write_json(study["data"] / "reference-curves.json", {"new": "Alternative 2"})
    write_json(study["root"] / "analysis/local-review/completion.json", {"new": "build"})
    write_json(study["root"] / "analysis/values_meta.json", {"new": "analysis"})
    loaded = alternatives.load_study(study["root"], study["data"], "adopted-method", study["id"])
    assert loaded["archived"] is True
    page = render(study)
    assert "Archived study; displayed output hashes and sealed completion verified" in page
    assert "current input binding verified" not in page
    assert "historical evidence" in page


@pytest.mark.parametrize("relative", ["completion.json", "manifest.json", "snapshot/analysis/local-review/completion.json",
                                    "candidates/alternative-1/app-data/reference-curves.json", "summary.json"])
def test_promoted_archive_rejects_modified_receipts_and_displayed_artifacts(study, relative):
    archive(study)
    path = study["folder"] / relative
    value = json.loads(path.read_text())
    value["modified"] = True
    write_json(path, value)
    with pytest.raises(alternatives.StudyUnavailable):
        load(study)


def test_completed_paired_comparison_keeps_reference_and_files_unchanged(study):
    before = {p: (digest(p), p.stat().st_mtime_ns) for p in study["root"].rglob("*") if p.is_file()}
    page = render(study, alternative="alternative-4", selected="corridor-woody|8.2")
    assert "Alternative study verified" in page and "The owner adopted Alternative 2 for the application" in page
    assert "13 / 8 / 3" in page and "No curve for 8.2; stored national fallback shown" in page
    assert "Weighted stratified sample, target 100,000 reaches" in page and "Full Level II 8.2 population" in page
    assert "1,357,265" in page and "74,686" in page and "100,000" in page
    assert "Change CI lower" in page and "Paired observations" in page and "same paired stations" in page
    assert "HUC8 clusters" in page and "inconclusive" in page and "canonical_match" in page
    assert "Low-flow evidence and alternatives" in page and "Agriculture evidence" in page and "Biological model" in page
    assert "alternative-4-trace" in page and "field-agreement" in page
    assert "Verified complete at" not in page and chr(8212) not in page
    assert before == {p: (digest(p), p.stat().st_mtime_ns) for p in study["root"].rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_incomplete_study_does_not_expose_summary(study, status):
    study["manifest"]["status"] = status
    study["summary"]["limitations"] = ["UNPUBLISHED_RESULT"]
    seal(study)
    page = render(study)
    assert "Study results pending" in page and status in page and "UNPUBLISHED_RESULT" not in page
    assert "Alternative study verified" not in page


@pytest.mark.parametrize("area", ["completion_digest", "summary_digest", "output_hash", "parent_source", "parent_method", "reference_sha", "input_stamp", "input_hash"])
def test_stale_or_unbound_outputs_are_never_verified(study, area):
    folder = study["folder"]
    if area == "completion_digest":
        completion = json.loads((folder / "completion.json").read_text())
        completion["input_digest"] = "different"
        write_json(folder / "completion.json", completion)
    elif area == "summary_digest":
        study["summary"]["input_digest"] = "different"
        seal(study)
    elif area == "output_hash":
        study["summary"]["limitations"].append("different")
        write_json(folder / "summary.json", study["summary"])
    elif area in {"parent_source", "parent_method"}:
        field = "source_commit" if area == "parent_source" else "method_version"
        study["manifest"]["parent_binding"][field] = "different"
        seal(study)
    elif area == "reference_sha":
        study["manifest"]["alternative_1"]["frozen_sha"] = "different"
        seal(study)
    else:
        path = study["root"] / "analysis/values_meta.json"
        old = path.stat()
        path.write_bytes(path.read_bytes().replace(b"current-method", b"changed-method"))
        if area == "input_hash":
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert "Alternative study verified" not in render(study)
    with pytest.raises(alternatives.StudyUnavailable):
        load(study)


def test_candidate_count_is_checked_after_output_hashes(study):
    path = study["folder"] / alternatives.DOWNLOADS["alternative-2-curves"]
    artifact = json.loads(path.read_text())
    del artifact["sets"]["corridor-woody"]["curves"]["8.2"]
    write_json(path, artifact)
    seal(study)
    with pytest.raises(alternatives.StudyUnavailable, match="curve count"):
        load(study)


def test_a1_artifact_must_remain_byte_identical_to_main_app(study):
    path = study["folder"] / alternatives.DOWNLOADS["alternative-1-curves"]
    artifact = json.loads(path.read_text())
    artifact["sets"]["corridor-woody"]["curves"]["national"]["n"] = 118
    write_json(path, artifact)
    seal(study)
    with pytest.raises(alternatives.StudyUnavailable, match="Alternative 1 differs"):
        load(study)


def test_large_inputs_are_stat_checked_without_reading_payload(study, monkeypatch):
    path = study["root"] / "analysis/values.parquet"
    with path.open("wb") as stream:
        stream.truncate(alternatives.MAX_JSON_BYTES + 1)
    study["manifest"]["input_files"].append({"path": "analysis/values.parquet", "size": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns, "sha256": "producer-full-hash"})
    seal(study)
    original = type(path).read_bytes
    def guarded(self):
        assert self != path, "Page requests must not read large Parquet payloads"
        return original(self)
    monkeypatch.setattr(type(path), "read_bytes", guarded)
    assert load(study)["summary"]["reference_id"] == "alternative-1"


def test_oversized_json_is_pending(study, monkeypatch):
    monkeypatch.setattr(alternatives, "MAX_JSON_BYTES", 128)
    assert "Study results pending" in render(study)


def test_missing_summary_and_invalid_input_inventory_are_pending(study):
    (study["folder"] / "summary.json").unlink()
    assert "Study results pending" in render(study)
    study["manifest"]["input_files"] = []
    seal(study)
    assert "Study results pending" in render(study)


def test_html_is_escaped_and_optional_sections_are_unavailable(study):
    row = study["summary"]["alternatives"][1]
    row["notes"] = ["<script>alert(1)</script>"]
    row.pop("field_agreement")
    row["coverage"] = {}
    seal(study)
    page = render(study)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "Unavailable" in page and "Pending: no results" in page


@pytest.mark.parametrize("study_id", ["../secret", "..\\secret", "/absolute", "C:/outside", ".", ".."])
def test_study_path_is_enumerated_and_confined(study, study_id):
    with pytest.raises(alternatives.StudyUnavailable):
        alternatives.load_study(study["root"], study["data"], "current-method", study_id)


def test_input_path_escape_is_rejected(study):
    study["manifest"]["input_files"][0]["path"] = "../outside.json"
    seal(study)
    with pytest.raises(alternatives.StudyUnavailable, match="outside"):
        load(study)


def test_empty_study_root_is_pending_and_optional_routes_stay_disabled(tmp_path):
    assert local_review.routes(None) == []
    page = alternatives.render_page(tmp_path, tmp_path, "current-method")
    assert "No local alternative study" in page and "Alternative study verified" not in page


def set_sources(study, rows):
    study["manifest"]["source_files"] = rows
    study["manifest"]["source_digest"] = hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
    seal(study)


def source_row(folder, relative, scope):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original input bytes")
    stamp = path.stat()
    return {"scope": scope, "path": relative, "size": stamp.st_size,
            "mtime_ns": stamp.st_mtime_ns, "sha256": digest(path)}


@pytest.mark.parametrize("scope", ["data", "workspace"])
def test_source_inventory_stat_only_and_actual_changes_hide_completed_results(study, monkeypatch, scope):
    base = study["root"] if scope == "data" else study["data"].parents[2]
    row = source_row(base, "raw/source.bin", scope)
    set_sources(study, [row])
    path = base / row["path"]
    original = type(path).open
    def never_read_source(self, *args, **kwargs):
        assert self != path, "Source freshness must use stat only, even for small source files"
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(type(path), "open", never_read_source)
        assert load(study)["summary"]["reference_id"] == "alternative-1"
    path.write_bytes(b"changed source input bytes")
    page = render(study)
    assert "Study source files have changed" in page and "Alternative study verified" not in page


@pytest.mark.parametrize("scope,path", [("data", "../outside.bin"), ("workspace", "../outside.bin"),
                                         ("other", "source.bin"), ([], "source.bin"), ("data", "D:/outside.bin"),
                                         ("data", "raw/\x00invalid")])
def test_source_inventory_rejects_escaping_paths_and_unknown_scopes(study, scope, path):
    row = {"scope": scope, "path": path, "size": 1, "mtime_ns": 1, "sha256": "a" * 64}
    set_sources(study, [row])
    with pytest.raises(alternatives.StudyUnavailable):
        load(study)


def test_source_inventory_resolves_every_file_again_each_request(study, monkeypatch):
    from collections import Counter
    workspace = study["data"].parents[2]
    rows = [source_row(study["root"], f"raw/source-{i}.bin", "data") for i in range(4)]
    rows += [source_row(workspace, "raw/workspace.bin", "workspace")]
    set_sources(study, rows)
    completion = json.loads((study["folder"] / "completion.json").read_text())
    original = type(workspace).resolve
    calls = Counter()
    def count_resolve(path, *args, **kwargs):
        calls[str(path)] += 1
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(workspace), "resolve", count_resolve)
    for _ in range(2):
        alternatives._source_freshness(study["root"], workspace, study["manifest"], completion)
    assert calls[str(study["root"])] == calls[str(workspace)] == 2
    for row in rows:
        base = study["root"] if row["scope"] == "data" else workspace
        assert calls[str(base / row["path"])] == 2
    path = workspace / rows[-1]["path"]
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    assert path.stat().st_size == rows[-1]["size"]
    with pytest.raises(alternatives.StudyUnavailable, match="source files have changed"):
        alternatives._source_freshness(study["root"], workspace, study["manifest"], completion)


@pytest.mark.parametrize("replacement", ["missing", "directory"])
def test_source_inventory_rejects_missing_and_nonregular_entries(study, replacement):
    row = source_row(study["root"], "raw/source.bin", "data")
    path = study["root"] / row["path"]
    path.unlink()
    if replacement == "directory":
        path.mkdir()
        stamp = path.stat()
        row.update(size=stamp.st_size, mtime_ns=stamp.st_mtime_ns)
    set_sources(study, [row])
    with pytest.raises(alternatives.StudyUnavailable):
        load(study)


def test_source_inventory_rejects_directory_link_escape(study):
    outside = study["root"].parent / "outside-source"
    outside.mkdir()
    target = outside / "source.bin"
    target.write_bytes(b"outside source")
    link = study["root"] / "linked-source"
    if os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    stamp = target.stat()
    set_sources(study, [{"scope": "data", "path": "linked-source/source.bin", "size": stamp.st_size,
                         "mtime_ns": stamp.st_mtime_ns, "sha256": digest(target)}])
    with pytest.raises(alternatives.StudyUnavailable, match="outside"):
        load(study)


def test_source_digest_must_bind_manifest_receipt_and_inventory(study):
    row = source_row(study["root"], "raw/source.bin", "data")
    set_sources(study, [row])
    completion = json.loads((study["folder"] / "completion.json").read_text())
    completion["source_digest"] = "b" * 64
    write_json(study["folder"] / "completion.json", completion)
    with pytest.raises(alternatives.StudyUnavailable, match="source digests"):
        load(study)
    set_sources(study, [row])
    study["manifest"]["source_files"][0]["sha256"] = "c" * 64
    seal(study)
    with pytest.raises(alternatives.StudyUnavailable, match="source digests"):
        load(study)


def test_source_inventory_has_a_bounded_size(study):
    row = {"scope": "data", "path": "raw/source.bin", "size": 1, "mtime_ns": 1, "sha256": "a" * 64}
    set_sources(study, [row] * 10001)
    with pytest.raises(alternatives.StudyUnavailable, match="allowed size"):
        load(study)


def test_recommendation_and_acquisition_use_bounded_summary_and_qualified_counts(study):
    study["summary"]["recommendation"] = {"recommended": "alternative-1", "decision": "Evidence remains inconclusive",
        "criteria": "Paired delta lower bounds and declared margin", "candidates": [
            {"alternative_id": "alternative-4", "curve_count": 61, "eligible": False,
             "aggregate_noninferiority": False, "availability_unchanged": True, "spatial_support_available": False,
             "deterioration_findings": [], "spatial_conflicts": []}]}
    study["summary"]["acquisition"] = {"status": "partial", "matched_station_keys": 42, "unmatched_station_keys": 3000,
        "unique_matched_gages": 17, "gages_with_daily_values": 16, "daily_observations": 120000,
        "training_independence": "unknown", "failures": ["<script>unavailable</script>"],
        "gage_diagnostics": {"status": "complete", "gage_count": 16, "comparison_pairs": 17,
            "signed_associations": [{"measure": "monthly_cv", "statistic": "signed_spearman", "rho": -.02,
                                     "n": 17, "interpretation": "descriptive exact-gage pairs"}]}}
    seal(study)
    page = render(study)
    assert "Study recommendation" in page and "Evidence remains inconclusive" in page
    assert "Held-out support" in page and "This historical recommendation is unchanged" in page
    assert "Station keys with an exact gage match" in page and "120,000" in page
    assert "Paired gage records" in page and "descriptive exact-gage pairs" in page
    assert "<script>unavailable</script>" not in page and "&lt;script&gt;unavailable" in page


def test_recommendation_explains_rejection_and_keeps_full_supporting_findings():
    common = {"function": "light_thermal_regime", "target": "bent_mmi", "region": "8.2",
              "cohort": "latest_visit1", "reference": .62, "alternative": .59, "delta": -.03,
              "ci_low": -.05, "ci_high": -.01, "n": 123, "n_huc8": 45,
              "boot_valid": 1000, "boot_requested": 1000}
    deterioration = {**common, "statistic": "auc", "comparison_design": "spatial_refit",
                     "reason": "Supported regional AUC decline"}
    findings = [{**common, "statistic": "signed_spearman", "comparison_design": "frozen",
                 "reason": "Correlation decline needs review"},
                {**common, "statistic": "weighted_kappa", "comparison_design": "spatial_refit",
                 "reason": "Class-agreement decline needs review"}]
    page = alternatives._recommendation({"recommended": "alternative-1", "decision": "Retain Alternative 1",
        "candidates": [{"alternative_id": "alternative-2", "curve_count": 34, "eligible": False,
                        "aggregate_noninferiority": True, "availability_unchanged": True,
                        "spatial_support_available": True, "deterioration_findings": [deterioration],
                        "spatial_conflicts": [deterioration], "unresolved_review_findings": findings,
                        "reasons": ["Review unresolved correlation and class agreement", "<script>unsafe</script>"]}]})
    assert '<td>alternative-2</td><td>34</td><td>No</td><td>Yes</td><td>Yes</td><td>Yes</td><td>1</td><td>1</td><td>2</td>' in page
    assert "Review unresolved correlation and class agreement" in page
    assert '<details><summary>alternative-2: supporting findings (3 rows)</summary>' in page
    assert "Overlapping exploratory findings" in page and "do not represent independent tests" in page
    assert "Spatial conflicts are a subset" in page
    for text in ("Comparison design", "Review reason", "spatial_refit", "frozen", "latest_visit1", "8.2",
                 "Change CI lower", "Change CI upper", "-0.05", "-0.01", "123", "45", "1,000",
                 "Correlation decline needs review", "Class-agreement decline needs review", "Supported regional AUC decline"):
        assert text in page
    assert page.count("Supported regional AUC decline") == 1
    assert "<script>unsafe</script>" not in page and "&lt;script&gt;unsafe" in page


def test_difference_count_describes_curve_entries_without_changing_value(study):
    row = next(row for row in study["summary"]["alternatives"] if row["id"] == "alternative-2")
    row["differences"]["changed_curves"] = 82
    seal(study)
    page = render(study)
    assert '<td>Curve entries added, removed or replaced relative to Alternative 1</td><td>82</td>' in page
    assert "Curves changed relative to Alternative 1" not in page


def test_complete_study_distinguishes_unavailable_curve_uncertainty_from_pending(study):
    unavailable = render(study, selected="corridor-woody|s0")
    assert "Reference-resampling uncertainty is unavailable for this selected curve." in unavailable
    assert "Pending: no results available yet." not in unavailable
    supplied = render(study, selected="corridor-woody|8.2")
    assert "canonical_match" in supplied and "x39 CI lower" in supplied and "9.44" in supplied
    assert "Reference-resampling uncertainty is unavailable for this selected curve." not in supplied


def test_nars9_selector_displays_selected_candidate_curve_without_crosswalk(study):
    path = study["folder"] / alternatives.DOWNLOADS["alternative-2-curves"]
    artifact = json.loads(path.read_text())
    definition = artifact["sets"]["corridor-woody"]
    definition["stratifier"] = "nars9"
    definition["curves"]["NAP"] = definition["curves"].pop("8.2")
    definition["curves"]["NAP"]["points"] = [[0, 0], [20, .7], [100, 1]]
    write_json(path, artifact)
    seal(study)
    page = render(study, alternative="alternative-2", selected="corridor-woody|8.2", candidate_stratum="NAP")
    assert '<option value="NAP" selected>NARS-9 NAP</option>' in page
    assert "Alternative 1: Level II 8.2" in page
    assert "Alternative 2: NARS-9 NAP. Stored curve: NAP" in page
    card = page.split("Alternative 2: NARS-9 NAP. Stored curve: NAP", 1)[1].split("</section>", 1)[0]
    assert "Fitted knot: 20, 0.7" in card and "Fitted knot: 50, 0.7" not in card
    assert "not a one-to-one geographic match" in page and "Slope class NAP" not in page
    fallback = render(study, alternative="alternative-2", selected="corridor-woody|8.2", candidate_stratum="outside")
    assert "Alternative 2: National reference" in fallback


def test_raw_diagnostics_show_their_own_cohorts_controls_and_calibration_quantities():
    section = {"rows": [
        {"subject": "baseflow_index", "measure": "raw_signed_spearman", "reference_subject": "negative_monthly_flow_cv",
         "target": "dry_bed", "cohort": "retrospective_2324", "region": "US", "reference": .1, "alternative": .2, "n": 40},
        {"subject": "shared_agriculture", "measure": "paired_function_dependence", "cohort": "latest_visit1",
         "class_agreement": .97, "correlation": .94, "n": 50},
        {"subject": "bed", "measure": "conditional_field_association", "control_function": "catchment_hydrology",
         "control_rating": "Fair", "target": "bent_mmi", "reference": .6, "alternative": .6, "n": 20},
        {"subject": "raw_probability", "measure": "calibration_bin", "bin_low": .2, "bin_high": .4,
         "reference": .31, "alternative": .45, "n": 30},
        {"subject": "remove_bed", "measure": "eci_field_association_removed_contribution", "removed_function": "bed",
         "reference": .65, "alternative": .63, "n": 50}]}
    page = alternatives._diagnostic("Raw diagnostics", section)
    assert "Alternative 1</th>" not in page and "Selected alternative</th>" not in page
    assert "Reference result" in page and "Comparison result" in page
    for text in ("retrospective_2324", "Field target", "Region", "Held-constant function", "catchment_hydrology", "Fair",
                 "Class agreement (share)", "Function rank correlation", "Probability bin lower", "Probability bin upper",
                 "Mean predicted Good probability", "Observed Good share", "Current weighted ECI", "Removed contribution"):
        assert text in page


def test_loopback_routes_and_enumerated_hashed_downloads(study, monkeypatch):
    from easi import config, national
    monkeypatch.setattr(config, "DATA_DIR", study["data"])
    monkeypatch.setattr(national, "method_version", lambda: "current-method")
    additional = ("spatial-field-agreement", "acquisition-summary", "gage-comparison", "gage-coverage", "woody-stability", "protocol")
    for name in additional:
        path = study["folder"] / alternatives.DOWNLOADS[name]
        if path.suffix == ".json":
            write_json(path, {"synthetic": name})
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic\n" + name + "\n", encoding="utf-8")
    seal(study)
    app = Starlette(routes=local_review.routes(study["root"]))

    async def get(url, host="127.0.0.1"):
        parsed = urlsplit(url)
        messages = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": unquote(parsed.path), "raw_path": parsed.path.encode(),
                 "query_string": parsed.query.encode(), "root_path": "", "headers": [],
                 "client": (host, 123), "server": ("127.0.0.1", 8000)}
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await app(scope, receive, send)
        return (next(m["status"] for m in messages if m["type"] == "http.response.start"),
                b"".join(m.get("body", b"") for m in messages).decode())

    async def check():
        base = "/local-review/alternatives/"
        url = base + "download/" + study["id"] + "/"
        assert (await get(base))[0] == 200
        assert "Alternative study verified" in (await get(base))[1]
        assert "stored only" in (await get(url + "alternative-4-trace"))[1]
        assert "alternative_id,delta,n" in (await get(url + "field-agreement"))[1]
        for name in additional:
            status, body = await get(url + name)
            assert status == 200 and name in body
        for name in ("unknown", "completion", "../../completion.json", "%2e%2e%5ccompletion.json"):
            assert (await get(url + name))[0] == 404
        assert (await get(base, "192.0.2.1"))[0] == 403
        assert (await get(url + "summary", "192.0.2.1"))[0] == 403
        (study["folder"] / "results/field_agreement.csv").write_text("changed", encoding="utf-8")
        assert (await get(url + "field-agreement"))[0] == 404
    asyncio.run(check())
