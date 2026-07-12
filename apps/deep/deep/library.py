"""Read the shared STAF assessment library (DEEP side).

DEEP consumes the *latest* version of each published assessment. Two sources feed
the picker, resolved in :func:`deep.config.assessments`:

- the baked registry ``data/deep-assessments.json`` (what ships to the cloud, produced
  by ``scripts/build_deep_data.py``), and
- when reachable (local dev / desktop), the live ``apps/library/`` folder, merged on top
  so newly published versions show up without re-baking.

On the cloud the library folder is absent, so :func:`latest_bundles` returns ``[]`` and
only the baked registry is used. See ``apps/library/README.md`` for the format.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("deep")

_ENV_ROOT = "STAF_LIBRARY_ROOT"
DEEP_ROOT = Path(__file__).resolve().parents[1]  # apps/deep


def library_root() -> Path:
    """``STAF_LIBRARY_ROOT`` if set, else the sibling of the app dir
    (``apps/deep`` -> ``apps/library``)."""
    env = os.environ.get(_ENV_ROOT)
    if env:
        return Path(env)
    return DEEP_ROOT.parent / "library"


def available() -> bool:
    return (library_root() / "catalog.json").is_file()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def latest_bundles() -> list[dict]:
    """Return each published assessment's latest ``assessment.deep.json`` bundle.

    Empty when the library folder is absent (cloud) or on any read error — DEEP then
    falls back to its baked registry. Assessments with no published version are skipped.
    """
    root = library_root()
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        return []
    try:
        catalog = _read_json(catalog_path)
    except Exception:  # noqa: BLE001
        logger.exception("library: could not read catalog at %s", catalog_path)
        return []

    out: list[dict] = []
    for entry in catalog.get("assessments") or []:
        aid = entry.get("assessmentId")
        latest = int(entry.get("latestVersion") or 0)
        if not aid or latest < 1:
            continue
        bundle_path = root / "assessments" / aid / f"v{latest}" / "assessment.deep.json"
        if not bundle_path.is_file():
            logger.warning("library: %s v%d bundle missing at %s", aid, latest, bundle_path)
            continue
        try:
            out.append(_read_json(bundle_path))
        except Exception:  # noqa: BLE001
            logger.exception("library: could not read %s", bundle_path)
    return out


# --------------------------------------------------------------------------- #
# All-versions view (bake source): one record per eligible (id, version)
# --------------------------------------------------------------------------- #
_ELIGIBLE = ("preliminary", "certified")


def _status_map(root: Path, aid: str) -> dict[int, str]:
    """version -> current lifecycle status from ``status.json`` (last record wins)."""
    p = root / "assessments" / aid / "status.json"
    if not p.is_file():
        return {}
    try:
        doc = _read_json(p)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, str] = {}
    for rec in doc.get("history") or []:
        try:
            v = int(rec.get("version"))
        except (TypeError, ValueError):
            continue
        s = str(rec.get("status") or "").strip().lower()
        if v:
            out[v] = s
    return out


def all_eligible_bundles() -> list[dict]:
    """Every *eligible* (preliminary or certified) published version's bundle, each
    stamped with ``version``, ``lifecycle``, and ``assessmentRef`` (``"id@vN"``).

    This is the authoritative bake source: DEEP offers a version chooser per assessment,
    so every eligible version is baked (not just the latest). Empty when the library
    folder is absent (cloud) or on read error.
    """
    root = library_root()
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        return []
    try:
        catalog = _read_json(catalog_path)
    except Exception:  # noqa: BLE001
        logger.exception("library: could not read catalog at %s", catalog_path)
        return []

    out: list[dict] = []
    for entry in catalog.get("assessments") or []:
        aid = entry.get("assessmentId")
        if not aid:
            continue
        manifest_path = root / "assessments" / aid / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:  # noqa: BLE001
            continue
        smap = _status_map(root, aid)
        for v in manifest.get("versions") or []:
            try:
                ver = int(v.get("version"))
            except (TypeError, ValueError):
                continue
            status = smap.get(ver, "preliminary")
            if status not in _ELIGIBLE:
                continue
            bundle_path = root / "assessments" / aid / f"v{ver}" / "assessment.deep.json"
            if not bundle_path.is_file():
                logger.warning("library: %s v%d bundle missing", aid, ver)
                continue
            try:
                bundle = dict(_read_json(bundle_path))
            except Exception:  # noqa: BLE001
                logger.exception("library: could not read %s", bundle_path)
                continue
            bundle["version"] = ver
            bundle["lifecycle"] = status
            bundle["assessmentRef"] = f"{aid}@v{ver}"
            out.append(bundle)
    return out


def catalog_pointers() -> dict[str, dict]:
    """``{assessmentId: {defaultVersion, latestCertified, latestPreliminary}}`` from the
    library catalog, for the baked registry's ``libraryCatalog`` block. Empty when the
    library is absent."""
    root = library_root()
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        return {}
    try:
        catalog = _read_json(catalog_path)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for entry in catalog.get("assessments") or []:
        aid = entry.get("assessmentId")
        latest = int(entry.get("latestVersion") or 0)
        if not aid or latest < 1:
            continue
        out[aid] = {
            "defaultVersion": int(entry.get("defaultVersion") or latest),
            "latestCertified": int(entry.get("latestCertified") or 0),
            "latestPreliminary": int(entry.get("latestPreliminary") or 0),
        }
    return out
