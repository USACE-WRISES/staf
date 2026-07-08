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

CATALOG_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1

# Lets the desktop shell or tests point at a specific library root.
_ENV_ROOT = "STAF_LIBRARY_ROOT"

BUNDLE_FILE = "assessment.deep.json"
SESSION_FILE = "session.streamcurves.json"
META_FILE = "meta.json"


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
# Write side (publish)
# --------------------------------------------------------------------------- #
def _regenerate_catalog() -> None:
    """Rebuild ``catalog.json`` from every ``assessments/*/manifest.json``."""
    root = library_root()
    adir = root / "assessments"
    entries: list[dict] = []
    if adir.is_dir():
        for sub in sorted((p for p in adir.iterdir() if p.is_dir()), key=lambda p: p.name):
            mp = sub / "manifest.json"
            if not mp.is_file():
                continue
            m = _read_json(mp)
            latest = int(m.get("latestVersion") or 0)
            latest_updated = next(
                (v.get("updatedAt") for v in (m.get("versions") or [])
                 if int(v.get("version") or 0) == latest),
                None,
            )
            entries.append(
                {
                    "assessmentId": m.get("assessmentId"),
                    "assessmentName": m.get("assessmentName"),
                    "region": m.get("region"),
                    "stateCode": m.get("stateCode", ""),
                    "stateName": m.get("stateName", ""),
                    "latestVersion": latest,
                    "latestUpdatedAt": latest_updated,
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


def publish_version(
    assessment_id: str,
    meta: dict,
    session_payload: dict,
    bundle: dict,
) -> int:
    """Write a new version for ``assessment_id`` and return its version number.

    ``meta``: identity for the assessment — ``assessmentName``, ``region`` (see the
    README region block), optional ``stateCode`` / ``stateName`` / ``sourceCitation`` /
    ``author`` / ``revisionNotes``.
    ``session_payload``: the dict from :func:`session_io.dump_session_fields` (round-trip).
    ``bundle``: the dict from :func:`deep_export.build_deep_assessment_bundle`. This
    function stamps the authoritative ``library`` block and the stable ``assessmentId``
    onto it before writing.
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
    new_version = int(manifest.get("latestVersion") or 0) + 1
    updated_at = _now_iso()

    library_block = {
        "libraryId": assessment_id,
        "version": new_version,
        "updatedAt": updated_at,
        "author": meta.get("author", ""),
        "revisionNotes": meta.get("revisionNotes", ""),
        "region": meta.get("region"),
    }

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
    out_bundle["library"] = library_block

    vdir = version_dir(assessment_id, new_version)
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / BUNDLE_FILE, out_bundle)
    (vdir / SESSION_FILE).write_text(
        session_io.dumps_session(session_payload), encoding="utf-8"
    )
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
        },
    )

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
        }
    ]
    _write_json(manifest_path(assessment_id), manifest)

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
