"""Grouped held-out reference-method validation, separate from frozen scores.

Every fold reuses only original strict panel membership outside all held-out
watersheds. The actual curve engine and export rounding build new fold-local
artifacts, including entrenchment. No fitted curve enters the frozen study.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import artifact, curves, panels, screens, stats
from . import ALTERNATIVES
from . import field_evaluation, scorer
from .io import info, now, read_json, safe_study, sha, write_json, write_parquet

VERSION = 1
FOLDS = tuple(range(5))
ORIGINAL = "review/2026-09-15-regional/baseline/analysis"


def _stamp(path: Path) -> dict:
    value = path.stat()
    return {"path": str(path.resolve()), "bytes": value.st_size, "mtime_ns": value.st_mtime_ns}


def _same_stamps(inputs):
    for item in inputs:
        if _stamp(Path(item["path"])) != {k: item[k] for k in ("path", "bytes", "mtime_ns")}:
            raise RuntimeError(f"Spatial input changed: {item['path']}")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _write_json(path, value):
    # Keep scorer checkpoint stamps unchanged when a resumed fold is identical.
    if path.is_file() and read_json(path) == value:
        return
    write_json(path, value)


def _write_table(path, table):
    if path.is_file() and pq.read_table(path).equals(table):
        return
    write_parquet(path, table)


def fold_ledger(observations: list[dict]) -> tuple[list[dict], dict]:
    """Use every observation identity for grouping, even with missing targets."""
    grouped = field_evaluation.group_folds(observations)
    for old, new in zip(observations, grouped):
        if old.get("fold") is not None and old["fold"] != new["fold"]:
            raise ValueError("Observation folds disagree with connected station/COMID/HUC8 grouping")
    ledgers = {fold: {"fold": fold, "excluded_huc8": set(), "evaluation_comids": set(),
                      "observation_rows": 0, "eligible_observation_rows": 0} for fold in FOLDS}
    unresolved = 0
    assignments = {}
    for row in grouped:
        fold = row["fold"]
        huc = field_evaluation._huc8(row.get("huc8"))
        comid = field_evaluation._integer(row.get("comid"))
        if fold is None or not huc:
            unresolved += 1
        if fold is None:
            continue
        item = ledgers[fold]
        item["observation_rows"] += 1
        if huc:
            item["excluded_huc8"].add(huc)
        if row.get("status") == "eligible" and huc and comid:
            if comid in assignments and assignments[comid] != fold:
                raise ValueError("One COMID appears in multiple held-out folds")
            assignments[comid] = fold
            item["evaluation_comids"].add(comid)
            item["eligible_observation_rows"] += 1
    rows = [{**item, "excluded_huc8": sorted(item["excluded_huc8"]),
             "evaluation_comids": sorted(item["evaluation_comids"])} for item in ledgers.values()]
    return rows, {"observation_rows": len(grouped), "unresolved_huc8_rows": unresolved,
                  "eligible_heldout_comids": len(assignments),
                  "definition": "Connected station/COMID/HUC8 components; five deterministic folds from the observation ledger."}


def _member_hucs(row, landscape_hucs) -> tuple[str, ...]:
    """Retain both identities on disagreement, excluding either watershed."""
    hucs = set()
    raw = str(row.get("huc12") or "").strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    if raw.isdigit() and len(raw) <= 12 and int(raw) > 0 and raw.zfill(12)[:8] != "00000000":
        hucs.add(raw.zfill(12)[:8])
    if huc := field_evaluation._huc8(landscape_hucs.get(int(row["comid"]))):
        hucs.add(huc)
    return tuple(sorted(hucs))


def _read_reference(original: Path, members: pd.DataFrame):
    """Read necessary columns once, filtered to original reference COMIDs."""
    ids = sorted(set(int(value) for value in members.comid))
    landscape_path, values_path = original / "landscape.parquet", original / "values.parquet"
    landscape_columns = ["comid", "huc8", *screens.PRESSURE_VARIABLES,
                         *(curves.QUANTITIES[q].column for q, _ in artifact.SET_SPECS.values()
                           if curves.QUANTITIES[q].source == "landscape")]
    value_columns = ["comid", *(curves.QUANTITIES[q].column for q, _ in artifact.SET_SPECS.values()
                               if curves.QUANTITIES[q].source == "values")]
    frames = {}
    receipts = []
    for kind, path, wanted in (("landscape", landscape_path, landscape_columns), ("values", values_path, value_columns)):
        if not path.is_file():
            raise FileNotFoundError(f"Original reference {kind} is required; current analysis cannot substitute: {path}")
        stamp = _stamp(path)
        names = pq.read_schema(path).names
        columns = list(dict.fromkeys(column for column in wanted if column in names))
        if "comid" not in columns or kind == "landscape" and "huc8" not in columns:
            raise ValueError(f"Original reference {kind} lacks required identities")
        table = pq.read_table(path, columns=columns, filters=[("comid", "in", ids)])
        frame = table.to_pandas().set_index("comid").sort_index()
        if not frame.index.is_unique:
            raise ValueError(f"Original reference {kind} has duplicate COMIDs")
        frames[kind] = frame
        # Only selected input columns are content-hashed. The receipt explicitly
        # distinguishes this from a full giant-Parquet hash.
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        receipts.append({**stamp, "selected_columns": columns, "selected_rows": len(table),
                         "selected_arrow_sha256": hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()})
        _same_stamps([stamp])
    frame = members.copy()
    landscape_hucs = frames["landscape"].huc8.to_dict()
    frame["_hucs"] = [_member_hucs(row, landscape_hucs) for row in frame.to_dict("records")]
    for quantity, _ in artifact.SET_SPECS.values():
        q = curves.QUANTITIES[quantity]
        source = frames[q.source]
        frame[quantity] = source[q.column].reindex(frame.comid).to_numpy(dtype=float) if q.column in source else np.nan
    pressure_columns = [name for name in screens.PRESSURE_VARIABLES if name in frames["landscape"]]
    for name in pressure_columns:
        frame["pressure__" + name] = frames["landscape"][name].reindex(frame.comid).to_numpy(dtype=float)
    return frame, receipts


def _fit_key(definition, key):
    stratifier = definition["stratifier"]
    if stratifier == "slope_class":
        return definition["quantity"], "national", "national:national", "" if key == "national" else key
    if key == "national":
        return definition["quantity"], "national", "national:national", ""
    if stratifier not in {"l2", "nars9"}:
        raise ValueError(f"Unexpected candidate reference stratifier: {stratifier}")
    return definition["quantity"], stratifier, f"{stratifier}:{key}", ""


def refit_fold(frame: pd.DataFrame, definitions: dict, excluded_hucs: set[str], heldout_comids: set[int]):
    """Fit every distinct requested reference, with no frozen-fit substitution."""
    excluded_huc = frame._hucs.map(lambda values: bool(excluded_hucs.intersection(values)))
    unresolved = frame._hucs.map(lambda values: not values)
    heldout = frame.comid.isin(heldout_comids)
    nonstrict = frame.screen.ne("strict")
    retained = frame.loc[~(excluded_huc | unresolved | heldout | nonstrict)].copy()
    pressure_data = {key: retained["pressure__" + key].to_numpy(dtype=float)
                     for key in screens.PRESSURE_VARIABLES if "pressure__" + key in retained}
    pressure, used = screens.composite_pressure(pressure_data)
    retained["_pressure"] = pressure if pressure_data else np.full(len(retained), np.nan)
    fitted, records = {}, []
    for identity in sorted(definitions):
        quantity, level, stratum, split = identity
        original = frame.loc[frame.level.eq(level) & frame.stratum.eq(stratum)]
        group = retained.loc[retained.level.eq(level) & retained.stratum.eq(stratum)]
        if split:
            original = original.loc[original.slope_class.eq(split)]
            group = group.loc[group.slope_class.eq(split)]
        if group.comid.duplicated().any():
            raise ValueError(f"Duplicate reference COMID within {identity}")
        n_members = len(group)
        tier = "complete" if n_members >= panels.FLOOR_COMPLETE else "exploratory" if n_members >= panels.FLOOR_EXPLORATORY else "none"
        q = curves.QUANTITIES[quantity]
        values = group[quantity].to_numpy(dtype=float)
        finite = values[np.isfinite(values)]
        row = {"quantity": quantity, "level": level, "stratum": stratum, "split": split,
               "screen": "strict", "panel_tier": tier, "n_members": n_members, "n": len(finite),
               "original_members": len(original), "excluded_members": len(original) - n_members,
               "training_huc8_count": len({huc for values in group._hucs for huc in values}),
               "heldout_comid_overlap": int(group.comid.isin(heldout_comids).sum()),
               "heldout_huc8_overlap": int(group._hucs.map(lambda values: bool(excluded_hucs.intersection(values))).sum()),
               "higher_is_better": q.higher_is_better}
        if len(finite) < curves.SPLIT_FLOOR:
            row.update(status="insufficient_data", usable=False, reason="fewer than 30 finite retained values", points=[])
        else:
            row.update(curves.fit_curve(finite, q, f"{stratum}|{split}" if split else stratum))
            row["rho_pressure"] = stats.spearman(values, group._pressure.to_numpy(dtype=float))
            row["usable"], row["reason"] = curves.usable(q, row, tier)
        if row["usable"]:
            fitted[identity] = artifact._curve(row)
        records.append(row)
    exclusions = {"original_memberships": len(frame), "retained_memberships": len(retained),
                  "heldout_watershed_memberships": int(excluded_huc.sum()),
                  "direct_heldout_comid_memberships": int(heldout.sum()),
                  "unresolved_huc8_memberships": int(unresolved.sum()), "nonstrict_memberships": int(nonstrict.sum()),
                  "multiple_huc8_identity_memberships": int(frame._hucs.map(len).gt(1).sum()),
                  "counts_overlap": True, "pressure_variables": used}
    return fitted, records, exclusions


def _candidate_artifact(original, fitted, fold):
    result = deepcopy(original)
    unavailable = []
    for sid, definition in result["sets"].items():
        selected = {}
        for key in definition["curves"]:
            identity = _fit_key(definition, key)
            if identity in fitted:
                selected[key] = fitted[identity]
            else:
                unavailable.append({"set_id": sid, "key": key, "fallback": "national" if key != "national" else None})
        definition["curves"] = selected
    missing_national = [sid for sid, definition in result["sets"].items() if "national" not in definition["curves"]]
    result["provenance"] = {"purpose": "grouped-heldout-reference-method-validation", "fold": fold,
                            "source_artifact_provenance": original.get("provenance"),
                            "frozen_scoring_artifact": False,
                            "fit_method": "curves.fit_curve, curves.usable and canonical artifact._curve"}
    return result, unavailable, missing_national


def _partition_evidence(study: Path, folder: Path, ledgers):
    assignment = {comid: row["fold"] for row in ledgers for comid in row["evaluation_comids"]}
    wanted = sorted(assignment)
    seen = set()
    sources = []
    for index, source in enumerate(sorted((study / "evidence").rglob("*.parquet"))):
        stamp = _stamp(source)
        sources.append(stamp)
        table = pq.read_table(source, filters=[("comid", "in", wanted)])
        ids = table["comid"].to_pylist()
        if any(comid in seen for comid in ids) or len(ids) != len(set(ids)):
            raise ValueError("Duplicate held-out COMID in study evidence")
        seen.update(ids)
        for fold in sorted({assignment[int(comid)] for comid in ids}):
            keep = pa.array([assignment[int(comid)] == fold for comid in ids])
            _write_table(folder / f"fold-{fold}/evidence/{index:05d}.parquet", table.filter(keep))
        _same_stamps([stamp])
    missing = set(wanted) - seen
    return sources, sorted(missing)


def _score_coverage(frame):
    missing, method_fallback, curve_fallback = {}, {}, Counter()
    for column in frame:
        if column.startswith("rating__"):
            missing[column[8:]] = int(frame[column].isna().sum())
        elif column.startswith("fallback__"):
            method_fallback[column[10:]] = int(frame[column].fillna(False).sum())
        elif column.startswith("curves__"):
            for raw in frame[column]:
                if not isinstance(raw, str):
                    continue
                for value in json.loads(raw).values():
                    if isinstance(value, dict) and (value.get("fallbackDepth") or 0) > 0:
                        curve_fallback[column[8:]] += 1
    return {"rows": len(frame), "missing_ratings": missing, "method_fallbacks": method_fallback,
            "curve_fallback_applications": dict(curve_fallback)}


def run(root: Path, study: Path) -> dict:
    """Create five fold-local refits and four merged held-out score tables."""
    root, study = Path(root).resolve(), safe_study(root, study)
    folder = (study / "spatial").resolve()
    if not folder.is_relative_to(study):
        raise ValueError("Spatial output escapes study")
    original = root / ORIGINAL
    artifact_path = study / "snapshot/app-data/reference-curves.json"
    frozen = read_json(artifact_path)
    registry_path = original / "curves/curve_registry.parquet"
    members_path = original / "panels/panel_members.parquet"
    panel_path = original / "panels/reference_panels.parquet"
    observation_path = study / "cohorts/observations.parquet"
    protected = {"artifact": info(artifact_path), "registry": info(registry_path), "members": info(members_path),
                 "panels": info(panel_path), "observations": info(observation_path)}
    if protected["registry"]["sha256"] != frozen["provenance"]["registry"]["sha256"]:
        raise ValueError("Original registry does not match frozen artifact provenance")
    fallback_provenance = frozen["provenance"].get("entrenchmentNationalFallback") or {}
    for name, key in (("members", "panelMembersSha256"), ("panels", "referencePanelsSha256")):
        if not fallback_provenance.get(key) or fallback_provenance[key] != protected[name]["sha256"]:
            raise ValueError(f"Original {name} do not match frozen artifact provenance")
    observation_columns = [name for name in ("station_key", "comid", "huc8", "fold", "status")
                           if name in pq.read_schema(observation_path).names]
    observations = pq.read_table(observation_path, columns=observation_columns).to_pylist()
    ledgers, observation_summary = fold_ledger(observations)
    if not observation_summary["eligible_heldout_comids"]:
        raise ValueError("No eligible field COMIDs have resolved spatial folds")
    candidates = {}
    definitions = {}
    candidate_inputs = []
    for alternative in ALTERNATIVES:
        aid = alternative["id"]
        path = study / "candidates" / aid / "app-data"
        candidate_inputs.extend(info(p) for p in sorted(path.glob("*.json")))
        candidate = read_json(path / "reference-curves.json")
        candidates[aid] = candidate
        for definition in candidate["sets"].values():
            for key in definition["curves"]:
                definitions[_fit_key(definition, key)] = True
    members = pq.read_table(members_path, filters=[("level", "in", ["l2", "nars9", "national"])]).to_pandas()
    frame, reference_inputs = _read_reference(original, members)
    evidence_stamps = [_stamp(path) for path in sorted((study / "evidence").rglob("*.parquet"))]
    source_paths = [Path(module.__file__) for module in (curves, artifact, panels, screens, field_evaluation, scorer)]
    source_paths += [Path(__file__), artifact.ENGINE_PATH]
    from builder import EASI_APP
    source_paths += sorted((EASI_APP / "easi").rglob("*.py"))
    source_hashes = {str(path): sha(path) for path in source_paths}
    signature = _digest({"version": VERSION, "protected": protected, "reference": reference_inputs,
                         "candidate_inputs": candidate_inputs, "folds": ledgers,
                         "evidence_stamps": evidence_stamps,
                         "source": source_hashes})
    input_path = folder / "inputs.json"
    if input_path.is_file() and read_json(input_path).get("input_digest") != signature:
        raise RuntimeError("Spatial inputs changed during a resumed study; use a new study")
    _write_json(input_path, {"input_digest": signature, "version": VERSION})
    result_path = folder / "result.json"
    if result_path.is_file():
        previous = read_json(result_path)
        if previous.get("input_digest") != signature:
            raise RuntimeError("Existing spatial validation has different inputs; use a new study")
        if all(Path(item["path"]).is_file() and sha(Path(item["path"])) == item["sha256"] for item in previous.get("outputs", [])):
            _same_stamps([*protected.values(), *reference_inputs, *candidate_inputs, *evidence_stamps])
            return previous
        raise RuntimeError("Completed spatial output has changed")
    evidence_inputs, missing_evidence = _partition_evidence(study, folder, ledgers)
    all_scores = defaultdict(list)
    fold_receipts = []
    for ledger in ledgers:
        fold = ledger["fold"]
        fold_dir = folder / f"fold-{fold}"
        heldout = set(ledger["evaluation_comids"]) - set(missing_evidence)
        _write_json(fold_dir / "comids.json", sorted(heldout))
        fitted, fits, exclusions = refit_fold(frame, definitions, set(ledger["excluded_huc8"]), set(ledger["evaluation_comids"]))
        _write_json(fold_dir / "fits.json", {"fold": fold, "exclusions": exclusions, "fits": fits})
        reports = []
        for alternative in ALTERNATIVES:
            aid = alternative["id"]
            source = study / "candidates" / aid / "app-data"
            data_dir = fold_dir / "candidates" / aid / "app-data"
            data_dir.mkdir(parents=True, exist_ok=True)
            for path in source.iterdir():
                if path.is_file() and path.name != "reference-curves.json":
                    target = data_dir / path.name
                    if not target.exists() or sha(target) != sha(path):
                        shutil.copy2(path, target)
            candidate, unavailable, national_missing = _candidate_artifact(candidates[aid], fitted, fold)
            _write_json(data_dir / "reference-curves.json", candidate)
            receipt = {"alternative_id": aid, "unavailable_fits": unavailable, "missing_national": national_missing,
                       "curve_count": sum(len(row["curves"]) for row in candidate["sets"].values())}
            if not heldout or national_missing:
                receipt.update(status="unavailable" if national_missing else "empty_evaluation_fold", rows=0)
            else:
                destination = fold_dir / "scores" / f"{aid}.parquet"
                scorer.launch(fold_dir, data_dir, destination, comids_file=fold_dir / "comids.json")
                scores = pq.read_table(destination).to_pandas()
                if scores.comid.duplicated().any() or set(scores.comid) != heldout:
                    raise ValueError("Held-out scorer output does not exactly match the requested fold COMIDs")
                scores["fold"] = fold
                all_scores[aid].append(scores)
                receipt.update(status="complete", **_score_coverage(scores))
            reports.append(receipt)
        fold_receipts.append({**ledger, "available_evidence_comids": len(heldout), "exclusions": exclusions,
                              "usable_fits": len(fitted), "unusable_fits": len(fits) - len(fitted), "alternatives": reports})
    outputs = []
    for alternative in ALTERNATIVES:
        aid = alternative["id"]
        scores = pd.concat(all_scores[aid], ignore_index=True).sort_values("comid") if all_scores[aid] else pd.DataFrame({"comid": pd.Series(dtype="int64"), "fold": pd.Series(dtype="int64")})
        if scores.comid.duplicated().any():
            raise ValueError("Merged held-out scores contain a COMID in multiple folds")
        destination = folder / "scores" / f"{aid}.parquet"
        write_parquet(destination, scores)
        outputs.append(info(destination))
    _same_stamps([*protected.values(), *reference_inputs, *candidate_inputs, *evidence_inputs])
    # Rehash the small original reference sources; large sources retain the
    # selected-column content proof plus unchanged full-file stamps.
    for item in protected.values():
        if sha(Path(item["path"])) != item["sha256"]:
            raise RuntimeError("Protected spatial source changed")
    if any(sha(Path(path)) != value for path, value in source_hashes.items()):
        raise RuntimeError("Spatial fitting or scoring source changed during the run")
    write_json(folder / "folds.json", {"folds": fold_receipts, "observations": observation_summary})
    outputs.append(info(folder / "folds.json"))
    for fold in FOLDS:
        fold_dir = folder / f"fold-{fold}"
        outputs.extend(info(path) for path in [fold_dir / "fits.json", fold_dir / "comids.json"])
        outputs.extend(info(path) for path in sorted((fold_dir / "candidates").rglob("*.json")))
    result = {"schema_version": 1, "status": "complete", "created_at": now(), "input_digest": signature,
              "purpose": "spatial_reference_method_validation", "frozen_alternative_scores_unchanged": True,
              "inputs": {"protected": protected, "original_reference": reference_inputs, "candidate_assets": candidate_inputs,
                         "evidence_stamps": evidence_inputs, "source_hashes": source_hashes}, "observations": observation_summary,
              "missing_evidence_comids": missing_evidence, "folds": fold_receipts, "outputs": outputs,
              "limits": ["Held-out reference-method refits are distinct from the frozen alternative comparison.",
                         "All observed fold watersheds and held-out COMIDs are excluded from original strict reference memberships.",
                         "Unresolved reference HUC8s are excluded; unresolved evaluation identities receive no spatial prediction.",
                         "Strict original panel membership is retained without re-screening, re-thinning or relaxed-panel replacement.",
                         "Missing regional fits use only a usable refitted national curve; missing national fits make a fold unavailable.",
                         "Unchanged entrenchment families are also refitted with held-out watersheds excluded.",
                         "Spatial exclusion does not establish independence from upstream shared evidence or biological-model training.",
                         "Original large files are stamp-checked and selected columns content-hashed; no full-file hash is claimed."]}
    write_json(result_path, result)
    return result
