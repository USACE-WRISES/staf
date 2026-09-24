"""The assessment gallery: what the start page's Assessment library lists, and how a version gets
from the library to a user's computer. After HYPE Desktop's hype_app/examples.py.

The library itself is `apps/library` in the STAF repository (streamcurves.library). It reaches
people two ways:

* a CHECKOUT (a maintainer, or anyone running from the repository) reads it in place:
  :func:`entries_from_library` builds the catalog from the files, and opening a version builds
  its project straight from the version folder (:func:`pack_bytes`);
* every INSTALLED copy reads the rolling GitHub prerelease `library`, which
  scripts/library_release.py rebuilds from `apps/library` whenever the library changes:
  `library.json` (the catalog, :func:`catalog_doc`) plus immutable per-version assets, among
  them the pack `<id>-v<N>-p1.streamcurves` a download fetches (:func:`fetch_pack`). The
  library snapshot the app payload ships stands in for the catalog when there is no network.

A pack is a project file (streamcurves.project_file) with `desktop_project: false`; opening
one imports it into a project folder.

Downloads stream to `<name>.part` with an HTTP Range resume and are renamed into place only
when size AND sha256 match. Everything network-facing goes through one injectable function
(:data:`http_get`), so the flow is tested offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import library as lib
from . import project_file as pf
from .desktop_env import data_root
from .version import REPO

CATALOG_SCHEMA = 1
RELEASE_TAG = "library"
CATALOG_NAME = "library.json"
#: The typed feed: every assessment with its type (DEEP detailed assessments and EASI
#: screening methods). library.json stays the DEEP-only schema-1 feed StreamCurves 1.0.0 and
#: DEEP read; this app reads library-v2.json and falls back to library.json.
CATALOG_SCHEMA_V2 = 2
CATALOG_NAME_V2 = "library-v2.json"
ASSESSMENT_GROUPS = {"easi": "EASI screening methods"}
PUBLIC_BASE_URL = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}/"
#: A folder or an http(s) base holding the same files (a mirror, or a local E2E server).
BASE_URL_ENV = "STREAMCURVES_LIBRARY_BASE_URL"
CATALOG_TTL_S = 6 * 3600
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_CHUNK = 256 * 1024

STATUS_ORDER = ("certified", "preliminary", "draft")
#: The statuses DEEP serves (apps/deep/deep/library.py _ELIGIBLE).
DEEP_STATUSES = ("preliminary", "certified")


class GalleryError(Exception):
    """A download or catalog failure with a user-facing message."""


class GalleryGone(GalleryError):
    """The asset is no longer published (HTTP 404)."""


class GalleryCancelled(GalleryError):
    """The download was cancelled; its partial file is kept for a resume."""


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Version:
    version: int
    status: str
    status_label: str
    validation: str
    validation_label: str
    published_at: str | None
    published_by: str | None
    content_digest: str | None
    metrics: int | None
    functions_covered: int | None
    revision_notes: str | None
    assets: dict = field(default_factory=dict)       # kind -> Asset
    assessment_type: str = "deep"
    method_version: str | None = None                # an EASI version: the identity EASI reports

    @property
    def in_deep(self) -> bool:
        return self.assessment_type == "deep" and self.status in DEEP_STATUSES

    @property
    def published_display(self) -> str:
        try:
            dt = datetime.fromisoformat(str(self.published_at).replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y").replace(" 0", " ")
        except (TypeError, ValueError):
            return ""


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    region: dict
    latest_version: int
    default_version: int
    versions: tuple            # Version, newest first
    type: str = "deep"         # "deep" | "easi"

    @property
    def group(self) -> str:
        if self.type in ASSESSMENT_GROUPS:
            return ASSESSMENT_GROUPS[self.type]
        kind = str((self.region or {}).get("kind") or "")
        return {"ecoregion": "Ecoregions", "state": "States"}.get(kind, "Other regions")

    def version(self, v: int | None = None) -> Version | None:
        want = int(v if v is not None else self.latest_version)
        return next((x for x in self.versions if x.version == want), None)

    @property
    def region_line(self) -> str:
        r = self.region or {}
        kind, code, name = r.get("kind"), r.get("code"), r.get("name")
        if kind == "ecoregion" and code:
            return f"{name or code} (Level III {code})"
        if kind == "state" and code:
            return f"{name or code} (state)"
        if kind == "national":
            return f"{name or code} (one national method)"
        return str(name or "Custom region")


# --------------------------------------------------------------------------- #
# From the library files (a checkout, the payload snapshot, the release builder)
# --------------------------------------------------------------------------- #
def _bundle_counts(bundle: dict | None) -> tuple[int | None, int | None]:
    """(distinct metrics, functions covered) of a DEEP bundle: metrics are listed per
    function in metricsByFunction; functionCoverage.covered is the count the publish gate
    checked (deep_export)."""
    if not isinstance(bundle, dict):
        return None, None
    mbf = bundle.get("metricsByFunction")
    ids: set[str] = set()
    n_funcs_with_metrics = 0
    if isinstance(mbf, list):
        for fn in mbf:
            ms = (fn or {}).get("metrics") or []
            if ms:
                n_funcs_with_metrics += 1
            ids.update(str(m.get("metricId")) for m in ms if isinstance(m, dict))
    cov = bundle.get("functionCoverage") or {}
    covered = cov.get("covered") if isinstance(cov, dict) else None
    if not isinstance(covered, int):
        covered = n_funcs_with_metrics if isinstance(mbf, list) else None
    return (len(ids) if isinstance(mbf, list) else None), covered


def _easi_counts(aid: str, v: int) -> tuple[int | None, int | None]:
    """(methods, functions) of an EASI method version from its catalog and metrics files."""
    try:
        vdir = lib.version_dir(aid, v) / lib.EASI_METHOD_DIR
        cat = json.loads((vdir / "screening-methods.json").read_text(encoding="utf-8"))
        metrics = json.loads((vdir / "easi-metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    fids = {m.get("functionId") for m in metrics.get("metrics") or [] if m.get("functionId")}
    return len(cat.get("methods") or []), len(fids)


def version_from_library(aid: str, row: dict, *, assets: dict | None = None,
                         assessment_type: str = "deep") -> Version:
    """One version's catalog row from its library files."""
    v = int(row.get("version") or 0)
    status = lib.version_status(aid, v)
    validation = lib.version_validation_state(aid, v)
    if assessment_type == "easi":
        n_metrics, covered = _easi_counts(aid, v)
    else:
        try:
            bundle = lib.load_version_bundle(aid, v)
        except Exception:  # noqa: BLE001 - a version without a bundle still lists
            bundle = None
        n_metrics, covered = _bundle_counts(bundle)
    return Version(
        version=v, status=status, status_label=lib.status_label(status),
        validation=validation, validation_label=lib.validation_label(validation),
        published_at=row.get("updatedAt"), published_by=row.get("author") or None,
        content_digest=row.get("contentDigest") or lib.version_content_digest(aid, v),
        metrics=n_metrics, functions_covered=covered,
        revision_notes=(row.get("revisionNotes") or None),
        assets=dict(assets or {}), assessment_type=assessment_type,
        method_version=(row.get("methodVersion") or None) if assessment_type == "easi" else None)


def entries_from_library(*, assets_for: Callable[[str, int], dict] | None = None) -> list[Entry]:
    """The catalog built from the library files (library.library_root())."""
    out: list[Entry] = []
    for a in lib.list_assessments():
        aid = str(a.get("assessmentId") or "")
        if not aid:
            continue
        manifest = lib.read_manifest(aid) or {}
        atype = lib.entry_type(manifest)
        rows = sorted(manifest.get("versions") or [], key=lambda r: -int(r.get("version") or 0))
        versions = tuple(version_from_library(aid, r, assets=(assets_for(aid, int(r["version"]))
                                                              if assets_for else None),
                                              assessment_type=atype)
                         for r in rows if int(r.get("version") or 0) > 0)
        if not versions:
            continue
        out.append(Entry(
            id=aid, name=str(a.get("assessmentName") or manifest.get("assessmentName") or aid),
            region=dict(a.get("region") or manifest.get("region") or {}),
            latest_version=int(a.get("latestVersion") or versions[0].version),
            default_version=int(a.get("defaultVersion") or versions[0].version),
            versions=versions, type=atype))
    return sorted(out, key=lambda e: (e.group, e.name.lower()))


def origin_files(aid: str, version: int) -> dict[str, bytes]:
    """The immutable files a pack carries (project_file.ORIGIN_FILES) from a version folder.

    Read as text (newlines normalized, then UTF-8), so a checkout with CRLF files and one
    with LF files build the same pack bytes."""
    vdir = lib.version_dir(aid, int(version))
    out = {}
    for name in pf.ORIGIN_FILES:
        p = vdir / name
        if p.is_file():
            out[name] = p.read_text(encoding="utf-8").encode("utf-8")
    return out


def origin_meta(entry: Entry, version: Version, *, source: str) -> dict:
    """A pack's (and its working copy's) origin block."""
    return {"kind": "library", "assessmentId": entry.id, "assessmentName": entry.name,
            "version": version.version, "status": version.status,
            "statusLabel": version.status_label, "contentDigest": version.content_digest,
            "region": dict(entry.region or {}), "source": source}


def pack_bytes(entry: Entry, version: Version, *, source: str = "release") -> bytes:
    """The byte-deterministic pack of one library version."""
    if entry.type == "easi":
        return _easi_pack_bytes(entry, version, source=source)
    vdir = lib.version_dir(entry.id, version.version)
    session_text = (vdir / lib.SESSION_FILE).read_text(encoding="utf-8")
    meta = {"project_name": f"{entry.name} v{version.version}",
            "origin": origin_meta(entry, version, source=source)}
    return pf.build_bytes(meta=meta, session_text=session_text,
                          origin=origin_files(entry.id, version.version),
                          desktop_project=False, deterministic=True,
                          min_format=pf.session_text_format(session_text, pack=True))


def _easi_pack_bytes(entry: Entry, version: Version, *, source: str) -> bytes:
    """An EASI method version's pack: a format-2 project holding the method files byte for
    byte and the version's authoring record (StreamCurves 1.0.0 refuses it cleanly)."""
    from .easi_method import io as eio
    project = eio.open_version(entry.id, version.version)
    meta = {"project_name": f"{entry.name} v{version.version}",
            "origin": origin_meta(entry, version, source=source),
            "region": dict(entry.region or {})}
    session_text = pf.session_text_from_fields({}, session_name=meta["project_name"])
    return pf.build_bytes(meta=meta, session_text=session_text,
                          origin=origin_files(entry.id, version.version),
                          desktop_project=False, deterministic=True,
                          parts=project.to_parts(), assessment_type="easi")


def pack_name(entry: Entry, version: Version, sha256: str) -> str:
    """The pack's asset name: its pack schema (-p1- DEEP, -p2- EASI) and a content hash."""
    schema = pf.PACK_SCHEMA_TYPED if entry.type != "deep" else pf.PACK_SCHEMA
    return pf.pack_asset_name(entry.id, version.version, sha256, schema=schema)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_doc(entries: Iterable[Entry], *, source_commit: str | None = None,
                schema: int = CATALOG_SCHEMA) -> dict:
    """library.json (schema 1): every DEEP assessment, every version, every asset; or
    library-v2.json (schema 2): every assessment of every type, each entry typed."""
    doc = {"schema": int(schema),
           "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": {"repo": REPO, "commit": source_commit},
           "assessments": []}
    for e in entries:
        if schema == CATALOG_SCHEMA and e.type != "deep":
            continue                       # the schema-1 feed never lists another type
        doc["assessments"].append({
            **({"type": e.type} if schema >= CATALOG_SCHEMA_V2 else {}),
            "id": e.id, "name": e.name, "region": e.region,
            "latestVersion": e.latest_version, "defaultVersion": e.default_version,
            "versions": [{
                "version": v.version, "status": v.status, "statusLabel": v.status_label,
                "validation": v.validation, "validationLabel": v.validation_label,
                "publishedAt": v.published_at, "publishedBy": v.published_by,
                "contentDigest": v.content_digest, "metrics": v.metrics,
                "functionsCovered": v.functions_covered, "revisionNotes": v.revision_notes,
                "assets": {k: ({"name": a.name, "size": a.size, "sha256": a.sha256}
                               if a is not None else None) for k, a in v.assets.items()},
                **({"methodVersion": v.method_version} if e.type == "easi" else {}),
            } for v in e.versions],
        })
    return doc


# --------------------------------------------------------------------------- #
# Reading a published catalog
# --------------------------------------------------------------------------- #
def _asset(raw) -> Asset | None:
    if not isinstance(raw, dict):
        return None
    name, sha, size = str(raw.get("name") or ""), str(raw.get("sha256") or ""), raw.get("size")
    if not name or "/" in name or "\\" in name or not _SHA_RE.match(sha):
        return None
    if not isinstance(size, int) or size <= 0:
        return None
    return Asset(name=name, size=size, sha256=sha)


def parse_catalog(text: str) -> list[Entry]:
    """Entries of a library.json. Unknown fields are ignored; a bad row is skipped; a catalog
    newer than this app understands raises (the caller keeps what it had)."""
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("not a library catalog")
    schema = doc.get("schema")
    if not isinstance(schema, int) or schema < 1:
        raise ValueError("not a library catalog")
    if schema > CATALOG_SCHEMA_V2:
        raise ValueError(f"library catalog schema {schema} is newer than this app reads")
    out: list[Entry] = []
    for a in doc.get("assessments") or []:
        try:
            aid = str(a.get("id") or "")
            if not _ID_RE.match(aid):
                continue
            atype = str(a.get("type") or "deep")
            if atype not in ("deep", "easi"):
                continue                   # a type this app does not know is never guessed at
            versions = []
            for v in a.get("versions") or []:
                n = int(v.get("version") or 0)
                if n < 1:
                    continue
                assets = {k: _asset(x) for k, x in (v.get("assets") or {}).items()}
                versions.append(Version(
                    version=n, status=str(v.get("status") or "preliminary"),
                    status_label=str(v.get("statusLabel") or lib.status_label(v.get("status"))),
                    validation=str(v.get("validation") or "unvalidated"),
                    validation_label=str(v.get("validationLabel")
                                         or lib.validation_label(v.get("validation"))),
                    published_at=v.get("publishedAt"), published_by=v.get("publishedBy"),
                    content_digest=v.get("contentDigest"),
                    metrics=v.get("metrics") if isinstance(v.get("metrics"), int) else None,
                    functions_covered=(v.get("functionsCovered")
                                       if isinstance(v.get("functionsCovered"), int) else None),
                    revision_notes=v.get("revisionNotes") or None,
                    assets={k: x for k, x in assets.items() if x is not None},
                    assessment_type=atype,
                    method_version=(str(v.get("methodVersion")) if atype == "easi"
                                    and v.get("methodVersion") else None)))
            if not versions:
                continue
            versions.sort(key=lambda x: -x.version)
            out.append(Entry(id=aid, name=str(a.get("name") or aid),
                             region=dict(a.get("region") or {}),
                             latest_version=int(a.get("latestVersion") or versions[0].version),
                             default_version=int(a.get("defaultVersion") or versions[0].version),
                             versions=tuple(versions), type=atype))
        except (TypeError, ValueError, AttributeError):
            continue
    return sorted(out, key=lambda e: (e.group, e.name.lower()))


# --------------------------------------------------------------------------- #
# The network (one injectable function) and the per-user cache
# --------------------------------------------------------------------------- #
def _default_http_get(url: str, *, headers: dict | None = None, stream: bool = False,
                      timeout: float = 30.0):
    import requests
    return requests.get(url, headers=headers or {}, stream=stream, timeout=timeout)


#: Replaced in tests: (url, headers=, stream=, timeout=) -> an object with .status_code,
#: .content, .iter_content(n), .headers and .close().
http_get = _default_http_get


def base_url() -> str:
    b = os.environ.get(BASE_URL_ENV, "").strip()
    if not b:
        return PUBLIC_BASE_URL
    return b if b.endswith(("/", "\\")) else b + "/"


def _is_folder(base: str) -> bool:
    return not base.lower().startswith(("http://", "https://"))


def cache_dir() -> Path:
    return data_root() / "gallery"


def _catalog_cache() -> Path:
    return cache_dir() / CATALOG_NAME


def _catalog_cache_v2() -> Path:
    return cache_dir() / CATALOG_NAME_V2


_LOCK = threading.Lock()


def cached_catalog() -> tuple[list[Entry], float] | None:
    """(entries, fetched-at epoch seconds) of the typed feed's cached copy whenever it parses,
    else of ``library.json``'s, or None. A ``library.json`` an older StreamCurves refreshed in
    the same cache folder never outranks the typed copy (it lists DEEP only, so EASI versions
    would vanish until the next refresh); a withdrawn typed feed's copy is deleted by
    :func:`refresh_catalog`."""
    for p in (_catalog_cache_v2(), _catalog_cache()):
        try:
            return parse_catalog(p.read_text(encoding="utf-8")), p.stat().st_mtime
        except (OSError, ValueError):
            continue
    return None


def catalog_stale(ttl_s: float = CATALOG_TTL_S) -> bool:
    got = cached_catalog()
    return got is None or (time.time() - got[1]) > ttl_s


def refresh_catalog(*, force: bool = False) -> list[Entry]:
    """Fetch library.json (at most every CATALOG_TTL_S unless forced); returns the entries.
    Raises GalleryError on failure (the caller keeps showing what it had). Blocking: call it
    from a worker thread."""
    with _LOCK:
        if not force and not catalog_stale():
            return cached_catalog()[0]
        base = base_url()
        text, name = None, None
        for candidate in (CATALOG_NAME_V2, CATALOG_NAME):
            got = _read_catalog_text(base, candidate)
            if got is not None:
                text, name = got, candidate
                break
            if candidate == CATALOG_NAME_V2:
                # the typed feed is not published (withdrawn, or a release from before it):
                # its cached copy must not outlive it
                try:
                    _catalog_cache_v2().unlink()
                except OSError:
                    pass
        if text is None:
            raise GalleryError("The assessment library is being updated. Try again in a minute.")
        try:
            entries = parse_catalog(text)
        except ValueError as e:
            raise GalleryError(f"The assessment library catalog could not be read ({e}).") from e
        pf.atomic_write(_catalog_cache_v2() if name == CATALOG_NAME_V2 else _catalog_cache(),
                        text.encode("utf-8"))
        return entries


def _read_catalog_text(base: str, name: str) -> str | None:
    """One catalog's text, None when that catalog is not published (the typed feed is absent
    from a release built before it existed, so the schema-1 feed is read instead)."""
    if _is_folder(base):
        p = Path(base) / name
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError as e:
            raise GalleryError(f"The assessment library could not be read ({e}).") from e
    try:
        r = http_get(base + name, timeout=20.0)
    except Exception as e:  # noqa: BLE001
        raise GalleryError("The assessment library could not be reached. Check the "
                           "internet connection and try again.") from e
    try:
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GalleryError(f"The assessment library answered {r.status_code}.")
        return r.content.decode("utf-8")
    finally:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass


def snapshot_entries() -> list[Entry]:
    """The catalog of the library this app ships (the offline fallback), or []."""
    try:
        return entries_from_library()
    except Exception:  # noqa: BLE001
        return []


def load_catalog() -> tuple[list[Entry], str]:
    """(entries, where from) without touching the network: the cached download, else the
    shipped snapshot. The start page refreshes the cache off the event loop."""
    got = cached_catalog()
    if got is not None and got[0]:
        return got[0], "release"
    return snapshot_entries(), "snapshot"


def packs_dir() -> Path:
    return cache_dir() / "packs"


def cached_pack(asset: Asset) -> Path | None:
    p = packs_dir() / asset.name
    try:
        if p.is_file() and p.stat().st_size == asset.size:
            return p
    except OSError:
        pass
    return None


ProgressFn = Callable[[int, int], None]


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_pack(asset: Asset, *, progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> Path:
    """Download (or reuse) one pack, verified by size and sha256. Blocking: call it from a
    worker thread. Cancel keeps the .part for a resume; a mismatch deletes it."""
    hit = cached_pack(asset)
    if hit is not None and _sha256_file(hit) == asset.sha256:
        return hit
    out_dir = packs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / asset.name
    part = out_dir / (asset.name + ".part")
    base = base_url()
    if _is_folder(base):
        src = Path(base) / asset.name
        if not src.is_file():
            raise GalleryGone("This assessment version is no longer available. Refresh the "
                              "library and try again.")
        data = src.read_bytes()
        if len(data) != asset.size or sha256_bytes(data) != asset.sha256:
            raise GalleryError("The downloaded assessment did not match the library record.")
        pf.atomic_write(dest, data)
        if progress:
            progress(asset.size, asset.size)
        return dest
    have = part.stat().st_size if part.is_file() else 0
    if have > asset.size:
        part.unlink(missing_ok=True)
        have = 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        r = http_get(base + asset.name, headers=headers, stream=True, timeout=60.0)
    except Exception as e:  # noqa: BLE001
        raise GalleryError("The download could not start. Check the internet connection and "
                           "try again.") from e
    try:
        if r.status_code == 404:
            raise GalleryGone("This assessment version is no longer available. Refresh the "
                              "library and try again.")
        if r.status_code == 200 and have:
            have = 0                      # the server ignored the range: start over
        elif r.status_code not in (200, 206):
            raise GalleryError(f"The download failed (HTTP {r.status_code}).")
        with open(part, "ab" if have else "wb") as fh:
            got = have
            for chunk in r.iter_content(_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise GalleryCancelled("Download cancelled.")
                if not chunk:
                    continue
                fh.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, asset.size)
    finally:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass
    if part.stat().st_size != asset.size or _sha256_file(part) != asset.sha256:
        part.unlink(missing_ok=True)
        raise GalleryError("The downloaded assessment did not match the library record. Try "
                           "again.")
    os.replace(part, dest)
    return dest


__all__ = ["CATALOG_SCHEMA", "CATALOG_SCHEMA_V2", "CATALOG_NAME_V2", "pack_name",
           "RELEASE_TAG", "CATALOG_NAME", "PUBLIC_BASE_URL", "BASE_URL_ENV",
           "DEEP_STATUSES", "GalleryError", "GalleryGone", "GalleryCancelled", "Asset",
           "Version", "Entry", "entries_from_library", "version_from_library", "origin_files",
           "origin_meta", "pack_bytes", "sha256_bytes", "catalog_doc", "parse_catalog",
           "http_get", "base_url", "cache_dir", "cached_catalog", "catalog_stale",
           "refresh_catalog", "snapshot_entries", "load_catalog", "packs_dir", "cached_pack",
           "fetch_pack"]
