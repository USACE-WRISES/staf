"""The DEEP assessment file (the header's Save and Open), and the provenance it records.

A whole DEEP run in one JSON file, so a field or desk session can be paused and resumed: the
delineation, *which* assessment definition was used (inlined with its curves so the file resumes
standalone, no registry needed) and every scenario's measured values. Scores are recomputed on load
(not trusted from the file).

Since 2026-10-05 DEEP writes the STAF assessment file, the structure EASI and SFARI write too
(``_vendor/staf_workbook/assessment_file.py``): ``delineation``; ``toolData`` with the
``assessment`` and its ``provenance`` (the resolved site region, Level III ecoregion and state;
the assessment version and lifecycle status; a content digest over the inlined bundle); and
``scenarios``, each scenario's ``{"measured_values"}``. Since 2026-09 the delineation may carry
``siteAnchor`` (the reach classification), ``siteEngine`` (the geometry-stripped STAF site engine
record) and ``watershedBasis``; earlier files lack them and every reader uses ``.get``.

Files written before still open: schema v2 (provenance, Existing Conditions' values at the top
level) and v1 or version-less files, whose provenance is reconstructed from the embedded bundle,
marking what the legacy file cannot supply as absent.

This module owns the provenance primitives (:func:`lifecycle_status`,
:func:`content_digest`, :func:`bundle_digest`) so the assessment file and the reports stamp
the same values. It imports only the standard library and the vendored toolkit.
"""
from __future__ import annotations

import hashlib
import json

from ._vendor.staf_workbook import assessment_file

TOOL = "DEEP"
#: the version of DEEP's own files from before the shared format (still read)
SCHEMA_VERSION = 2


# --------------------------------------------------------------------------- #
# Provenance primitives (shared by sessions + reports)
# --------------------------------------------------------------------------- #
#: The statuses DEEP runs and labels (owner, 2026-10-08): Draft, Preliminary and Final.
LIFECYCLES = ("draft", "preliminary", "certified")


def lifecycle_status(bundle: dict | None) -> str:
    """``"draft"`` | ``"preliminary"`` | ``"certified"`` for an assessment bundle.

    Reads an optional ``lifecycle``/``status`` field (bundle top level or its ``library``
    block) and defaults to ``"preliminary"``: nothing is certified until the publisher
    writes a status, and a bundle from before the status record (or with a status DEEP does
    not run) reads as preliminary, as it always has.
    """
    lib = (bundle or {}).get("library") or {}
    for src in ((bundle or {}), lib):
        for key in ("lifecycle", "status"):
            v = src.get(key)
            if v:
                s = str(v).strip().lower()
                if s in LIFECYCLES:
                    return s
    return "preliminary"


# Stored literal -> label shown to people. Machine fields (saved sessions,
# geojson properties, lifecycleByRef) keep the raw literal; only human-facing
# text uses these. Keep in sync with the copy in
# apps/stream-curves/streamcurves/library.py (no shared package yet).
STATUS_LABELS = {
    "draft": "Draft",
    "preliminary": "Preliminary",
    "under_review": "Under review",
    "certified": "Final",
    "revised": "Revised",
    "retired": "Retired",
}


def status_label(status) -> str:
    """Display label for a lifecycle status; unknown strings title-case rather
    than render blank. (DEEP only ever renders draft, preliminary and certified,
    since the bake filters everything else, but the map carries the writer's full
    vocabulary so a foreign value still reads sensibly.)"""
    s = str(status or "").strip().lower()
    return STATUS_LABELS.get(s) or (s.title() if s else STATUS_LABELS["preliminary"])


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
def dump(delineation: dict, assessment: dict, scenarios: dict, *,
         region: dict | None = None, completeness=None, result_state=None) -> str:
    """The file (the STAF assessment file).

    ``assessment`` is the loaded assessment dict (metricsByFunction with inlined curves) so a
    resumed session does not depend on the predefined registry. ``scenarios`` is
    ``assessment_file.scenarios_block``'s: every scenario's ``{"measured_values"}``.

    The ``provenance`` beside the assessment holds the assessmentId, version, lifecycle status,
    and content digest (all derived from ``assessment``), plus the resolved ``region`` (level3 +
    state) and ``completeness`` / ``resultState`` the caller supplies. All provenance is derived
    or optional, so a caller passing only the positional arguments still writes a valid file.
    """
    bundle = assessment or {}
    return assessment_file.dump(TOOL, delineation or {},
                                {"assessment": bundle,
                                 "provenance": _provenance(bundle, region, completeness, result_state)},
                                scenarios)


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
    """``{"delineation", "assessment", "provenance", "scenarios"}`` of a saved run (missing blocks
    read empty), a file from before the shared format migrated forward. Raises
    ``assessment_file.AssessmentFileError`` for a file DEEP cannot open. Scores are always
    recomputed by the caller, never trusted from the file."""
    st = assessment_file.parse(text, TOOL, legacy=_legacy)
    data = st["toolData"]
    assessment, provenance = data.get("assessment"), data.get("provenance")
    return {"delineation": st["delineation"],
            "assessment": assessment if isinstance(assessment, dict) else {},
            "provenance": provenance if isinstance(provenance, dict) else {},
            "scenarios": st["scenarios"]}


def _legacy(d: dict) -> dict:
    """A file from before the shared format (schema v2, or v1 and version-less ones migrated), in
    its shape: Existing Conditions' values were at the top level."""
    try:
        version = int(d.get("schemaVersion") or 1)
    except (TypeError, ValueError):
        raise assessment_file.AssessmentFileError("the file has no valid schema version.") from None
    if version < SCHEMA_VERSION:
        d = _migrate(d, version)
    return {"delineation": d.get("delineation", {}),
            "toolData": {"assessment": d.get("assessment") or {}, "provenance": d.get("provenance") or {}},
            "scenarios": assessment_file.legacy_scenarios(d.get("scenarios"),
                                                          {"measured_values": d.get("measured_values") or {}})}
