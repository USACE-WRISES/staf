"""The base arm's reference scores when the stored analysis is another base's.

The study runner's stored analysis (``<root>/analysis``: values, the NRSA desktop table, the
strata, the local-review completion) was scored under one method. A 1.2.0 study on a base
other than that method still uses the stored analysis for its inputs (the sampling frame, the
state by Level II strata and weights, the station identities and desktop predictors, the
reference inputs of the refits and the stability resamples, the stored population values),
never for the base arm's scores. Those come from ``--base-scores <staging folder>``: a
national build's staging folder whose ``manifest.json`` records the study base's method
version and alternative id (the Alternative 2 rollout of 2026-09-16 for base
``alternative-2-b2e3033116e3``), whose per-VPU score files are hashed and recorded at the
snapshot, and whose evidence must be the evidence build this study copies (the staging
manifest the build records, and every unit's evidence sha, equal the root's).

``replay`` is what ``scorer.verify`` runs for such a study: the base arm's rating, function
score and index per function and its raw ECI and sub-indices must equal the base scores for
every sampled reach and NRSA station present in them, and the coverage (rows compared, rows
absent from the base scores: the stations scored from synthetic records) is reported.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from builder import REPO_ROOT
from . import bases
from .io import info, read_json, sha

#: what the stored analysis is used for when it is not the study base's
STORED_ANALYSIS_USES = (
    "the sampling frame and the state by Level II strata and weights (analysis/values.parquet: comid, state, l2, nars9, huc8, huc12)",
    "the NRSA station identities and desktop predictors (analysis/nrsa/nrsa_desktop.parquet: station_key, comid, desktop_source, huc8, the raw EROM, BFI and model columns)",
    "the strata of stations outside the stored footprint (analysis/strata.parquet)",
    "the reference inputs of the fold refits and the stability resamples (review/2026-09-15-regional/baseline/analysis)",
    "the stored population values of the stability step (analysis/landscape.parquet, analysis/values.parquet)",
)
STORED_ANALYSIS_NEVER = ("the base arm's scores: scorer.verify compares the base arm with the base scores, "
                         "never with the stored analysis's eci_raw, rating_, fs_ or index_ columns")
#: the score columns a base-scores file must carry beside the per-function columns
REQUIRED_COLUMNS = ("comid", "eci_raw", "phys_raw", "chem_raw", "bio_raw")
AGGREGATES = {"eci": "eci_raw", "physical": "phys_raw", "chemical": "chem_raw", "biological": "bio_raw"}
PREFIXES = {"rating": "rating_", "score": "fs_", "index": "index_"}


class BaseScoresError(RuntimeError):
    """A base-scores folder the study cannot take. Nothing is written."""


def stored_analysis_base(method_version, repo_root=REPO_ROOT):
    """The registry base whose method (or a version the library validated its files under)
    scored the stored analysis, or None when no base accepts the method version."""
    for base in bases.BASES.values():
        if bases.accepts(base, method_version, repo_root):
            return base
    return None


def evidence_consistency(manifest: dict, root) -> dict:
    """Whether the base scores were computed from the evidence build this study copies: the
    staging manifest sha the build records against the root's staging manifest, and every
    unit's evidence sha against the root's record of the same unit."""
    out = {"root_staging_manifest": None, "source_manifest_matches": None, "units_compared": 0,
           "units_differing": [], "identical": None}
    if root is None:
        return out
    path = Path(root) / "staging" / "manifest.json"
    if not path.is_file():
        out["note"] = "the root has no staging manifest; the evidence identity could not be checked"
        return out
    out["root_staging_manifest"] = sha(path)
    recorded = manifest.get("source_manifest_sha256")
    if recorded:
        out["source_manifest_matches"] = recorded == out["root_staging_manifest"]
    root_units = read_json(path).get("units") or {}
    differing = []
    compared = 0
    for huc4, unit in (manifest.get("units") or {}).items():
        theirs = ((unit or {}).get("evidence") or {}).get("sha256")
        ours = ((root_units.get(huc4) or {}).get("evidence") or {}).get("sha256")
        if theirs and ours:
            compared += 1
            if theirs != ours:
                differing.append(huc4)
    out.update(units_compared=compared, units_differing=differing)
    out["identical"] = (out["source_manifest_matches"] is not False) and not differing and \
        (compared > 0 or out["source_manifest_matches"] is True)
    return out


def read_base_scores(folder, base, root=None) -> dict:
    """The base-scores record a study manifest carries, after every refusal: the folder's
    manifest must name the study base's method version and alternative id, every scores file
    it names must exist with the sha256 it records, the files must carry the score columns,
    and the evidence they were scored from must be the root's."""
    folder = Path(folder)
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise BaseScoresError(f"{folder} has no manifest.json (a national build's staging folder is expected)")
    manifest = read_json(manifest_path)
    accepted = bases.accepted_method_versions(base, REPO_ROOT)
    method_version = manifest.get("method_version")
    alternative = manifest.get("alternative_id") or (manifest.get("scoring_identity") or {}).get("alternative_id")
    if method_version not in accepted:
        raise BaseScoresError(f"{folder}: the build's method version {method_version!r} is not the study base's "
                              f"({base.id}: {', '.join(accepted)})")
    if alternative != base.alternative_id:
        raise BaseScoresError(f"{folder}: the build's alternative id {alternative!r} is not the study base's "
                              f"{base.alternative_id!r}")
    scores = manifest.get("scores") or {}
    if not scores:
        raise BaseScoresError(f"{folder}: the manifest names no scores files")
    files = []
    for key, entry in sorted(scores.items()):
        asset = (entry or {}).get("asset")
        path = folder / str(asset) if asset else None
        if path is None or not path.is_file():
            raise BaseScoresError(f"{folder}: scores file {asset!r} for unit {key} is missing")
        if entry.get("method_version") not in (None, method_version) or entry.get("alternative_id") not in (None, alternative):
            raise BaseScoresError(f"{folder}: scores file {asset} records another method or alternative than the manifest")
        record = info(path)
        if entry.get("sha256") and entry["sha256"] != record["sha256"]:
            raise BaseScoresError(f"{folder}: scores file {asset} does not hash as the manifest records")
        files.append({"unit": key, "asset": asset, "path": record["path"], "bytes": record["bytes"],
                      "mtime_ns": record["mtime_ns"], "sha256": record["sha256"], "n_scored": entry.get("n_scored")})
    names = pq.read_schema(files[0]["path"]).names
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing or not any(n.startswith("rating_") for n in names):
        raise BaseScoresError(f"{folder}: {files[0]['asset']} lacks score columns {missing or ['rating_*']}")
    evidence = evidence_consistency(manifest, root)
    if evidence.get("identical") is False:
        raise BaseScoresError(f"{folder}: the base scores were computed from another evidence build than the one this "
                              f"study copies (source manifest matches: {evidence['source_manifest_matches']}, units "
                              f"differing: {evidence['units_differing'][:5]})")
    return {"folder": str(folder.resolve()), "manifest_sha256": sha(manifest_path), "build_id": manifest.get("build_id"),
            "method_version": method_version, "alternative_id": alternative,
            "scoring_identity": manifest.get("scoring_identity"), "criteria_set": manifest.get("criteria_set"),
            "updated": manifest.get("updated"), "reaches_scored": manifest.get("reaches_scored"),
            "source_manifest_sha256": manifest.get("source_manifest_sha256"),
            "files": files, "score_columns": [n for n in names if n in REQUIRED_COLUMNS or n.startswith(("rating_", "fs_", "index_"))],
            "evidence": evidence,
            "used_for": "the base arm's reference scores (scorer.verify); every input stays on the stored analysis"}


def score_fields(baseline: pd.DataFrame) -> dict:
    """study column -> base-scores column for the fields the replay compares."""
    fields = dict(AGGREGATES)
    for name in baseline.columns:
        prefix, _, function = name.partition("__")
        if prefix in PREFIXES:
            fields[name] = PREFIXES[prefix] + function
    return fields


def load_rows(base_scores: dict, comids, columns) -> pd.DataFrame:
    """The base-scores rows of ``comids`` (a DataFrame indexed by COMID), read from every
    scores file the record names, the columns checked present."""
    wanted = np.asarray(sorted(int(c) for c in comids), dtype=np.int64)
    frames = []
    for item in base_scores["files"]:
        path = Path(item["path"])
        names = pq.read_schema(path).names
        missing = [c for c in columns if c not in names]
        if missing:
            raise BaseScoresError(f"{path.name} lacks columns {missing[:6]}")
        table = pq.read_table(path, columns=list(columns))
        mask = np.isin(table.column("comid").to_numpy(), wanted)
        if mask.any():
            frames.append(table.filter(pa.array(mask)).to_pandas())
    if not frames:
        return pd.DataFrame(columns=list(columns)).set_index("comid")
    prior = pd.concat(frames, ignore_index=True).set_index("comid")
    if not prior.index.is_unique:
        raise BaseScoresError("the base scores hold a COMID more than once")
    return prior


def replay(study: Path, reaches: pd.DataFrame, baseline: pd.DataFrame, base_scores: dict) -> dict:
    """The base arm against the base scores: exact equality of rating, function score and
    index per function and of the raw ECI and sub-indices for every sampled reach and NRSA
    station present in the base scores; the coverage reported."""
    fields = score_fields(baseline)
    prior = load_rows(base_scores, baseline.index, ["comid", *dict.fromkeys(fields.values())])
    present = baseline.index.intersection(prior.index)
    mismatches = {}
    for new, old in fields.items():
        left, right = baseline.loc[present, new], prior.loc[present, old]
        same = left.eq(right) | (left.isna() & right.isna())
        if not same.all():
            bad = present[~same.to_numpy()]
            mismatches[new] = {"n": int((~same).sum()), "comids": [int(c) for c in bad[:10]]}
    if mismatches:
        raise RuntimeError(f"Base arm differs from the base scores ({base_scores.get('build_id')}): {mismatches}")
    expected = set(reaches.index[reaches.evidence_available])
    if set(baseline.index) != expected:
        raise RuntimeError("Alternative 1 output does not cover exactly the eligible evidence cohort")
    absent = baseline.index.difference(prior.index)
    meta = reaches.reindex(absent)
    stations = reaches.reindex(present)
    station_flag = stations["station"].fillna(False).astype(bool) if "station" in stations else pd.Series(False, index=present)
    absent_station = meta["station"].fillna(False).astype(bool) if "station" in meta else pd.Series(False, index=absent)
    absent_sampled = meta["sample"].fillna(False).astype(bool) if "sample" in meta else pd.Series(False, index=absent)
    return {"replay": "base-scores", "fields": fields, "compared": int(len(present)),
            "replayed_stored_reaches": int(len(present) - station_flag.sum()),
            "replayed_nrsa_stations": int(station_flag.sum()),
            "absent_from_base_scores": {"n": int(len(absent)), "stations": int(absent_station.sum()),
                                        "sampled_reaches": int(absent_sampled.sum()),
                                        "examples": [int(c) for c in absent[:20]]},
            "mismatches": mismatches}


__all__ = ["BaseScoresError", "STORED_ANALYSIS_USES", "STORED_ANALYSIS_NEVER", "REQUIRED_COLUMNS",
           "stored_analysis_base", "evidence_consistency", "read_base_scores", "score_fields", "load_rows", "replay"]
