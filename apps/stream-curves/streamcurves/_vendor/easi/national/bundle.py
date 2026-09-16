"""Completion and identity gates for a national bundle used by the application.

Historical datasets remain readable by the low-level client for research. The
interactive viewer and report entry point require a completed, matching bundle.
"""
from __future__ import annotations

import hashlib
import json

from .. import config
from . import SCHEMA_VERSION, method_version


def asset_entries(manifest: dict) -> dict[str, dict]:
    """Collect named assets and reject contradictory declarations."""
    entries = {}
    blocks = [manifest.get("assets") or {}, manifest.get("scores") or {},
              manifest.get("tiles") or {}]
    blocks.append({key: value.get("evidence") or {}
                   for key, value in (manifest.get("units") or {}).items()})
    for block in blocks:
        for key, entry in block.items():
            name = entry.get("asset") or (key if block is blocks[0] else None)
            if not name:
                continue
            if "/" in name or "\\" in name or name in (".", ".."):
                raise ValueError("Invalid national asset name")
            sha = entry.get("sha256")
            if not isinstance(sha, str) or len(sha) != 64:
                raise ValueError(f"Missing national asset hash: {name}")
            if name in entries and entries[name]["sha256"] != sha:
                raise ValueError(f"Contradictory national asset hash: {name}")
            entries[name] = entry
    return entries


def dataset_key(manifest: dict) -> str:
    """All URLs and asynchronous requests bind to one exact manifest."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()[:24]


def identity_error(manifest: dict) -> str | None:
    if not manifest:
        return "The national dataset is unavailable."
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return "The national dataset schema is outdated for this EASI application."
    expected = config.scoring_identity()
    if (manifest.get("alternative_id") != expected.get("alternative_id")
            or manifest.get("criteria_set") != config.criteria_set()
            or manifest.get("method_version") != method_version()):
        return ("The configured national dataset is outdated for the active EASI method. "
                "A completed matching national build is required.")
    if manifest.get("scoring_identity") != expected:
        return "The national scoring assets do not match this EASI application."
    if manifest.get("build_status") != "complete" or not manifest.get("build_id"):
        return "The national build is incomplete."
    return None


def validate(dataset, *, verify_all: bool = True) -> dict:
    """Verify completion and asset bindings; local hashes are cached by file stamp.

    Remote range archives are bound to the producer's verified inventory and
    their content hashes. Files downloaded in full are checked by the client.
    """
    manifest = dataset.manifest() or {}
    error = identity_error(manifest)
    if error:
        raise ValueError(error)
    entries = asset_entries(manifest)
    if not {"completion.json", "stats.json", "coverage.geojson", "comid_huc4.parquet"} <= entries.keys():
        raise ValueError("The national build lacks required assets.")
    if not manifest.get("scores") or set(manifest["scores"]) != set(manifest.get("tiles") or {}):
        raise ValueError("The national score and tile regions do not match.")
    if dataset.local is not None and verify_all:
        for name in entries:
            if dataset.asset_path(name) is None:
                raise ValueError(f"The national asset is missing or damaged: {name}")
    path = dataset.asset_path("completion.json")
    if path is None:
        raise ValueError("The national completion receipt is missing or damaged.")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    for key in ("build_id", "alternative_id", "method_version", "source_manifest_sha256"):
        if not manifest.get(key) or receipt.get(key) != manifest.get(key):
            raise ValueError(f"The national completion receipt does not match: {key}")
    if (receipt.get("validation") or {}).get("passed") is not True:
        raise ValueError("The national build has not passed verification.")
    if receipt.get("status") != "complete":
        raise ValueError("The national completion receipt is incomplete.")
    if receipt.get("scoring_identity") != manifest.get("scoring_identity"):
        raise ValueError("The national receipt identifies different scoring assets.")
    reaches = manifest.get("reaches_scored")
    if (not isinstance(reaches, int) or reaches <= 0
            or receipt["validation"].get("reaches") != reaches
            or sum(int(unit.get("n_scored") or 0) for unit in manifest["units"].values()) != reaches):
        raise ValueError("The national completion cohort does not match the manifest.")
    artifacts = {name: entry["sha256"] for name, entry in entries.items()
                 if name != "completion.json"}
    digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    if receipt.get("artifact_inventory_sha256") != digest:
        raise ValueError("The national artifact inventory does not match its completion receipt.")
    if receipt.get("artifacts") != artifacts:
        raise ValueError("The national completion lists different artifacts.")
    stats = dataset.stats()
    if stats is None or any(stats.get(key) != manifest.get(key)
                            for key in ("build_id", "alternative_id", "method_version")):
        raise ValueError("The dashboard statistics do not match the national build.")
    return manifest
