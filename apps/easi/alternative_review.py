"""Local, read-only comparison of completed alternative studies.

The study producer owns every calculation. Requests read bounded summaries and
stored curves, check their completed-study binding, and never score evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from stat import S_ISREG

import local_review as review

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_INPUTS = 256
MAX_SOURCE_FILES = 10000
ALTERNATIVES = {"alternative-1": 62, "alternative-2": 34, "alternative-3": 7, "alternative-4": 61}
STUDY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\Z")
DOWNLOADS = {
    "manifest": "manifest.json", "summary": "summary.json",
    "field-agreement": "results/field_agreement.csv",
    "field-lowflow": "results/field_lowflow.csv",
    "field-model": "results/field_model.csv",
    "field-agriculture": "results/field_agriculture.csv",
    "field-summary": "results/field_summary.json",
    "spatial-field-agreement": "spatial/results/field_agreement.csv",
    "acquisition-summary": "acquisition/summary.json",
    "gage-comparison": "acquisition/gage-comparison.csv",
    "gage-coverage": "acquisition/annual-record-coverage.csv",
    "woody-stability": "woody-stability/result.json",
    "protocol": "protocol.json",
    **{f"{aid}-curves": f"candidates/{aid}/app-data/reference-curves.json" for aid in ALTERNATIVES},
    **{f"{aid}-trace": f"traces/{aid}.json" for aid in ALTERNATIVES},
}
REQUIRED_OUTPUTS = ["summary.json", *(DOWNLOADS[f"{aid}-curves"] for aid in ALTERNATIVES)]


class StudyUnavailable(ValueError):
    """The producer has not supplied a complete, current, readable study."""


def _inside(folder: Path, relative: str) -> Path:
    """Resolve only a relative path contained in the intended local folder."""
    return _inside_resolved(folder.resolve(), relative)


def _inside_resolved(folder: Path, relative: str) -> Path:
    """Resolve every target against a root already resolved in this request."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise StudyUnavailable("An input path is invalid.")
    try:
        path = (folder / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StudyUnavailable("An input path is unavailable or invalid.") from exc
    if not path.is_relative_to(folder) or path == folder:
        raise StudyUnavailable("An input path is outside its study or data folder.")
    return path


def _read_json(path: Path) -> tuple[dict, bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise StudyUnavailable("A study JSON exceeds the local review size limit.")
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise StudyUnavailable("A required study file is unavailable or unreadable.") from exc
    if not isinstance(value, dict):
        raise StudyUnavailable("A study file has an invalid schema.")
    return value, raw


def _study_folder(root: Path, study_id: str) -> Path:
    if not STUDY_ID.fullmatch(study_id or "") or study_id in {".", ".."}:
        raise StudyUnavailable("Select a local study from the list.")
    folder = _inside(root, "review/alternative-studies")
    return _inside(folder, study_id)


def studies(root: Path) -> list[dict]:
    """Read at most 100 small manifests, without loading result tables."""
    try:
        folder = _inside(root, "review/alternative-studies")
        paths = sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)
    except (OSError, StudyUnavailable):
        return []
    found = []
    for path in paths[:100]:
        try:
            if not path.is_dir():
                continue
            manifest, _ = _read_json(_study_folder(root, path.name) / "manifest.json")
            if manifest.get("schema_version") == 1 and manifest.get("study_id") == path.name:
                found.append(manifest)
        except StudyUnavailable:
            continue
    return found


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_hash(outputs: dict, relative: str) -> str:
    value = outputs.get(relative)
    if isinstance(value, dict):
        value = value.get("sha256")
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise StudyUnavailable("A displayed output lacks a completed-study hash.")
    return value


def _input_freshness(root: Path, inputs) -> None:
    if not isinstance(inputs, list) or not inputs or len(inputs) > MAX_INPUTS:
        raise StudyUnavailable("The study input inventory is absent or invalid.")
    for item in inputs:
        if not isinstance(item, dict):
            raise StudyUnavailable("The study input inventory is invalid.")
        path = _inside(root, item.get("path"))
        try:
            stamp = path.stat()
            if not path.is_file() or stamp.st_size != item.get("size") or stamp.st_mtime_ns != item.get("mtime_ns"):
                raise StudyUnavailable("Study inputs have changed; refreshed results are pending.")
            # Giant Parquet inputs are bound by the producer's full hash and a
            # current stamp, not read into memory on each page request.
            if item.get("sha256") and stamp.st_size <= MAX_JSON_BYTES:
                if _sha(path.read_bytes()) != item["sha256"]:
                    raise StudyUnavailable("A study input hash has changed; refreshed results are pending.")
        except OSError as exc:
            raise StudyUnavailable("A study input is unavailable.") from exc


def _source_freshness(root: Path, workspace: Path, manifest: dict, completion: dict) -> None:
    """Bind the producer's source inventory, checking stamps without payload reads."""
    if "source_files" not in manifest:
        if "source_digest" in manifest or "source_digest" in completion:
            raise StudyUnavailable("The study source inventory is absent.")
        return  # Older completed studies do not have this optional inventory.
    rows = manifest["source_files"]
    if not isinstance(rows, list) or len(rows) > MAX_SOURCE_FILES:
        raise StudyUnavailable("The study source inventory exceeds its allowed size or is invalid.")
    try:
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise StudyUnavailable("The study source inventory is invalid.") from exc
    digest = _sha(canonical)
    if manifest.get("source_digest") != digest or completion.get("source_digest") != digest:
        raise StudyUnavailable("Study source digests do not agree; refreshed results are pending.")
    try:
        folders = {"data": root.resolve(), "workspace": workspace.resolve()}
    except (OSError, RuntimeError) as exc:
        raise StudyUnavailable("A study source folder is unavailable.") from exc
    for row in rows:
        if not isinstance(row, dict) or row.get("scope") not in ("data", "workspace"):
            raise StudyUnavailable("A study source scope is invalid.")
        if (type(row.get("size")) is not int or row["size"] < 0 or type(row.get("mtime_ns")) is not int
                or not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])):
            raise StudyUnavailable("A study source stamp or producer hash is invalid.")
        path = _inside_resolved(folders[row["scope"]], row.get("path"))
        try:
            stamp = path.stat()
            if not S_ISREG(stamp.st_mode) or stamp.st_size != row["size"] or stamp.st_mtime_ns != row["mtime_ns"]:
                raise StudyUnavailable("Study source files have changed; refreshed results are pending.")
        except OSError as exc:
            raise StudyUnavailable("A study source file is unavailable.") from exc


def load_study(root: Path, data_dir: Path, method: str, study_id: str) -> dict:
    """Verify current parent/input binding before exposing completed results."""
    folder = _study_folder(root, study_id)
    manifest, manifest_raw = _read_json(_inside(folder, "manifest.json"))
    if manifest.get("schema_version") != 1 or manifest.get("study_id") != study_id:
        raise StudyUnavailable("The selected study manifest is invalid.")
    if manifest.get("status") != "complete":
        raise StudyUnavailable(f"Study {study_id}: {manifest.get('status') or 'pending'}. Completed results are not available yet.")
    completion, _ = _read_json(_inside(folder, "completion.json"))
    if (completion.get("schema_version") != 1 or completion.get("study_id") != study_id
            or completion.get("status") != "complete"):
        raise StudyUnavailable("The study completion receipt is pending or invalid.")
    digest = manifest.get("input_digest")
    if not isinstance(digest, str) or not digest or completion.get("input_digest") != digest:
        raise StudyUnavailable("Study input digests do not agree; refreshed results are pending.")
    binding = manifest.get("parent_binding")
    if not isinstance(binding, dict) or completion.get("parent_binding") != binding:
        raise StudyUnavailable("The completed study has an invalid parent binding.")
    parent, parent_raw = _read_json(_inside(root, "analysis/local-review/completion.json"))
    _, frozen_raw = _read_json(data_dir / "reference-curves.json")
    frozen_sha = _sha(frozen_raw)
    reference = manifest.get("alternative_1") or {}
    if (parent.get("status") != "complete" or parent.get("method_version") != method
            or binding.get("completion_sha256") != _sha(parent_raw)
            or binding.get("method_version") != method
            or binding.get("source_commit") != parent.get("source_commit")
            or not binding.get("source_commit")
            or binding.get("frozen_sha256") != frozen_sha
            or parent.get("frozen_artifact_sha256") != frozen_sha
            or reference.get("method_version") != method
            or reference.get("source_commit") != binding.get("source_commit")
            or reference.get("frozen_sha") != frozen_sha):
        raise StudyUnavailable("The parent build or Alternative 1 has changed; this study is stale.")
    _input_freshness(root, manifest.get("input_files"))
    _source_freshness(root, data_dir.resolve().parents[2], manifest, completion)
    alternatives = manifest.get("alternatives")
    if (not isinstance(alternatives, list) or len(alternatives) != 4
            or {row.get("id") for row in alternatives if isinstance(row, dict)} != set(ALTERNATIVES)):
        raise StudyUnavailable("The four-alternative study inventory is invalid.")
    outputs = completion.get("output_hashes")
    if not isinstance(outputs, dict):
        raise StudyUnavailable("The completed study output inventory is invalid.")
    decoded = {}
    for relative in REQUIRED_OUTPUTS:
        value, raw = _read_json(_inside(folder, relative))
        if _sha(raw) != _expected_hash(outputs, relative):
            raise StudyUnavailable("A displayed study output differs from its completion hash.")
        decoded[relative] = value
        if relative == DOWNLOADS["alternative-1-curves"] and _sha(raw) != frozen_sha:
            raise StudyUnavailable("Alternative 1 differs from the current frozen artifact.")
    if "manifest.json" in outputs and _sha(manifest_raw) != _expected_hash(outputs, "manifest.json"):
        raise StudyUnavailable("The study manifest differs from its completion hash.")
    artifacts = {}
    for alternative in alternatives:
        aid = alternative["id"]
        artifact = decoded[DOWNLOADS[f"{aid}-curves"]]
        sets = artifact.get("sets")
        if artifact.get("schemaVersion") != 1 or not isinstance(sets, dict):
            raise StudyUnavailable("A candidate curve artifact is invalid.")
        count = sum(len(row.get("curves") or {}) for row in sets.values() if isinstance(row, dict))
        if count != ALTERNATIVES[aid] or alternative.get("curve_count") != count:
            raise StudyUnavailable("A candidate curve count does not match the declared alternative.")
        artifacts[aid] = artifact
    summary = decoded["summary.json"]
    if (summary.get("schema_version") != 1 or summary.get("study_id") != study_id
            or summary.get("input_digest") != digest or summary.get("reference_id") != "alternative-1"):
        raise StudyUnavailable("The summary is not bound to this completed input set and reference.")
    rows = summary.get("alternatives")
    if (not isinstance(rows, list) or len(rows) != 4
            or {row.get("id") for row in rows if isinstance(row, dict)} != set(ALTERNATIVES)):
        raise StudyUnavailable("The completed summary does not contain all four alternatives.")
    return {"folder": folder, "manifest": manifest, "completion": completion,
            "summary": summary, "artifacts": artifacts}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _notes(value) -> str:
    values = value if isinstance(value, list) else [value] if value else []
    return "".join(f"<p>{review._e(item)}</p>" for item in values if isinstance(item, str))


def _value(value, *, share=False, count=False):
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if share:
        try:
            return review._number(100 * float(value), 3) + "%"
        except (ValueError, TypeError):
            return "Unavailable"
    if isinstance(value, (int, float)):
        return review._count(value) if count else review._number(value, 6)
    return str(value)


def _field_table(rows) -> str:
    formatted = []
    for row in _rows(rows):
        formatted.append({key: _value(value, count=key in {"n", "n_huc8", "boot_requested", "boot_valid"})
                          for key, value in row.items()})
    columns = [
        ("function", "Function"), ("target", "Field target"), ("statistic", "Statistic"),
        ("region", "Region"), ("cohort", "Paired cohort"), ("reference", "Alternative 1"),
        ("alternative", "Selected alternative"), ("delta", "Paired change"),
        ("ci_low", "Change CI lower"), ("ci_high", "Change CI upper"), ("n", "Paired observations"),
        ("n_huc8", "HUC8 clusters"), ("boot_valid", "Valid bootstrap draws"),
        ("boot_requested", "Requested draws"), ("noninferiority_margin", "Noninferiority margin"),
        ("noninferiority_status", "Noninferiority result"), ("interpretation", "Interpretation")]
    columns += [(key, label) for key, label in [("comparison_design", "Comparison design"), ("reason", "Review reason")]
                if any(row.get(key) for row in formatted)]
    return review._table(formatted, columns)


def _diagnostic(title: str, section) -> str:
    section = section if isinstance(section, dict) else {}
    groups = {}
    for raw in _rows(section.get("rows")):
        row = dict(raw)
        measure = row.get("measure") or "diagnostic"
        row["reference_subject"] = review._label(row.get("reference_subject"))
        row["comparison_subject"] = review._label(row.get("subject"))
        if measure == "calibration_bin":
            row.update(reference_subject="Mean predicted Good probability", comparison_subject="Observed Good share")
        elif measure.startswith("eci_") and "removed_contribution" in measure:
            row.update(reference_subject="Current weighted ECI", comparison_subject="ECI with the named contribution removed")
        elif measure == "conditional_field_association" or measure.startswith("current_route_"):
            row.update(reference_subject="Current stored score within the stated cohort", comparison_subject="Same stored score; descriptive diagnostic")
        groups.setdefault(measure, []).append(row)
    parts = [f"<section><h2>{review._e(title)}</h2>", _notes(section.get("note")),
             '<p>These diagnostics are shared across the scoring alternatives. Their reference and comparison columns identify '
             'the named predictor, calibration quantity or contribution-removal experiment. They do not indicate a change '
             'to the selected scoring alternative.</p>']
    if not groups:
        parts.append('<p class="muted">No diagnostic results supplied.</p>')
    columns = [("subject", "Subject"), ("target", "Field target"), ("cohort", "Cohort"), ("region", "Region"),
               ("role", "Role"), ("route", "Evidence route"), ("statistic", "Statistic"),
               ("control_function", "Held-constant function"), ("control_rating", "Held-constant rating"),
               ("removed_function", "Removed contribution"), ("bin_low", "Probability bin lower"), ("bin_high", "Probability bin upper"),
               ("reference_subject", "Reference quantity"), ("comparison_subject", "Comparison quantity"),
               ("reference", "Reference result"), ("alternative", "Comparison result"),
               ("delta", "Paired change"), ("ci_low", "Change CI lower"), ("ci_high", "Change CI upper"),
               ("value_ci_low", "Absolute CI lower"), ("value_ci_high", "Absolute CI upper"),
               ("class_agreement", "Class agreement (share)"), ("correlation", "Function rank correlation"),
               ("n", "Paired observations"), ("n_huc8", "HUC8 clusters"),
               ("boot_valid", "Valid bootstrap draws"), ("boot_requested", "Requested draws"),
               ("interpretation", "Interpretation"), ("interpretation_scope", "Interpretation scope")]
    for measure, rows in groups.items():
        visible = [(key, label) for key, label in columns if any(row.get(key) is not None and row.get(key) != "" for row in rows)]
        formatted = [{key: _value(value, count=key in {"n", "n_huc8", "boot_valid", "boot_requested"}) for key, value in row.items()}
                     for row in rows]
        parts.append(f'<h3>{review._e(review._label(measure))}</h3>' + review._table(formatted, visible))
    return ''.join(parts) + '</section>'


def _recommendation(value) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    candidates = _rows(value.get("candidates"))
    rows = [{"alternative": row.get("alternative_id"), "curves": _value(row.get("curve_count"), count=True),
             "eligible": _value(row.get("eligible")), "aggregate": _value(row.get("aggregate_noninferiority")),
             "availability": _value(row.get("availability_unchanged")), "spatial": _value(row.get("spatial_support_available")),
             "deterioration": len(_rows(row.get("deterioration_findings"))), "conflicts": len(_rows(row.get("spatial_conflicts"))),
             "review": len(_rows(row.get("unresolved_review_findings")))}
            for row in candidates]
    details = []
    for row in candidates:
        label = review._e(row.get("alternative_id"))
        details.append(f'<h3>{label}: decision reasons</h3>' + _notes(row.get("reasons")))
        deterioration = _rows(row.get("deterioration_findings"))
        unresolved = _rows(row.get("unresolved_review_findings"))
        if deterioration or unresolved:
            details.append(f'<details><summary>{label}: supporting findings ({len(deterioration) + len(unresolved)} rows)</summary>'
                           '<p>Overlapping exploratory findings can share observations, cohorts and targets. '
                           'These counts do not represent independent tests. Spatial conflicts are a subset of the deterioration findings.</p>')
            if deterioration:
                details.append('<h4>Supported AUC deterioration</h4>' + _field_table(deterioration))
            if unresolved:
                details.append('<h4>Unresolved correlation or class-agreement findings</h4>' + _field_table(unresolved))
            details.append('</details>')
    return ('<section><h2>Study recommendation</h2><p><strong>' + review._e(value.get("decision")) + '</strong></p>'
            '<p>Suggested alternative: ' + review._e(value.get("recommended")) + '. Alternative 1 remains the main app default.</p>'
            + _notes(value.get("criteria")) + review._table(rows, [("alternative", "Alternative"), ("curves", "Curves"),
                ("eligible", "Eligible under declared rule"), ("aggregate", "Aggregate noninferiority"),
                ("availability", "Availability unchanged"), ("spatial", "Held-out support"),
                ("deterioration", "Supported deterioration findings"), ("conflicts", "Spatial conflicts"),
                ("review", "Unresolved correlation or class-agreement findings")]) + ''.join(details)
            + '<p>This stored recommendation supports review. It does not promote a method or change scoring.</p></section>')


def _acquisition(value) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    labels = [("status", "Acquisition status"), ("matched_station_keys", "Station keys with an exact gage match"),
              ("unmatched_station_keys", "Station keys without an exact gage match"), ("unique_matched_gages", "Exact matched gages"),
              ("gages_with_daily_values", "Gages with daily observations"), ("daily_observations", "Cached daily observations"),
              ("sources_available", "Available source downloads"), ("training_era_identifiers", "Training-era survey identifiers"),
              ("training_roster_status", "Model-training roster status"), ("training_independence", "Training independence")]
    rows = [{"item": label, "value": _value(value.get(key), count=key not in {"status", "training_roster_status", "training_independence"})}
            for key, label in labels]
    gage = value.get("gage_diagnostics") or {}
    parts = ['<section><h2>Acquisition and observed-gage diagnostics</h2>', review._table(rows, [("item", "Item"), ("value", "Result")]),
             _notes(value.get("limits"))]
    if value.get("failures"):
        parts += ['<details><summary>Unavailable sources</summary>', _notes(value["failures"]), '</details>']
    if isinstance(gage, dict) and gage:
        parts.append(review._table([{"status": gage.get("status"), "gages": _value(gage.get("gage_count"), count=True),
                                     "pairs": _value(gage.get("comparison_pairs"), count=True)}],
                                    [("status", "Gage analysis status"), ("gages", "Gages analyzed"), ("pairs", "Gage / COMID pairs")]))
        parts.append(review._table([{**row, "rho": _value(row.get("rho")), "n": _value(row.get("n"), count=True)}
                                    for row in _rows(gage.get("signed_associations"))],
                                   [("measure", "Measure"), ("statistic", "Statistic"), ("rho", "Signed rank correlation"),
                                    ("n", "Paired gage records"), ("interpretation", "Interpretation")]))
    parts.append('<p>Exact COMID matches do not establish identical drainage areas, undisturbed flow or independent ecological validation. '
                 'Observed-flow and modeled-flow vintages can differ. Missing gage coverage is not evidence of no nearby gage. '
                 'Annual completeness and source qualifiers remain in the downloadable coverage tables.</p></section>')
    return ''.join(parts)


def _curve(artifact: dict, sid: str, key: str) -> tuple[dict, str]:
    curves = ((artifact.get("sets") or {}).get(sid) or {}).get("curves") or {}
    if key in curves:
        return curves[key], f"Stored curve: {key}"
    if "national" in curves:
        return curves["national"], f"No curve for {key}; stored national fallback shown"
    return {}, f"No curve or national fallback available for {key}"


def _stratum_label(definition: dict, key: str) -> str:
    if key == "national":
        return "National reference"
    label = {"l2": "Level II", "nars9": "NARS-9", "slope_class": "Slope class"}.get(definition.get("stratifier"), "Stratum")
    return f"{label} {key}"


def _download_links(study_id: str, completion: dict, aid: str) -> str:
    outputs = completion.get("output_hashes") or {}
    labels = {"summary": "Summary JSON", "manifest": "Study manifest", f"{aid}-curves": "Selected curve artifact",
              f"{aid}-trace": "Selected uncertainty and scoring trace", "field-agreement": "Full field agreement CSV",
              "field-lowflow": "Low-flow diagnostics CSV", "field-model": "Model diagnostics CSV",
              "field-agriculture": "Agriculture diagnostics CSV", "field-summary": "Field study provenance JSON",
              "spatial-field-agreement": "Watershed-held-out field agreement CSV", "acquisition-summary": "Acquisition summary JSON",
              "gage-comparison": "Observed-gage comparisons CSV", "gage-coverage": "Annual gage coverage CSV",
              "woody-stability": "Woody reference stability JSON", "protocol": "Study protocol JSON"}
    links = [f'<li><a href="download/{review._e(study_id)}/{name}">{review._e(label)}</a></li>'
             for name, label in labels.items() if DOWNLOADS[name] in outputs]
    return '<section><h2>Stored outputs</h2><p>Downloads are limited to completed-study outputs with verified hashes.</p><ul>' + "".join(links) + '</ul></section>'


def render_page(root: Path, data_dir: Path, method: str, study_id="", alternative="alternative-2", selected="", candidate_stratum="") -> str:
    known = studies(root)
    study_id = study_id or (known[0]["study_id"] if known else "")
    alternative = alternative if alternative in ALTERNATIVES else "alternative-2"
    sections = ['<h1>Local alternative studies</h1><p><a href="../">Local review</a> | <a href="../../">Open EASI</a></p>',
        '<p><strong>Alternative 1 remains the main app default.</strong> It has 62 frozen curves and Good / Fair / Poor '
        'scores of 13 / 8 / 3. This page compares stored study outputs against Alternative 1. Selecting an alternative '
        'does not change the app, criteria, weights, evidence or national scores.</p>']
    options = ''.join(f'<option value="{review._e(row["study_id"])}"'
                      f'{" selected" if row["study_id"] == study_id else ""}>'
                      f'{review._e(row["study_id"])} ({review._e(row.get("status", "pending"))})</option>' for row in known)
    alt_options = ''.join(f'<option value="{aid}"{" selected" if aid == alternative else ""}>'
                          f'Alternative {aid[-1]} ({count} curves)</option>' for aid, count in ALTERNATIVES.items())
    sections.append('<section><h2>Select a stored study</h2><form method="get"><label for="study">Study</label> '
                    f'<select id="study" name="study">{options}</select> <label for="alternative">Compare with Alternative 1</label> '
                    f'<select id="alternative" name="alternative">{alt_options}</select> <button type="submit">Show comparison</button></form></section>')
    try:
        study = load_study(root, data_dir, method, study_id)
    except StudyUnavailable as exc:
        message = str(exc) if study_id else "No local alternative study is available yet."
        sections.append(f'<section><h2>Study results pending</h2><p class="status">{review._e(message)}</p>'
                        '<p>No completed summary is displayed until its status, parent build, input inventory and output hashes agree. '
                        'The national rebuild completion label is separate from this study.</p></section>')
        return _document(sections)
    manifest, summary = study["manifest"], study["summary"]
    metadata = next(row for row in manifest["alternatives"] if row["id"] == alternative)
    row = next(row for row in summary["alternatives"] if row["id"] == alternative)
    sections.append('<section><h2>Alternative study verified</h2><p class="verified">Completed study; displayed output hashes '
                    'and current input binding verified.</p>' + review._table([
                        {"item": "Study", "value": study_id}, {"item": "Created", "value": manifest.get("created_at")},
                        {"item": "Fixed reference", "value": "Alternative 1"},
                        {"item": "Reference method", "value": manifest["alternative_1"]["method_version"]},
                        {"item": "Parent source", "value": manifest["parent_binding"]["source_commit"]},
                        {"item": "Input digest", "value": manifest["input_digest"]},
                        {"item": "Selected alternative", "value": metadata.get("label") or alternative},
                    ], [("item", "Item"), ("value", "Value")])
                    + '<p>This independent study status does not reuse the national rebuild badge. Large inputs are checked '
                    'against producer-recorded file stamps; full input hashes belong to the completed producer receipt. '
                    'No scoring, fitting or field-statistic calculation runs on this page.</p></section>')
    sections.append(_recommendation(summary.get("recommendation")))
    sections.append(_acquisition(summary.get("acquisition")))
    differences, coverage = row.get("differences") or {}, row.get("coverage") or {}
    measures = [{"measure": "Stored curves", "value": metadata["curve_count"]}]
    for key, label, source, share, count in [
        ("cohort", "Difference cohort", differences, False, False),
        ("weighted", "Differences use weighted estimates", differences, False, False),
        ("changed_curves", "Curve entries added, removed or replaced relative to Alternative 1", differences, False, True),
        ("changed_ratings_n", "Ratings changed in the reported cohort", differences, False, True),
        ("changed_ratings_share", "Reported rating change share", differences, True, False),
        ("eci_mean", "Reported mean ECI", differences, False, False),
        ("eci_mean_delta", "Mean ECI change from Alternative 1", differences, False, False),
        ("cohort", "Coverage cohort", coverage, False, False),
        ("weighted", "Coverage uses weighted estimates", coverage, False, False),
        ("population_n", "Coverage population", coverage, False, True),
        ("rated_n", "Rated", coverage, False, True), ("missing_n", "Not rated", coverage, False, True),
        ("fallback_n", "Reaches selecting national fallback (including unavailable quantities)", coverage, False, True)]:
        measures.append({"measure": label, "value": _value(source.get(key), share=share, count=count)})
    sections.append('<section><h2>Differences, fallback and coverage</h2>' + _notes(metadata.get("changes"))
                    + _notes(row.get("notes")) + review._table(measures, [("measure", "Measure"), ("value", "Result")]) + '</section>')
    sections.append('<section><h2>Paired field agreement</h2><p>Each row compares both alternatives on the same labeled cohort. '
                    'Confidence bounds describe the paired selected-minus-Alternative-1 change. Counts refer to that row, not '
                    'all national reaches. Missing intervals or insufficient bootstrap support are inconclusive. Noninferiority '
                    'applies only where a declared endpoint and margin are supplied.</p>' + _field_table(row.get("field_agreement")) + '</section>')
    sections.append('<section><h2>Population transitions</h2><p>The weighted stratified sample and the full Level II 8.2 population '
                    'are separate analyses. Sample weights do not turn sample rows into observed full-population transitions.</p>')
    transitions = _rows(row.get("transitions"))
    if not transitions:
        sections.append('<p class="muted">No transition results supplied.</p>')
    for transition in transitions:
        design = transition.get("design")
        label = {"weighted-sample": "Weighted stratified sample, target 100,000 reaches", "full-l2-8.2": "Full Level II 8.2 population"}.get(design, "Other explicitly labeled study cohort")
        sections.append(f'<h3>{review._e(label)}</h3>' + _notes(transition.get("label"))
                        + review._table([{"sample_n": _value(transition.get("sample_n"), count=True),
                            "population_n": _value(transition.get("population_n"), count=True),
                            "weighted": _value(transition.get("weighted"))}],
                            [("sample_n", "Observed rows"), ("population_n", "Represented population"), ("weighted", "Weighted estimate")])
                        + review._table([{**item, "n": _value(item.get("n")), "share": _value(item.get("share"), share=True)}
                                         for item in _rows(transition.get("rows"))],
                                        [("from", "Alternative 1 class"), ("to", "Selected class"), ("n", "Count or weighted estimate"), ("share", "Share")]))
    sections.append('</section>')
    base, candidate = study["artifacts"]["alternative-1"], study["artifacts"][alternative]
    choices = {f"{sid}|{key}": (sid, key) for sid, definition in sorted(base.get("sets", {}).items())
               for key in sorted((definition.get("curves") or {}), key=lambda key: (key != "national", key))}
    selected = selected if selected in choices else next(iter(choices), "")
    curve_options = ''.join(f'<option value="{review._e(value)}"{" selected" if value == selected else ""}>'
                            f'{review._e(sid)} / {review._e(_stratum_label(base["sets"][sid], key))}</option>'
                            for value, (sid, key) in choices.items())
    independent_selector = ""
    candidate_key = "national"
    distinct_stratifiers = False
    if selected:
        sid, key = choices[selected]
        definition = candidate["sets"].get(sid) or {}
        distinct_stratifiers = base["sets"][sid].get("stratifier") != definition.get("stratifier")
        candidate_key = key
        if distinct_stratifiers:
            candidate_keys = sorted(definition.get("curves") or {}, key=lambda item: (item != "national", item))
            candidate_key = candidate_stratum if candidate_stratum in candidate_keys else "national"
            candidate_options = ''.join(f'<option value="{review._e(item)}"{" selected" if item == candidate_key else ""}>'
                                        f'{review._e(_stratum_label(definition, item))}</option>' for item in candidate_keys)
            independent_selector = (' <label for="candidate-stratum">Selected alternative reference</label> '
                                    f'<select id="candidate-stratum" name="candidate_stratum">{candidate_options}</select>')
    sections.append('<section><h2>Stored curve comparison and uncertainty</h2>'
                    f'<form method="get"><input type="hidden" name="study" value="{review._e(study_id)}">'
                    f'<input type="hidden" name="alternative" value="{alternative}">'
                    f'<label for="curve">Reference set and Alternative 1 stratum</label> <select id="curve" name="curve">{curve_options}</select>'
                    + independent_selector + ' <button type="submit">Show curves</button></form>')
    if selected:
        sid, key = choices[selected]
        axis = review.QUANTITY_LABELS.get(base["sets"][sid].get("quantity"), "Physical value")
        first, first_label = _curve(base, sid, key)
        second, second_label = _curve(candidate, sid, candidate_key)
        if distinct_stratifiers:
            sections.append('<p class="status">The alternatives use different reference classifications. Level II and NARS-9 selections '
                            'are not a one-to-one geographic match. Select each stored reference independently; no geographic crosswalk '
                            'or reach-specific resolution is inferred here.</p>')
        sections.append('<div class="grid">'
                        + review._fit_card("Alternative 1: " + _stratum_label(base["sets"][sid], key) + ". " + first_label, first, axis_label=axis)
                        + review._fit_card(f"Alternative {alternative[-1]}: " + _stratum_label(candidate["sets"][sid], candidate_key)
                                           + ". " + second_label, second, axis_label=axis) + '</div>')
        uncertainty = [{k: _value(v, count=k in {"n_members", "n_clusters", "n_population", "n_valid", "n_requested"})
                        for k, v in item.items()} for item in _rows(row.get("curve_uncertainty")) if item.get("set_id") == sid and item.get("key") in {key, candidate_key, "national"}]
        sections.append('<p>Uncertainty rows retain their supplied stratum and fit-linkage labels. They do not automatically apply '
                        'to a different selected regional curve.</p>')
        sections.append(review._table(uncertainty, [("key", "Stored stratum"), ("fit_linkage", "Fit linkage"),
            ("x39_lo", "x39 CI lower"), ("x39_hi", "x39 CI upper"), ("x69_lo", "x69 CI lower"), ("x69_hi", "x69 CI upper"),
            ("mean_flip", "Mean flip share"), ("p90_flip", "P90 flip share"), ("n_members", "Finite references"),
            ("n_clusters", "HUC12 clusters"), ("n_population", "Evaluated population"), ("n_valid", "Valid draws"),
            ("n_requested", "Requested draws")]) if uncertainty else
            '<p class="muted">Reference-resampling uncertainty is unavailable for this selected curve.</p>')
    sections.append('<p>Plots show stored points only. Reference-sampling uncertainty is not field accuracy. Absent uncertainty '
                    'is unavailable, and no national result is inferred from a regional bootstrap.</p></section>')
    sections.append(_diagnostic("Low-flow evidence and alternatives", row.get("low_flow")))
    sections.append(_diagnostic("Agriculture evidence and shared influence", row.get("agriculture")))
    sections.append(_diagnostic("Biological model and fallback routes", row.get("model")))
    sections.append('<section><h2>Interpretation limits</h2>' + _notes(summary.get("limitations"))
                    + '<p>These comparisons do not change the approved scoring method or weights. Shared inputs are not '
                    'independent validation. Better spread or a single point estimate does not establish ecological accuracy.</p></section>')
    sections.append(_download_links(study_id, study["completion"], alternative))
    return _document(sections)


def _document(sections) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Local EASI alternative studies</title><style>' + review.STYLE
            + '.verified{background:#e3f1e8;border-left:4px solid #2d6b43;padding:12px}</style></head><body><main>'
            + ''.join(sections) + '</main></body></html>')


def routes(root: Path):
    from starlette.responses import FileResponse, HTMLResponse, Response
    from starlette.routing import Route

    async def page(request):
        if not review._loopback(request):
            return Response(status_code=403)
        import anyio
        from easi import config
        from easi.national import method_version
        params = request.query_params
        content = await anyio.to_thread.run_sync(render_page, root, config.DATA_DIR, method_version(),
                                                params.get("study", ""), params.get("alternative", "alternative-2"), params.get("curve", ""),
                                                params.get("candidate_stratum", ""))
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

    def download(study_id, item, data_dir, method):
        if item not in DOWNLOADS:
            raise StudyUnavailable("No enumerated download exists.")
        study = load_study(root, data_dir, method, study_id)
        relative = DOWNLOADS[item]
        expected = _expected_hash(study["completion"]["output_hashes"], relative)
        path = _inside(study["folder"], relative)
        try:
            before = path.stat()
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            after = path.stat()
            if hasher.hexdigest() != expected or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise StudyUnavailable("The download differs from its completed output.")
        except OSError as exc:
            raise StudyUnavailable("The download is unavailable.") from exc
        return path

    async def asset(request):
        if not review._loopback(request):
            return Response(status_code=403)
        import anyio
        from easi import config
        from easi.national import method_version
        try:
            path = await anyio.to_thread.run_sync(download, request.path_params["study_id"], request.path_params["item"],
                                                 config.DATA_DIR, method_version())
        except StudyUnavailable:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})

    return [Route("/local-review/alternatives/", page),
            Route("/local-review/alternatives/download/{study_id}/{item}", asset)]
