"""Resumable-session serialization for DEEP.

Serializes a whole DEEP run — the delineation, *which* assessment definition was
used (inlined with its curves so the file resumes standalone, no registry
needed), and the per-metric measured values — to a single JSON file so a
field/desk session can be paused and resumed. Scores are recomputed on load
(not trusted from the file).

Schema v2 (Part D1/D2) additionally records reproducibility provenance: the resolved
site region (Level III ecoregion + state), the assessment version + lifecycle status,
and a content digest over the inlined bundle. v1 files (or files with no
``schemaVersion``) still load, via a migration that reconstructs provenance from the
embedded bundle and marks what the legacy file cannot supply as absent.

This module owns the provenance primitives (:func:`lifecycle_status`,
:func:`content_digest`, :func:`bundle_digest`) so the session file and the reports stamp
the same values. It imports only the standard library so it stays a leaf dependency.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = 2


# --------------------------------------------------------------------------- #
# Provenance primitives (shared by sessions + reports)
# --------------------------------------------------------------------------- #
def lifecycle_status(bundle: dict | None) -> str:
    """``"preliminary"`` | ``"certified"`` for an assessment bundle.

    Reads an optional ``lifecycle``/``status`` field (bundle top level or its ``library``
    block) and defaults to ``"preliminary"``. Per the confirmed two-state model and the
    Part E sequencing, nothing is certified until the publisher writes a status, so an
    absent field is preliminary.
    """
    lib = (bundle or {}).get("library") or {}
    for src in ((bundle or {}), lib):
        for key in ("lifecycle", "status"):
            v = src.get(key)
            if v:
                s = str(v).strip().lower()
                if s in ("preliminary", "certified"):
                    return s
    return "preliminary"


def content_digest(bundle: dict | None) -> str:
    """Stable ``sha256:<hex>`` over the inlined assessment bundle.

    Canonical JSON (sorted keys, compact separators) so the same bundle always yields the
    same digest regardless of key order or whitespace. Empty bundle -> ``""`` (nothing to
    stamp). This is DEEP's local reproducibility stamp until upstream fingerprints exist.
    """
    if not bundle:
        return ""
    canon = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def bundle_digest(bundle: dict | None) -> str:
    """The publisher's canonical digest if the bundle carries one, else a local
    :func:`content_digest`. Prefers upstream fingerprints once they exist (Part E)."""
    lib = (bundle or {}).get("library") or {}
    for src in ((bundle or {}), lib):
        for key in ("contentDigest", "digest", "fingerprint"):
            v = src.get(key)
            if v:
                return str(v)
    return content_digest(bundle)


def _provenance(bundle: dict, region, completeness, result_state) -> dict:
    lib = (bundle or {}).get("library") or {}
    return {
        "assessmentId": (bundle or {}).get("assessmentId", ""),
        "version": lib.get("version"),
        "lifecycle": lifecycle_status(bundle),
        "contentDigest": bundle_digest(bundle),
        "region": region or {"level3": None, "state": None},
        "completeness": completeness,
        "resultState": result_state,
    }


# --------------------------------------------------------------------------- #
# Serialize / deserialize
# --------------------------------------------------------------------------- #
def dump(delineation: dict, assessment: dict, measured_values: dict, *,
         region: dict | None = None, completeness=None, result_state=None) -> str:
    """Serialize the run state to a JSON string (schema v2).

    ``assessment`` is the loaded assessment dict (metricsByFunction with inlined
    curves) so a resumed session does not depend on the predefined registry.

    Beyond the v1 fields, v2 records a ``provenance`` block: the assessmentId, version,
    lifecycle status, and content digest (all derived from ``assessment``), plus the
    resolved ``region`` (level3 + state) and ``completeness`` / ``resultState`` the caller
    supplies. All provenance is derived or optional, so a caller passing only the three
    positional arguments still produces a valid v2 session.
    """
    bundle = assessment or {}
    return json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "method": "DEEP",
        "delineation": delineation or {},
        "assessment": bundle,
        "measured_values": measured_values or {},
        "provenance": _provenance(bundle, region, completeness, result_state),
    }, indent=2, ensure_ascii=False)


def _migrate(d: dict, from_version: int) -> dict:
    """Bring an older session up to the current schema.

    v1 (or version-less) -> v2: keep the embedded bundle and measured values (the current
    scoring rules reconstruct scores on load) and synthesize a ``provenance`` block from
    the embedded bundle, marking what the legacy file cannot supply as absent — v1 never
    resolved a region, so region is ``None``; version is whatever the embedded bundle
    carried (often ``None``).
    """
    d = dict(d)
    if from_version <= 1:
        bundle = d.get("assessment") or {}
        prov = _provenance(bundle, region=None, completeness=None, result_state=None)
        prov["migratedFrom"] = from_version
        d["provenance"] = prov
    d["schemaVersion"] = SCHEMA_VERSION
    return d


def load(text: str) -> dict:
    """Parse a saved run, migrating a v1 (or version-less) file forward.

    Returns the state dict in the v2 shape (missing keys default empty). Scores are always
    recomputed by the caller, never trusted from the file.
    """
    d = json.loads(text)
    version = int(d.get("schemaVersion") or 1)
    if version < SCHEMA_VERSION:
        d = _migrate(d, version)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "delineation": d.get("delineation", {}),
        "assessment": d.get("assessment", {}),
        "measured_values": d.get("measured_values", {}),
        "provenance": d.get("provenance", {}),
    }
