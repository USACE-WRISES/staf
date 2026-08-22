"""Assessment library reader/writer — StreamCurves side of ``apps/library/``.

Reads and writes the shared, version-controlled home for completed detailed STAF
assessments. The on-disk format contract lives in ``apps/library/README.md``.

This module is storage-only. Callers build the session payload
(``session_io.dump_session_fields``) and the DEEP bundle
(``deep_export.build_deep_assessment_bundle``); ``publish_version`` persists them as a
new versioned folder and maintains ``catalog.json`` + each ``manifest.json``. It stamps
the authoritative ``library`` block (id / version / updatedAt / region) onto the bundle.

The library sits next to the app under ``apps/`` (``apps/stream-curves`` ->
``apps/library``), so it is reachable and writable on local/desktop. On the cloud the
folder is absent, so :func:`writable` is False and the UI degrades to "share the session
with the publisher" instead of publishing directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import geo, session_io
from .paths import ROOT

logger = logging.getLogger("streamcurves")

# v2 (Part F): each version carries a content fingerprint, and lifecycle status lives
# in a separate append-only record. v1 catalogs/manifests are still read (a v1 record
# lacking status is treated as all-preliminary).
CATALOG_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 2
STATUS_SCHEMA_VERSION = 2

# Lets the desktop shell or tests point at a specific library root.
_ENV_ROOT = "STAF_LIBRARY_ROOT"
# Canonical-publish gate (Part F): mutating the repo apps/library tree requires a
# verified checkout (this flag), a writable library, and a maintainer audit name.
_ENV_PUBLISH = "STAF_LIBRARY_PUBLISH"
_ENV_MAINTAINER = "STAF_LIBRARY_MAINTAINER"

BUNDLE_FILE = "assessment.deep.json"
SESSION_FILE = "session.streamcurves.json"
META_FILE = "meta.json"
STATUS_FILE = "status.json"
VALIDATION_FILE = "validation.json"
PROVENANCE_FILE = "provenance.json"

VALIDATION_UNVALIDATED = "unvalidated"
VALIDATION_VALIDATED = "validated"
VALIDATION_STATES = (VALIDATION_UNVALIDATED, VALIDATION_VALIDATED)

# Lifecycle vocabulary (writer side). DEEP the consumer only distinguishes
# preliminary vs certified; the full 5-state set lives here for history/admin. Only
# preliminary and certified versions are eligible for new DEEP assessments.
DEFAULT_STATUS = "preliminary"
VERSION_STATUSES = (
    "preliminary",
    "under_review",
    "certified",
    "revised",
    "retired",
)


# --------------------------------------------------------------------------- #
# Location + capability
# --------------------------------------------------------------------------- #
def library_root() -> Path:
    """Resolve ``apps/library/``: ``STAF_LIBRARY_ROOT`` env if set, else the sibling
    of the app directory (``apps/stream-curves`` -> ``apps/library``)."""
    env = os.environ.get(_ENV_ROOT)
    if env:
        return Path(env)
    return ROOT.parent / "library"


def exists() -> bool:
    return library_root().is_dir()


def writable() -> bool:
    """True when the library exists and files can be created under it — the gate for
    publishing (local/desktop) vs. share-with-publisher (cloud)."""
    root = library_root()
    return root.is_dir() and os.access(root, os.W_OK)


# --------------------------------------------------------------------------- #
# Canonical-publish gate (Part F)
# --------------------------------------------------------------------------- #
def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _maintainer_name(maintainer: Optional[str] = None) -> str:
    raw = maintainer if maintainer is not None else os.environ.get(_ENV_MAINTAINER, "")
    return (raw or "").strip()


def publish_gate_reason(maintainer: Optional[str] = None) -> Optional[str]:
    """Why publishing to the canonical library is blocked, or ``None`` when allowed.

    Mutating the repo ``apps/library`` tree requires all of: ``STAF_LIBRARY_PUBLISH=1``
    (a verified repository checkout), a writable library, and a non-empty maintainer
    audit name (the ``maintainer`` argument, else env ``STAF_LIBRARY_MAINTAINER``).
    Ordinary users without the flag can still run the local flow and export packages;
    they just cannot mutate the canonical library.
    """
    if not _env_flag(_ENV_PUBLISH):
        return (
            "Canonical publishing is off here. Set STAF_LIBRARY_PUBLISH=1 in a verified "
            "repository checkout to publish into the shared assessment library."
        )
    if not writable():
        return (
            f"The assessment library at {library_root()} is not writable here. Publishing "
            "is a local/desktop action; on the web, share the session with the publisher."
        )
    if not _maintainer_name(maintainer):
        return "Enter a maintainer name for the publish audit trail before publishing."
    return None


def can_publish_canonical(maintainer: Optional[str] = None) -> bool:
    """True when this process may mutate the canonical ``apps/library`` tree
    (see :func:`publish_gate_reason` for the individual conditions)."""
    return publish_gate_reason(maintainer) is None


# The reviewer marker a staged batch run stamps on its standing decisions
# (streamcurves.decisions.PENDING_SUFFIX); duplicated here so this storage
# module does not import the policy machinery.
_PENDING_MARKER = "(pending owner confirmation)"


def _carries_pending_marker(provenance: dict) -> bool:
    """Whether a provenance document still holds the marker anywhere but the
    recorded command line (``manifest.agent.argv`` quotes the owner's own
    ``--approve-portfolio`` input verbatim and is not a decision). Mirrors
    ``decisions.pending_locations`` without importing it."""
    doc = dict(provenance or {})
    manifest = dict(doc.get("manifest") or {})
    agent = dict(manifest.get("agent") or {})
    agent.pop("argv", None)
    manifest["agent"] = agent
    doc["manifest"] = manifest
    return _PENDING_MARKER in json.dumps(doc, default=str)


def canonical_root() -> Path:
    """The repo's ``apps/library``, whatever STAF_LIBRARY_ROOT says."""
    return (ROOT.parent / "library").resolve()


def is_canonical_root() -> bool:
    try:
        return library_root().resolve() == canonical_root()
    except OSError:
        return False


def slugify(text: str) -> str:
    s = str(text).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


# --------------------------------------------------------------------------- #
# Small JSON helpers
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _json_default(o: Any):
    """JSON fallback for numpy scalars/arrays that may ride inside a bundle's curve
    points, without importing numpy here (duck-typed)."""
    item = getattr(o, "item", None)
    if callable(item):
        try:
            return o.item()
        except Exception:  # noqa: BLE001
            pass
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# --------------------------------------------------------------------------- #
# Coverage gate (STAF function completeness)
# --------------------------------------------------------------------------- #
def _require_documented_coverage(assessment_id: str, bundle: dict) -> dict:
    """Refuse to publish while a STAF function is neither covered nor justified.

    ``docs/tiered-approach.md`` allows a regional tool to tailor the 20-function
    list "but changes should be documented and traceable". Tailoring therefore
    stays legal; an undocumented gap does not. Every published version before this
    gate existed covered 8-13 of 20 with no record of whether that was deliberate.
    """
    from . import deep_export  # local: deep_export imports nothing from here

    coverage = bundle.get("functionCoverage")
    if not isinstance(coverage, dict):
        # A bundle built by an older code path carries no coverage block; derive one
        # rather than waving it through, so the gate cannot be bypassed by staleness.
        crosswalk = deep_export.deep_read_staf_crosswalk()
        coverage = deep_export.function_coverage(
            bundle.get("metricsByFunction"), crosswalk, None)
    else:
        crosswalk = deep_export.deep_read_staf_crosswalk()

    if int(coverage.get("missing") or 0) > 0:
        gaps = deep_export.coverage_gap_message(coverage, crosswalk)
        raise ValueError(
            f"Cannot publish {assessment_id}: {coverage['missing']} of "
            f"{coverage['total']} STAF functions have no metric and no documented "
            f"reason -- {gaps}. Either add a metric that informs each one, or record "
            "a coverage exception (reason + justification) explaining why it is out "
            f"of scope for this assessment. Valid reasons: "
            f"{', '.join(deep_export.FUNCTION_EXCLUSION_REASONS)}."
        )
    return coverage


# --------------------------------------------------------------------------- #
# Content digest (analytical fingerprint)
# --------------------------------------------------------------------------- #
def _canonical_content(bundle: dict) -> dict:
    """The analytical content that defines a version's fingerprint: the scored metrics
    with their inlined reference curves (``metricsByFunction``) and the region *code*.
    Volatile/provenance fields (updatedAt, author, the library block, the region outline
    polygon, checksums) are deliberately excluded so a status change or an identical
    re-publish yields the same digest."""
    region = bundle.get("region") or (bundle.get("library") or {}).get("region") or {}
    code = region.get("code") if isinstance(region, dict) else None
    return {
        "metricsByFunction": bundle.get("metricsByFunction") or [],
        "regionCode": "" if code is None else str(code),
    }


def content_digest(bundle: dict) -> str:
    """``"sha256:" + <hex>`` over the canonical analytical content of a DEEP bundle
    (sorted keys, compact separators), so key order and whitespace do not affect it.

    DEEP prefers this exact ``contentDigest`` key when the bundle carries one
    (``apps/deep/deep/session.py`` ``bundle_digest``)."""
    canon = json.dumps(
        _canonical_content(bundle),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Read side
# --------------------------------------------------------------------------- #
def catalog_path() -> Path:
    return library_root() / "catalog.json"


def read_catalog() -> dict:
    """Return ``catalog.json``, or an empty catalog when the file is absent."""
    p = catalog_path()
    if not p.is_file():
        return {"schemaVersion": CATALOG_SCHEMA_VERSION, "generatedAt": None, "assessments": []}
    return _read_json(p)


def list_assessments() -> list[dict]:
    return read_catalog().get("assessments") or []


def assessment_dir(assessment_id: str) -> Path:
    return library_root() / "assessments" / assessment_id


def manifest_path(assessment_id: str) -> Path:
    return assessment_dir(assessment_id) / "manifest.json"


def read_manifest(assessment_id: str) -> Optional[dict]:
    p = manifest_path(assessment_id)
    return _read_json(p) if p.is_file() else None


def version_dir(assessment_id: str, version: int) -> Path:
    return assessment_dir(assessment_id) / f"v{int(version)}"


def latest_version(assessment_id: str) -> int:
    return int((read_manifest(assessment_id) or {}).get("latestVersion") or 0)


def load_version_bundle(assessment_id: str, version: int) -> dict:
    return _read_json(version_dir(assessment_id, version) / BUNDLE_FILE)


def load_version_session(assessment_id: str, version: int) -> dict:
    """Parsed + migrated session payload for restore (via ``session_io``)."""
    return session_io.load_session_payload(version_dir(assessment_id, version) / SESSION_FILE)


# --------------------------------------------------------------------------- #
# Lifecycle status — a separate append-only audited record so a status change
# never touches a version's analytical content (or its content fingerprint)
# --------------------------------------------------------------------------- #
def status_path(assessment_id: str) -> Path:
    return assessment_dir(assessment_id) / STATUS_FILE


def read_status(assessment_id: str) -> dict:
    """The append-only status record for an assessment, or an empty record when the file
    is absent (a v1 assessment with no ``status.json`` reads as all-preliminary)."""
    p = status_path(assessment_id)
    if not p.is_file():
        return {
            "schemaVersion": STATUS_SCHEMA_VERSION,
            "assessmentId": assessment_id,
            "history": [],
        }
    return _read_json(p)


def _status_map(assessment_id: str) -> dict[int, str]:
    """version -> current status (the last audited record for that version wins).
    Versions without a record are absent here and default to :data:`DEFAULT_STATUS`."""
    out: dict[int, str] = {}
    for rec in read_status(assessment_id).get("history") or []:
        try:
            v = int(rec.get("version"))
        except (TypeError, ValueError):
            continue
        s = str(rec.get("status") or "").strip().lower()
        if v and s in VERSION_STATUSES:
            out[v] = s
    return out


def version_status(assessment_id: str, version: int) -> str:
    """Current lifecycle status of a version, defaulting to ``"preliminary"``."""
    return _status_map(slugify(assessment_id)).get(int(version), DEFAULT_STATUS)


def _append_status(
    assessment_id: str, version: int, status: str, actor: str, note: Optional[str]
) -> None:
    doc = read_status(assessment_id)
    doc["schemaVersion"] = STATUS_SCHEMA_VERSION
    doc["assessmentId"] = assessment_id
    history = list(doc.get("history") or [])
    history.append(
        {
            "version": int(version),
            "status": status,
            "actor": actor or "",
            "timestamp": _now_iso(),
            "note": note,
        }
    )
    doc["history"] = history
    _write_json(status_path(assessment_id), doc)


def set_version_status(
    assessment_id: str,
    version: int,
    status: str,
    actor: str,
    note: Optional[str] = None,
) -> str:
    """Append a lifecycle-status change for a published version and refresh the catalog
    pointers. The record is append-only + audited (actor + timestamp); it never rewrites
    the version's analytical content, so the content fingerprint is unchanged. Returns
    the new status.

    ``status`` must be one of :data:`VERSION_STATUSES`; ``actor`` (the maintainer audit
    name) must be non-empty.
    """
    assessment_id = slugify(assessment_id)
    status = str(status or "").strip().lower()
    if status not in VERSION_STATUSES:
        raise ValueError(
            f"Unknown status {status!r}; expected one of {', '.join(VERSION_STATUSES)}."
        )
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("A non-empty actor (maintainer audit name) is required.")
    if not writable():
        raise RuntimeError(
            f"Assessment library is not writable at {library_root()}; cannot record a "
            "status change here."
        )
    if not version_dir(assessment_id, version).is_dir():
        raise ValueError(f"{assessment_id} has no version v{int(version)} to update.")
    _append_status(assessment_id, int(version), status, actor, note)
    _regenerate_catalog()
    logger.info("Set %s v%d status -> %s (%s).", assessment_id, int(version), status, actor)
    return status


# --------------------------------------------------------------------------- #
# Validation records — a separate append-only record of independent-check
# evidence + validation state, stored apart from analytical content (like status)
# --------------------------------------------------------------------------- #
def validation_path(assessment_id: str) -> Path:
    return assessment_dir(assessment_id) / VALIDATION_FILE


def read_validation(assessment_id: str) -> dict:
    """The append-only validation record for an assessment (records + state history),
    or an empty record when the file is absent."""
    p = validation_path(slugify(assessment_id))
    if not p.is_file():
        return {
            "schemaVersion": STATUS_SCHEMA_VERSION,
            "assessmentId": slugify(assessment_id),
            "records": [],
            "history": [],
        }
    return _read_json(p)


def _validation_records_for(assessment_id: str, version: int) -> list[dict]:
    return [r for r in read_validation(assessment_id).get("records") or []
            if int(r.get("version") or 0) == int(version)]


def _validation_state_map(assessment_id: str) -> dict[int, dict]:
    """version -> {state, summary} from the last state-change record per version."""
    out: dict[int, dict] = {}
    for rec in read_validation(assessment_id).get("history") or []:
        try:
            v = int(rec.get("version"))
        except (TypeError, ValueError):
            continue
        s = str(rec.get("state") or "").strip().lower()
        if v and s in VALIDATION_STATES:
            out[v] = {"state": s, "summary": rec.get("summary")}
    return out


def version_validation_state(assessment_id: str, version: int) -> str:
    """Current validation state of a version, defaulting to ``"unvalidated"``."""
    return _validation_state_map(slugify(assessment_id)).get(
        int(version), {}).get("state", VALIDATION_UNVALIDATED)


def version_validation_summary(assessment_id: str, version: int) -> Optional[dict]:
    return _validation_state_map(slugify(assessment_id)).get(
        int(version), {}).get("summary")


def _write_validation(assessment_id: str, doc: dict) -> None:
    doc["schemaVersion"] = STATUS_SCHEMA_VERSION
    doc["assessmentId"] = assessment_id
    _write_json(validation_path(assessment_id), doc)


def add_validation_record(
    assessment_id: str,
    version: int,
    record: dict,
    actor: str,
    note: Optional[str] = None,
) -> dict:
    """Append one independent-check evidence record to a version. ``record`` is a
    small dict of aggregate-only fields (method, checker, outcome, reference); never
    per-site data. Returns the stored record."""
    assessment_id = slugify(assessment_id)
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("A non-empty actor is required for a validation record.")
    if not writable():
        raise RuntimeError(f"Assessment library is not writable at {library_root()}.")
    if not version_dir(assessment_id, version).is_dir():
        raise ValueError(f"{assessment_id} has no version v{int(version)}.")
    doc = read_validation(assessment_id)
    stored = {
        "version": int(version),
        "actor": actor,
        "timestamp": _now_iso(),
        "note": note,
        **{k: v for k, v in (record or {}).items() if k not in ("version", "actor", "timestamp")},
    }
    doc["records"] = list(doc.get("records") or []) + [stored]
    _write_validation(assessment_id, doc)
    _regenerate_catalog()
    logger.info("Added validation record for %s v%d (%s).", assessment_id, int(version), actor)
    return stored


def set_version_validation(
    assessment_id: str,
    version: int,
    state: str,
    summary: Optional[dict],
    actor: str,
    note: Optional[str] = None,
) -> str:
    """Set a version's validation state. ``"validated"`` requires at least one
    validation record already attached; ``summary`` is aggregate-only (safe to store).
    Append-only + audited; returns the new state."""
    assessment_id = slugify(assessment_id)
    state = str(state or "").strip().lower()
    if state not in VALIDATION_STATES:
        raise ValueError(
            f"Unknown validation state {state!r}; expected one of {', '.join(VALIDATION_STATES)}.")
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("A non-empty actor (maintainer audit name) is required.")
    if not writable():
        raise RuntimeError(f"Assessment library is not writable at {library_root()}.")
    if not version_dir(assessment_id, version).is_dir():
        raise ValueError(f"{assessment_id} has no version v{int(version)}.")
    if state == VALIDATION_VALIDATED and not _validation_records_for(assessment_id, version):
        raise ValueError(
            "Cannot mark validated without at least one validation record attached.")
    doc = read_validation(assessment_id)
    doc["history"] = list(doc.get("history") or []) + [
        {
            "version": int(version),
            "state": state,
            "summary": summary,
            "actor": actor,
            "timestamp": _now_iso(),
            "note": note,
        }
    ]
    _write_validation(assessment_id, doc)
    _regenerate_catalog()
    logger.info("Set %s v%d validation -> %s (%s).", assessment_id, int(version), state, actor)
    return state


# --------------------------------------------------------------------------- #
# Write side (publish)
# --------------------------------------------------------------------------- #
def _regenerate_catalog() -> None:
    """Rebuild ``catalog.json`` from every ``assessments/*/manifest.json`` + its
    ``status.json``.

    Each entry keeps the lean identity/region and adds lifecycle pointers derived from
    the per-version status records: ``latestPreliminary`` / ``latestCertified`` /
    ``defaultVersion`` (latest certified else latest preliminary, falling back to the
    numeric latest), plus the ``contentDigest`` of the latest version. A v1 manifest
    with no ``status.json`` and no per-version digest reads as all-preliminary.
    """
    root = library_root()
    adir = root / "assessments"
    entries: list[dict] = []
    if adir.is_dir():
        for sub in sorted((p for p in adir.iterdir() if p.is_dir()), key=lambda p: p.name):
            mp = sub / "manifest.json"
            if not mp.is_file():
                continue
            m = _read_json(mp)
            aid = m.get("assessmentId") or sub.name
            versions = m.get("versions") or []
            latest = int(m.get("latestVersion") or 0)
            latest_updated = next(
                (v.get("updatedAt") for v in versions
                 if int(v.get("version") or 0) == latest),
                None,
            )
            digest_by_v = {
                int(v.get("version") or 0): v.get("contentDigest") for v in versions
            }
            smap = _status_map(aid)

            def _cur(v: dict) -> str:
                return smap.get(int(v.get("version") or 0), DEFAULT_STATUS)

            prelim = [int(v.get("version") or 0) for v in versions if _cur(v) == "preliminary"]
            certified = [int(v.get("version") or 0) for v in versions if _cur(v) == "certified"]
            latest_prelim = max(prelim) if prelim else 0
            latest_cert = max(certified) if certified else 0
            default_v = latest_cert or latest_prelim or latest
            vmap = _validation_state_map(aid)
            default_vdir = sub / f"v{int(default_v)}"
            entries.append(
                {
                    "assessmentId": m.get("assessmentId"),
                    "assessmentName": m.get("assessmentName"),
                    "region": m.get("region"),
                    "stateCode": m.get("stateCode", ""),
                    "stateName": m.get("stateName", ""),
                    "latestVersion": latest,
                    "latestUpdatedAt": latest_updated,
                    "latestPreliminary": latest_prelim,
                    "latestCertified": latest_cert,
                    "defaultVersion": default_v,
                    "contentDigest": digest_by_v.get(latest),
                    "validationState": vmap.get(int(default_v), {}).get(
                        "state", VALIDATION_UNVALIDATED),
                    "validationSummary": vmap.get(int(default_v), {}).get("summary"),
                    # A version without provenance.json is visible from the top
                    # level, so an unauditable default cannot hide in the catalog.
                    "provenanceState": (
                        "present" if (default_vdir / PROVENANCE_FILE).is_file()
                        else "absent"),
                }
            )
    _write_json(
        catalog_path(),
        {
            "schemaVersion": CATALOG_SCHEMA_VERSION,
            "generatedAt": _now_iso(),
            "assessments": entries,
        },
    )


def _require_portfolio_approval(assessment_id: str, bundle: dict, meta: dict) -> None:
    """SELECT-01: a function carrying more than two metrics publishes only with a
    recorded human approval (``meta['portfolioApprovals']``: a list of
    ``{functionId, approvedBy, note}``). The approval is written into meta.json,
    so the decision is auditable beside the version it authorized."""
    approvals = {str(a.get("functionId")): a
                 for a in (meta.get("portfolioApprovals") or [])
                 if a.get("functionId") and a.get("approvedBy")}
    unapproved = []
    for block in bundle.get("metricsByFunction") or []:
        metrics = block.get("metrics") or []
        fid = str(block.get("functionId") or "")
        if len(metrics) > 2 and fid not in approvals:
            unapproved.append(f"{fid} ({len(metrics)} metrics)")
    if unapproved:
        raise ValueError(
            f"Refusing to publish '{assessment_id}': more than two metrics per "
            f"function requires a recorded human approval (SELECT-01). Missing "
            f"approvals: {', '.join(unapproved)}. Pass meta['portfolioApprovals'] "
            "entries with functionId and approvedBy."
        )


def publish_version(
    assessment_id: str,
    meta: dict,
    session_payload: dict,
    bundle: dict,
    restricted_package: Optional[dict] = None,
    provenance: Optional[dict] = None,
) -> int:
    """Write a new version for ``assessment_id`` and return its version number.

    ``meta``: identity for the assessment — ``assessmentName``, ``region`` (see the
    README region block), optional ``stateCode`` / ``stateName`` / ``sourceCitation`` /
    ``author`` / ``revisionNotes``.
    ``session_payload``: the dict from :func:`session_io.dump_session_fields` (round-trip).
    The publisher passes the *full* session here so every published version can be
    reopened and revised in StreamCurves.
    ``bundle``: the dict from :func:`deep_export.build_deep_assessment_bundle`. This
    function stamps the authoritative ``library`` block and the stable ``assessmentId``
    onto it before writing.
    ``restricted_package``: optional ``{"sha256", "summary"}`` recorded in ``meta.json``
    so the access-controlled full package's checksum + aggregate summary are auditable.
    ``provenance``: optional run manifest, decision log and review queue from
    :mod:`streamcurves.provenance`, written beside the bundle as ``provenance.json``.
    Without it a published version, the only citable artifact here, carries no record
    of how it was produced; the run folder that has one is untracked scratch.
    """
    if not writable():
        raise RuntimeError(
            f"Assessment library is not writable at {library_root()}. Publishing is a "
            "local/desktop action; on the web, save the session and share it with the "
            "publisher instead."
        )
    assessment_id = slugify(assessment_id)
    if not assessment_id:
        raise ValueError("assessment_id is empty after slugify")

    # Coverage gate. A published version is a citable artifact, and this is the only
    # place one is minted, so it is where "every STAF function is either covered or
    # documented as excluded" has to hold. Checked before anything is written, so a
    # rejected publish leaves no half-version on disk.
    _require_documented_coverage(assessment_id, bundle)

    # SELECT-01 gate: more than two metrics on one function requires a recorded
    # human approval riding in the meta, refused before anything is written.
    _require_portfolio_approval(assessment_id, bundle, meta)

    if provenance is None:
        logger.warning(
            "Publishing %s without a provenance document. The version will record "
            "provenance as absent; a reviewer will see it.", assessment_id)
    elif is_canonical_root() and _carries_pending_marker(provenance):
        # A staged batch run stamps its standing decisions with a pending
        # reviewer. They reach the canonical library only through `promote`,
        # which rewrites them to the confirming owner's name (2026-08-22).
        raise ValueError(
            f"Refusing to publish '{assessment_id}' to the canonical library: the "
            f"provenance still carries decisions marked '{_PENDING_MARKER}'. Confirm "
            "them with run_region_batch.py promote first.")

    manifest = read_manifest(assessment_id) or {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "assessmentId": assessment_id,
        "assessmentName": meta.get("assessmentName") or assessment_id,
        "region": meta.get("region"),
        "stateCode": meta.get("stateCode", ""),
        "stateName": meta.get("stateName", ""),
        "sourceCitation": meta.get("sourceCitation", ""),
        "latestVersion": 0,
        "versions": [],
    }
    prior_version = int(manifest.get("latestVersion") or 0)
    new_version = prior_version + 1
    # A revision (v>1) records which version it supersedes; the prior version's own
    # status is intentionally left unchanged (a maintainer decides when to retire it).
    supersedes_version = prior_version if prior_version >= 1 else None
    updated_at = _now_iso()

    out_bundle = dict(bundle)
    out_bundle["assessmentId"] = assessment_id
    if meta.get("assessmentName"):
        out_bundle["assessmentName"] = meta["assessmentName"]
    if meta.get("region") is not None:
        # Attach the region outline (ecoregion/state) so DEEP can shade it on the
        # "available assessments" map layer. Kept on the bundle only; the catalog,
        # manifest, and embedded library block keep the lean {kind,code,name} region.
        out_bundle["region"] = geo.region_with_polygon(meta["region"])
    for k in ("stateCode", "stateName", "sourceCitation"):
        if meta.get(k) is not None:
            out_bundle[k] = meta[k]

    # Fingerprint the analytical content (metricsByFunction + region code) before the
    # provenance/library block is attached; the outline polygon and volatile fields are
    # excluded so the digest is stable across re-publishes and status changes. DEEP reads
    # this exact ``contentDigest`` key.
    digest = content_digest(out_bundle)

    library_block = {
        "libraryId": assessment_id,
        "version": new_version,
        "updatedAt": updated_at,
        "author": meta.get("author", ""),
        "revisionNotes": meta.get("revisionNotes", ""),
        "region": meta.get("region"),
        "contentDigest": digest,
        "supersedesVersion": supersedes_version,
    }
    out_bundle["contentDigest"] = digest
    out_bundle["library"] = library_block

    vdir = version_dir(assessment_id, new_version)
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / BUNDLE_FILE, out_bundle)
    (vdir / SESSION_FILE).write_text(
        session_io.dumps_session(session_payload), encoding="utf-8"
    )
    if provenance:
        _write_json(vdir / PROVENANCE_FILE, {
            **provenance,
            "version": new_version,
            "updatedAt": updated_at,
            "contentDigest": digest,
        })
    _write_json(
        vdir / META_FILE,
        {
            "assessmentId": assessment_id,
            "assessmentName": meta.get("assessmentName") or assessment_id,
            "version": new_version,
            "updatedAt": updated_at,
            "author": meta.get("author", ""),
            "revisionNotes": meta.get("revisionNotes", ""),
            "region": meta.get("region"),
            "stateCode": meta.get("stateCode", ""),
            "stateName": meta.get("stateName", ""),
            "sourceCitation": meta.get("sourceCitation", ""),
            "contentDigest": digest,
            "supersedesVersion": supersedes_version,
            "provenance": "present" if provenance else "absent",
            **({"portfolioApprovals": meta["portfolioApprovals"]}
               if meta.get("portfolioApprovals") else {}),
            **({"restrictedPackage": restricted_package} if restricted_package else {}),
        },
    )

    manifest["schemaVersion"] = MANIFEST_SCHEMA_VERSION
    manifest["assessmentName"] = meta.get("assessmentName") or manifest.get("assessmentName")
    if meta.get("region") is not None:
        manifest["region"] = meta["region"]
    for k in ("stateCode", "stateName", "sourceCitation"):
        if meta.get(k) is not None:
            manifest[k] = meta[k]
    manifest["latestVersion"] = new_version
    manifest["versions"] = list(manifest.get("versions") or []) + [
        {
            "version": new_version,
            "updatedAt": updated_at,
            "author": meta.get("author", ""),
            "revisionNotes": meta.get("revisionNotes", ""),
            "contentDigest": digest,
            "supersedesVersion": supersedes_version,
        }
    ]
    _write_json(manifest_path(assessment_id), manifest)

    # Seed the append-only lifecycle record: a fresh publish is preliminary. Stored
    # separately from the analytical content so a later status change never re-mints
    # the version or its fingerprint.
    _append_status(
        assessment_id,
        new_version,
        DEFAULT_STATUS,
        meta.get("author") or "publisher",
        "Published new version.",
    )

    _regenerate_catalog()
    logger.info("Published %s v%d to the assessment library.", assessment_id, new_version)
    return new_version


# --------------------------------------------------------------------------- #
# DEEP registry bake (so the cloud DEEP ships the new latest version)
# --------------------------------------------------------------------------- #
def deep_bake_script() -> Path:
    """Path to the sibling DEEP bake script (apps/library -> apps/deep/scripts/...)."""
    return library_root().parent / "deep" / "scripts" / "bake_library_into_deep.py"


def rebake_deep() -> tuple[bool, str]:
    """Best-effort: fold the library's latest bundles into DEEP's baked registry
    (apps/deep/data) so the cloud DEEP lists them. Runs the sibling DEEP script as a
    subprocess (pure stdlib, so it works from the app interpreter). Returns
    ``(ok, message)``; never raises — publishing already succeeded by the time this runs.
    """
    import subprocess
    import sys

    script = deep_bake_script()
    if not script.is_file():
        return False, f"DEEP bake script not found at {script} (bake DEEP data manually)."
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--library-root", str(library_root())],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"DEEP bake could not run: {e}"
    if proc.returncode != 0:
        return False, f"DEEP bake exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}"
    tail = (proc.stdout or "").strip().splitlines()
    return True, (tail[-1] if tail else "DEEP registry updated.")
