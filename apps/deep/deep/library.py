"""Read the shared STAF assessment library (DEEP side).

DEEP runs every published version it is set to show: a Draft, Preliminary or Final one
whose Show in DEEP is on (StreamCurves writes ``visibility.json`` beside ``status.json``; no
record means shown). Three sources feed the picker, merged by ref in
:func:`deep.config._registry_records` (a later one wins):

- the baked registry ``data/deep-assessments.json`` (what ships to the cloud, produced
  by ``scripts/bake_library_into_deep.py``),
- the remote library release (:mod:`deep.remote_library`), which brings versions
  published after the deploy, and
- when reachable (local dev / desktop), the live ``apps/library/`` folder, merged on top
  so newly published versions show up without re-baking.

On the cloud the library folder is absent, so :func:`latest_bundles` returns ``[]`` and
the baked registry and the remote library are used. See ``apps/library/README.md`` for
the format.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("deep")

_ENV_ROOT = "STAF_LIBRARY_ROOT"
DEEP_ROOT = Path(__file__).resolve().parents[1]  # apps/deep

#: The last :func:`all_eligible_bundles` read. DEEP looks the library up many times per page
#: action and a full read parses every eligible bundle (0.35 s for the 126 of 2026-10, so a
#: Basin step that looked it up 171 times took 72 s). The read is kept with the size and
#: modification time of every file it looked at (catalog, manifests, status and visibility
#: files, bundles) and reused while none of them changed: a publish, a status change or a
#: Show in DEEP change rewrites one of them, so the next lookup reads again.
_last: dict = {"root": None, "stamps": None, "bundles": None, "withheld": None}
_last_lock = threading.Lock()


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


def is_deep(entry: dict) -> bool:
    """A library entry DEEP runs: every entry without an ``assessmentType`` (the whole
    library before typed entries) or with ``deep``. An EASI screening method published
    to the same library is never read here as a detailed assessment."""
    return str((entry or {}).get("assessmentType") or "deep") == "deep"


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
        if not aid or latest < 1 or not is_deep(entry):
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
# DEEP runs Draft, Preliminary and Final versions, each labeled on its map and
# badges (owner, 2026-10-08); under_review, revised and retired stay in the
# library only. StreamCurves' library.DEEP_STATUSES is the same tuple (its
# tests/test_library_visibility.py keeps them equal).
_ELIGIBLE = ("draft", "preliminary", "certified")
#: The order the default version is picked in: Final, else Preliminary, else Draft.
_DEFAULT_ORDER = ("certified", "preliminary", "draft")


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


def _hidden(root: Path, aid: str) -> set[int]:
    """The versions StreamCurves' Show in DEEP turned off: the last record of a version in
    ``visibility.json`` wins, and a version without one is shown."""
    p = root / "assessments" / aid / "visibility.json"
    try:
        doc = _read_json(p)
    except Exception:  # noqa: BLE001 - absent or unreadable: every version shown
        return set()
    shown: dict[int, bool] = {}
    for rec in doc.get("history") or []:
        try:
            v = int(rec.get("version"))
        except (TypeError, ValueError):
            continue
        if v and isinstance(rec.get("visibleInDeep"), bool):
            shown[v] = rec["visibleInDeep"]
    return {v for v, on in shown.items() if not on}


def all_eligible_bundles() -> list[dict]:
    """Every published version DEEP shows (a draft, preliminary or certified one that is
    not hidden), each bundle stamped with ``version``, ``lifecycle``, and
    ``assessmentRef`` (``"id@vN"``).

    This is the authoritative bake source: DEEP offers a version chooser per assessment,
    so every eligible version is baked (not just the latest). Empty when the library
    folder is absent (cloud) or on read error.

    Read once and reused while every file the read looked at is unchanged (``_last``).
    Each call gets its own list and its own top-level dicts; the nested content is shared
    between calls, as the baked registry's always is, so nothing may edit it in place.
    """
    return served_and_withheld()[0]


def served_and_withheld() -> tuple[list[dict], frozenset]:
    """:func:`all_eligible_bundles`, and the refs of every other DEEP version the library
    holds (its status is not one DEEP runs, or it is hidden), so a copy of one baked or
    listed by the release is dropped where this library is read (a local hide shows in a
    local DEEP at once)."""
    root = library_root()
    with _last_lock:
        if _last["root"] != str(root) or not _unchanged(_last["stamps"]):
            bundles, withheld, stamps = _read_eligible(root)
            _last.update(root=str(root), stamps=stamps, bundles=bundles, withheld=withheld)
        return [dict(b) for b in _last["bundles"]], _last["withheld"]


def default_pointers(records) -> dict[str, dict]:
    """``{assessmentId: {defaultVersion, latestCertified, latestPreliminary, latestDraft}}``
    of version records DEEP shows (stamped with ``version`` and ``lifecycle``). The default
    is the newest Final version, else the newest Preliminary one, else the newest Draft. An
    unknown lifecycle counts as preliminary, as :func:`deep.session.lifecycle_status` reads
    it."""
    by_id: dict[str, dict] = {}
    for r in records:
        aid = r.get("assessmentId")
        if not aid:
            continue
        try:
            ver = int(r.get("version") or 1)
        except (TypeError, ValueError):
            continue
        life = str(r.get("lifecycle") or "preliminary").strip().lower()
        life = life if life in _ELIGIBLE else "preliminary"
        by_id.setdefault(aid, {s: [] for s in _ELIGIBLE})[life].append(ver)
    out: dict[str, dict] = {}
    for aid, d in by_id.items():
        latest = {s: max(d[s]) if d[s] else 0 for s in _ELIGIBLE}
        out[aid] = {
            "defaultVersion": next(latest[s] for s in _DEFAULT_ORDER if latest[s]),
            "latestCertified": latest["certified"],
            "latestPreliminary": latest["preliminary"],
            "latestDraft": latest["draft"],
        }
    return out


def clear_cache() -> None:
    """Forget the last library read, so the next lookup reads every file again."""
    with _last_lock:
        _last.update(root=None, stamps=None, bundles=None, withheld=None)


def _stamp(path: Path):
    """``(mtime_ns, size)`` of a file, or None when it is absent."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _unchanged(stamps) -> bool:
    return stamps is not None and all(_stamp(path) == stamp for path, stamp in stamps.items())


def _read_eligible(root: Path) -> tuple[list[dict], frozenset, dict]:
    """The eligible bundles under ``root``, the refs of the DEEP versions withheld (their
    status is not eligible, or they are hidden), and the stamp of every file looked at (an
    absent one too, so its arrival is seen)."""
    stamps: dict = {}

    def present(path: Path) -> bool:
        stamps[str(path)] = _stamp(path)
        return stamps[str(path)] is not None

    catalog_path = root / "catalog.json"
    if not present(catalog_path):
        return [], frozenset(), stamps
    try:
        catalog = _read_json(catalog_path)
    except Exception:  # noqa: BLE001
        logger.exception("library: could not read catalog at %s", catalog_path)
        return [], frozenset(), stamps

    out: list[dict] = []
    withheld: set[str] = set()
    for entry in catalog.get("assessments") or []:
        aid = entry.get("assessmentId")
        if not aid or not is_deep(entry):
            continue
        manifest_path = root / "assessments" / aid / "manifest.json"
        if not present(manifest_path):
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:  # noqa: BLE001
            continue
        if not is_deep(manifest):
            continue
        present(root / "assessments" / aid / "status.json")
        smap = _status_map(root, aid)
        hidden = (_hidden(root, aid)
                  if present(root / "assessments" / aid / "visibility.json") else set())
        for v in manifest.get("versions") or []:
            try:
                ver = int(v.get("version"))
            except (TypeError, ValueError):
                continue
            status = smap.get(ver, "preliminary")
            if status not in _ELIGIBLE or ver in hidden:
                withheld.add(f"{aid}@v{ver}")
                continue
            bundle_path = root / "assessments" / aid / f"v{ver}" / "assessment.deep.json"
            if not present(bundle_path):
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
    return out, frozenset(withheld), stamps


def catalog_pointers() -> dict[str, dict]:
    """``{assessmentId: {defaultVersion, latestCertified, latestPreliminary}}`` from the
    library catalog: the version StreamCurves opens, whatever DEEP shows. The baked
    registry's ``libraryCatalog`` block comes from :func:`default_pointers` instead. Empty
    when the library is absent."""
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
        if not aid or latest < 1 or not is_deep(entry):
            continue
        out[aid] = {
            "defaultVersion": int(entry.get("defaultVersion") or latest),
            "latestCertified": int(entry.get("latestCertified") or 0),
            "latestPreliminary": int(entry.get("latestPreliminary") or 0),
        }
    return out
