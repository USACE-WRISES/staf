"""Assessments from the STAF library release, read while DEEP runs.

StreamCurves publishes every assessment version into ``apps/library`` and to a
rolling GitHub prerelease named ``library`` on USACE-WRISES/staf, so a running
DEEP picks up a new version without a redeploy. The release holds:

- ``library.json``, the catalog (schema 1), re-uploaded with ``--clobber`` on
  every publish. It can be missing for a moment, so a 404 on it is transient
  and the last good snapshot stays.
- per version, immutable and named with a content hash (the catalog gives
  each name): ``<id>-v<N>-<sha8>.deep.json`` (the ``assessment.deep.json``
  bytes) and an optional ``<id>-v<N>-calculator-<sha8>.xlsx``, plus the
  StreamCurves pack, which DEEP ignores.

This module keeps a *snapshot* in memory: every eligible (preliminary or
certified) version's bundle, stamped exactly as
:func:`deep.library.all_eligible_bundles` stamps the local library's
(``version``, ``lifecycle``, ``assessmentRef``); the refs the catalog lists with
any other status, so :func:`deep.config._registry_records` can drop a baked
version that was later revised or retired; and a ``generation`` that grows each
time the snapshot changes.

- :func:`snapshot` never waits on the network. Its first call loads the last
  good catalog and its cached assets from disk (no network), then starts a
  refresh; later calls start one when the last is older than the TTL. A
  refresh runs on a daemon thread, one at a time.
- A refresh fetches ``library.json``, validates it, and downloads only the
  assets the disk cache lacks (keyed by asset name and sha256; size and sha256
  are checked and a mismatch is rejected). Any failure keeps the previous
  snapshot, logs a warning, and is retried a minute later.
- Every request goes through :data:`_http_get`, which :func:`set_http_get`
  replaces, so the tests run offline.

Environment:

- ``DEEP_REMOTE_LIBRARY``: ``0``, ``false`` or ``off`` turns this off (the test
  suite does).
- ``DEEP_LIBRARY_URL``: an http(s) base ending in ``/``, or a local folder
  holding the same files. Default :data:`DEFAULT_URL`.
- ``DEEP_LIBRARY_TTL_S``: seconds between refreshes, default 600.
- ``DEEP_CACHE_DIR``: the disk cache, default ``<temp>/deep_remote_library``.

The baked registry stays the offline fallback: off, unreachable and never
cached, the snapshot is empty and DEEP serves exactly what was baked.
"""
from __future__ import annotations

import concurrent.futures
import datetime as _dt
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
import types
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import library as _library

logger = logging.getLogger("deep")

DEFAULT_URL = "https://github.com/USACE-WRISES/staf/releases/download/library/"
CATALOG_NAME = "library.json"
#: The catalog schema this DEEP reads; a larger number is never used.
SCHEMA = 1
#: The statuses DEEP serves: the local library's rule, shared so the two never differ.
ELIGIBLE = _library._ELIGIBLE
DEFAULT_TTL_S = 600.0
#: A failed refresh is retried after this long (or the TTL, when shorter): a 404
#: on library.json usually means a publish is replacing it.
RETRY_AFTER_FAILURE_S = 60.0
#: (connect, read) seconds for every request.
TIMEOUT = (5.0, 20.0)
#: Assets missing from the cache download this many at a time.
DOWNLOAD_WORKERS = 4
#: :func:`catch_up` starts a refresh at most this often.
CATCH_UP_INTERVAL_S = 30.0

ENV_SWITCH = "DEEP_REMOTE_LIBRARY"
ENV_URL = "DEEP_LIBRARY_URL"
ENV_TTL = "DEEP_LIBRARY_TTL_S"
ENV_CACHE = "DEEP_CACHE_DIR"
_OFF = ("0", "false", "off")

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
#: Asset names become cache file names, so nothing that can leave the folder.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


# --------------------------------------------------------------------------- #
# the network, in one replaceable function
# --------------------------------------------------------------------------- #
def _requests_get(url: str, timeout) -> tuple[int, bytes]:
    """GET ``url``: ``(status code, body)``. Redirects are followed (a release
    download redirects to GitHub's object store)."""
    import requests

    resp = requests.get(url, timeout=timeout,
                        headers={"User-Agent": "STAF-DEEP remote library"})
    return resp.status_code, resp.content


#: Every network request goes through this. :func:`set_http_get` swaps it.
_http_get: Callable[[str, object], tuple[int, bytes]] = _requests_get

#: The TTL clock (monotonic seconds); tests replace it.
_clock = time.monotonic


def set_http_get(fn: Optional[Callable]) -> Callable:
    """Route every request through ``fn(url, timeout) -> (status, bytes)``;
    ``None`` restores ``requests``. Returns the function it replaced."""
    global _http_get
    previous = _http_get
    _http_get = fn if fn is not None else _requests_get
    return previous


# --------------------------------------------------------------------------- #
# library.json
# --------------------------------------------------------------------------- #
class CatalogError(ValueError):
    """``library.json`` cannot be used; the previous snapshot stays."""


class NewerSchemaError(CatalogError):
    """``library.json`` declares a schema newer than :data:`SCHEMA`."""


class RefreshError(RuntimeError):
    """A refresh could not complete; the previous snapshot stays."""


@dataclass(frozen=True)
class Asset:
    """One release asset as the catalog describes it."""
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CatalogVersion:
    """One version of one assessment as the catalog lists it."""
    assessment_id: str
    version: int
    status: str
    content_digest: Optional[str] = None
    bundle: Optional[Asset] = None
    calculator: Optional[Asset] = None

    @property
    def ref(self) -> str:
        return f"{self.assessment_id}@v{self.version}"

    @property
    def eligible(self) -> bool:
        return self.status in ELIGIBLE


@dataclass(frozen=True)
class Catalog:
    schema: int
    generated_at: Optional[str]
    #: Catalog order of the assessments, each one's versions ascending.
    versions: tuple = ()


def _as_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _norm_sha(value) -> Optional[str]:
    """A sha256 as 64 lowercase hex characters (a ``sha256:`` prefix is allowed), or None."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    return text if _SHA_RE.fullmatch(text) else None


def _norm_digest(value) -> Optional[str]:
    """A content digest in one spelling: ``sha256:<hex>`` and ``<hex>`` compare equal."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()
    return text[len("sha256:"):] if text.startswith("sha256:") else text


def _parse_asset(raw) -> Optional[Asset]:
    if not isinstance(raw, dict):
        return None
    name, size, sha = raw.get("name"), _as_int(raw.get("size")), _norm_sha(raw.get("sha256"))
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        return None
    if size is None or size < 0 or sha is None:
        return None
    return Asset(name, size, sha)


def _parse_version(aid: str, raw) -> Optional[CatalogVersion]:
    number = _as_int(raw.get("version")) if isinstance(raw, dict) else None
    if number is None or number < 1:
        logger.warning("remote library: skipped a version of %s without a usable number", aid)
        return None
    ref = f"{aid}@v{number}"
    status = raw.get("status")
    status = status.strip().lower() if isinstance(status, str) else ""
    if not status:
        logger.warning("remote library: skipped %s, which has no status", ref)
        return None
    if status not in ELIGIBLE:
        return CatalogVersion(aid, number, status)
    assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    bundle = _parse_asset(assets.get("bundle"))
    if bundle is None:
        logger.warning("remote library: skipped %s, which has no usable bundle asset", ref)
        return None
    calculator = None
    if assets.get("calculator") is not None:
        calculator = _parse_asset(assets.get("calculator"))
        if calculator is None:
            logger.warning("remote library: %s lists an unusable calculator; "
                           "it is served without one", ref)
    return CatalogVersion(aid, number, status, _norm_digest(raw.get("contentDigest")),
                          bundle, calculator)


def parse_catalog(raw) -> Catalog:
    """Validate ``library.json`` (bytes or text) into a :class:`Catalog`.

    Raises :class:`CatalogError` when the document cannot be used at all and
    :class:`NewerSchemaError` when its schema is newer than :data:`SCHEMA`.
    Unknown fields are ignored. A malformed record is skipped with a warning so
    it never hides the rest: an entry without a usable ``id`` or ``versions``
    list, a version without a number or a status, and an eligible version
    without a usable bundle asset. A version whose status is neither
    preliminary nor certified is kept as *not eligible*.
    """
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else str(raw)
        doc = json.loads(text)
    except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise CatalogError(f"library.json is not valid JSON ({exc})") from None
    if not isinstance(doc, dict):
        raise CatalogError("library.json is not a JSON object")
    schema = _as_int(doc.get("schema"))
    if schema is None or schema < 1:
        raise CatalogError(f"library.json has no usable schema number ({doc.get('schema')!r})")
    if schema > SCHEMA:
        raise NewerSchemaError(f"library.json is schema {schema}, and this DEEP reads "
                               f"schema {SCHEMA}")
    entries = doc.get("assessments")
    if not isinstance(entries, list):
        raise CatalogError("library.json has no assessments list")

    versions: list[CatalogVersion] = []
    seen: set[str] = set()
    for entry in entries:
        aid = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(aid, str) or not _ID_RE.fullmatch(aid):
            logger.warning("remote library: skipped an assessment without a usable id (%r)", aid)
            continue
        if aid in seen:
            logger.warning("remote library: skipped a second entry for %s", aid)
            continue
        seen.add(aid)
        listed = entry.get("versions")
        if not isinstance(listed, list):
            logger.warning("remote library: skipped %s, which has no versions list", aid)
            continue
        by_number: dict[int, CatalogVersion] = {}
        for raw_version in listed:
            parsed = _parse_version(aid, raw_version)
            if parsed is None:
                continue
            if parsed.version in by_number:
                logger.warning("remote library: skipped a second %s", parsed.ref)
                continue
            by_number[parsed.version] = parsed
        versions.extend(by_number[n] for n in sorted(by_number))
    generated = doc.get("generatedAt")
    return Catalog(schema, generated if isinstance(generated, str) else None, tuple(versions))


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CalculatorFile:
    """A version's release calculator in the disk cache."""
    path: str
    #: The content digest of the bundle the workbook belongs to (the catalog's,
    #: else the bundle's own), in :func:`_norm_digest` spelling; None when unknown.
    content_digest: Optional[str]


@dataclass(frozen=True, eq=False)
class Snapshot:
    """What the remote library offers right now. Never changed once made, so it
    is read without a lock; a refresh installs a new one."""
    #: Eligible bundles, stamped like ``deep.library.all_eligible_bundles()``.
    records: tuple = ()
    #: ``id@vN`` refs the catalog lists with a status DEEP does not serve.
    ineligible_refs: frozenset = frozenset()
    #: ``id@vN`` -> :class:`CalculatorFile`, eligible versions only.
    calculators: Mapping[str, CalculatorFile] = field(
        default_factory=lambda: types.MappingProxyType({}))
    generation: int = 0
    generated_at: Optional[str] = None


EMPTY = Snapshot()


@dataclass(frozen=True)
class _Settings:
    enabled: bool
    base: str
    local: Optional[Path]
    cache_dir: Path


def _settings() -> _Settings:
    enabled = (os.environ.get(ENV_SWITCH) or "").strip().lower() not in _OFF
    base = (os.environ.get(ENV_URL) or "").strip() or DEFAULT_URL
    local = None
    if base.lower().startswith(("http://", "https://")):
        base = base if base.endswith("/") else base + "/"
    else:
        local = Path(base)
    cache = (os.environ.get(ENV_CACHE) or "").strip()
    cache_dir = Path(cache) if cache else Path(tempfile.gettempdir()) / "deep_remote_library"
    return _Settings(enabled, base, local, cache_dir)


def ttl_s() -> float:
    """Seconds between refreshes: ``DEEP_LIBRARY_TTL_S``, else 600."""
    raw = (os.environ.get(ENV_TTL) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TTL_S
    except ValueError:
        return DEFAULT_TTL_S
    return value if math.isfinite(value) and value >= 0 else DEFAULT_TTL_S


def _write_atomic(path: Path, data: bytes) -> None:
    """Write through a temporary file and ``os.replace``, so no reader (another
    DEEP process included) ever sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".part-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _matches(path: Path, asset: Asset) -> bool:
    """The cached file is this asset (size first, then sha256)."""
    try:
        if path.stat().st_size != asset.size:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == asset.sha256
    except OSError:
        return False


class _Library:
    """Snapshot and refresh state for one (source, cache folder) pair."""

    def __init__(self, settings: _Settings):
        self.settings = settings
        self.lock = threading.Lock()  # guards everything below but the warm start
        self.snapshot: Snapshot = EMPTY
        self.catalog_sha: Optional[str] = None
        self.files: tuple = ()  # the cache files the snapshot rests on
        self.thread: Optional[threading.Thread] = None
        self.started_at: Optional[float] = None
        self.failed = False
        self.last_error: Optional[str] = None
        self.last_success: Optional[float] = None
        self.warm_lock = threading.Lock()
        self.warm_done = False
        # bundle sha256 -> parsed bundle, reused across generations. Touched only
        # by the warm start and the refresh thread, which never overlap.
        self.parsed: dict[str, dict] = {}

    # -- the disk cache ------------------------------------------------------
    @property
    def catalog_file(self) -> Path:
        key = hashlib.sha256(self.settings.base.encode("utf-8")).hexdigest()[:16]
        return self.settings.cache_dir / f"library-{key}.json"

    def asset_file(self, asset: Asset) -> Path:
        return self.settings.cache_dir / "assets" / f"{asset.sha256}-{asset.name}"

    def fetch(self, name: str) -> tuple[int, bytes]:
        local = self.settings.local
        if local is not None:
            try:
                return 200, (local / name).read_bytes()
            except FileNotFoundError:
                return 404, b""
        return _http_get(self.settings.base + urllib.parse.quote(name), TIMEOUT)

    def warm_start(self) -> None:
        """Install the last good catalog from the disk cache, once, with no network."""
        if self.warm_done:
            return
        with self.warm_lock:
            if self.warm_done:
                return
            try:
                path = self.catalog_file
                if path.is_file():
                    body = path.read_bytes()
                    snap = self._install(parse_catalog(body), body, download=False)
                    logger.info("remote library: generation %d from the disk cache in %s "
                                "(%d eligible version(s))", snap.generation,
                                self.settings.cache_dir, len(snap.records))
            except Exception as exc:  # noqa: BLE001 - a bad cache only delays the first snapshot
                logger.warning("remote library: the disk cache in %s is not usable (%s); "
                               "waiting for the first refresh", self.settings.cache_dir, exc)
            finally:
                self.warm_done = True

    # -- building a snapshot -------------------------------------------------
    def _download(self, asset: Asset) -> Path:
        status, body = self.fetch(asset.name)
        if status != 200:
            raise RefreshError(f"HTTP {status}")
        if len(body) != asset.size:
            raise RefreshError(f"the size is {len(body)} bytes where the catalog says "
                               f"{asset.size}")
        if hashlib.sha256(body).hexdigest() != asset.sha256:
            raise RefreshError("the sha256 differs from the catalog's")
        path = self.asset_file(asset)
        _write_atomic(path, body)
        return path

    def _ensure(self, assets: list, *, download: bool) -> dict:
        """``{asset: cache file}``, downloading what the cache lacks (or, without
        ``download``, refusing). Raises when any asset is not there in the end."""
        files: dict = {}
        missing: list = []
        for asset in dict.fromkeys(assets):
            path = self.asset_file(asset)
            if _matches(path, asset):
                files[asset] = path
            else:
                missing.append(asset)
        if missing and not download:
            raise RefreshError(f"{len(missing)} asset(s) are missing from the disk cache")
        if missing:
            errors: list[str] = []
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(DOWNLOAD_WORKERS, len(missing)),
                    thread_name_prefix="deep-remote-library-download") as pool:
                jobs = {pool.submit(self._download, asset): asset for asset in missing}
                for job in concurrent.futures.as_completed(jobs):
                    asset = jobs[job]
                    try:
                        files[asset] = job.result()
                    except Exception as exc:  # noqa: BLE001 - reported together below
                        errors.append(f"{asset.name}: {exc}")
            if errors:
                raise RefreshError("could not fetch " + "; ".join(sorted(errors)))
        return files

    def _bundle(self, version: CatalogVersion, path: Path) -> Optional[dict]:
        """The parsed bundle, or None (with a warning) when it cannot be served.
        Its bytes are what the catalog names, so a bad one stays bad: skipped."""
        doc = self.parsed.get(version.bundle.sha256)
        if doc is None:
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise RefreshError(f"could not read the cached {version.bundle.name} ({exc})")
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except ValueError as exc:
                logger.warning("remote library: skipped %s, whose bundle is not JSON (%s)",
                               version.ref, exc)
                return None
            if not isinstance(doc, dict):
                logger.warning("remote library: skipped %s, whose bundle is not an object",
                               version.ref)
                return None
            self.parsed[version.bundle.sha256] = doc
        if doc.get("assessmentId") != version.assessment_id:
            logger.warning("remote library: skipped %s, whose bundle is for %r",
                           version.ref, doc.get("assessmentId"))
            return None
        return doc

    def _install(self, catalog: Catalog, body: bytes, *, download: bool) -> Snapshot:
        """Build the snapshot ``catalog`` describes and make it current. Raises,
        leaving the current snapshot alone, when any eligible asset is missing."""
        eligible = [v for v in catalog.versions if v.eligible]
        wanted: list = []
        for v in eligible:
            wanted.append(v.bundle)
            if v.calculator is not None:
                wanted.append(v.calculator)
        files = self._ensure(wanted, download=download)

        records: list[dict] = []
        calculators: dict[str, CalculatorFile] = {}
        kept: set[str] = set()
        for v in eligible:
            bundle = self._bundle(v, files[v.bundle])
            if bundle is None:
                continue
            kept.add(v.bundle.sha256)
            record = dict(bundle)
            # The stamp of deep.library.all_eligible_bundles(), exactly.
            record["version"] = v.version
            record["lifecycle"] = v.status
            record["assessmentRef"] = v.ref
            records.append(record)
            if v.calculator is not None:
                calculators[v.ref] = CalculatorFile(
                    str(files[v.calculator]),
                    v.content_digest or _norm_digest(bundle.get("contentDigest")))
        if download:
            _write_atomic(self.catalog_file, body)
        with self.lock:
            snap = Snapshot(
                records=tuple(records),
                ineligible_refs=frozenset(v.ref for v in catalog.versions if not v.eligible),
                calculators=types.MappingProxyType(calculators),
                generation=self.snapshot.generation + 1,
                generated_at=catalog.generated_at)
            self.snapshot = snap
            self.catalog_sha = hashlib.sha256(body).hexdigest()
            self.files = tuple(dict.fromkeys(files.values()))
        self.parsed = {sha: doc for sha, doc in self.parsed.items() if sha in kept}
        return snap

    # -- refreshing ----------------------------------------------------------
    def refresh(self) -> None:
        """One refresh, on the refresh thread. Raises on any failure."""
        status, body = self.fetch(CATALOG_NAME)
        if status == 404:
            raise RefreshError("library.json is not in the release (HTTP 404); "
                               "a publish may be replacing it")
        if status != 200:
            raise RefreshError(f"library.json answered HTTP {status}")
        catalog = parse_catalog(body)
        if (hashlib.sha256(body).hexdigest() == self.catalog_sha
                and all(Path(p).is_file() for p in self.files)):
            return  # unchanged
        snap = self._install(catalog, body, download=True)
        logger.info("remote library: generation %d from %s (%d eligible version(s), "
                    "%d not eligible)", snap.generation, self.settings.base,
                    len(snap.records), len(snap.ineligible_refs))

    def _run(self) -> None:
        error: Optional[str] = None
        retry_soon = False
        try:
            self.refresh()
        except NewerSchemaError as exc:  # nothing to retry until DEEP learns the schema
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - any failure keeps the snapshot
            error, retry_soon = (str(exc) or type(exc).__name__), True
        finally:
            with self.lock:
                repeated = error is not None and error == self.last_error
                self.failed = retry_soon
                self.last_error = error
                if error is None:
                    self.last_success = time.time()
                self.thread = None
        if error is not None:
            # One warning per distinct failure: a release that is not there yet
            # would otherwise warn every minute.
            (logger.debug if repeated else logger.warning)(
                "remote library: refresh from %s failed (%s); keeping the last snapshot "
                "(generation %d)", self.settings.base, error, self.snapshot.generation)

    def start(self, *, force: bool = False) -> Optional[threading.Thread]:
        """The refresh in flight; else a new one when ``force`` or due; else None."""
        with self.lock:
            if self.thread is not None:
                return self.thread
            now = _clock()
            if not force and self.started_at is not None:
                wait = ttl_s()
                if self.failed:
                    wait = min(wait, RETRY_AFTER_FAILURE_S)
                if now - self.started_at < wait:
                    return None
            thread = threading.Thread(target=self._run, name="deep-remote-library",
                                      daemon=True)
            self.thread, self.started_at = thread, now
            try:
                thread.start()
            except RuntimeError:  # the interpreter is shutting down
                self.thread = None
                return None
            return thread


_state_lock = threading.Lock()
_state: Optional[_Library] = None


def _library_for(settings: _Settings) -> _Library:
    """The state for these settings (new state when they changed), warm-started."""
    global _state
    with _state_lock:
        lib = _state
        if lib is None or lib.settings != settings:
            lib = _state = _Library(settings)
    lib.warm_start()
    return lib


# --------------------------------------------------------------------------- #
# public surface
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    """False when ``DEEP_REMOTE_LIBRARY`` is ``0``, ``false`` or ``off``."""
    return _settings().enabled


def snapshot() -> Snapshot:
    """The current snapshot, at once; :data:`EMPTY` when off or before anything
    arrived. The first call loads the disk cache (no network); any call may
    start a refresh on the refresh thread when the last is older than the TTL.
    Never raises."""
    settings = _settings()
    if not settings.enabled:
        return EMPTY
    try:
        lib = _library_for(settings)
        lib.start()
        return lib.snapshot
    except Exception:  # noqa: BLE001 - the registry must never fail because of this
        logger.exception("remote library: snapshot failed")
        return EMPTY


def refresh(*, wait: bool = False, timeout: Optional[float] = None) -> bool:
    """Start a refresh now, TTL or not, unless one is running (that one then
    counts). The fetch runs on the refresh thread; ``wait`` blocks until it ends
    or ``timeout`` passes. False when off, or when the wait timed out."""
    settings = _settings()
    if not settings.enabled:
        return False
    thread = _library_for(settings).start(force=True)
    if thread is None:
        return False
    if wait:
        thread.join(timeout)
        return not thread.is_alive()
    return True


def catch_up(timeout: float) -> bool:
    """Wait up to ``timeout`` seconds for a refresh, for a ref DEEP does not know.

    A link to a version published after the last refresh, or opened while the
    first refresh after a start is still running, would otherwise fall back to
    another version. A refresh already running is waited for; otherwise one is
    started, unless the last one started less than :data:`CATCH_UP_INTERVAL_S`
    ago, so a stream of unknown refs cannot hammer the release. The fetch still
    runs on the refresh thread. True when a newer snapshot arrived.
    """
    settings = _settings()
    if not settings.enabled:
        return False
    try:
        lib = _library_for(settings)
        before = lib.snapshot.generation
        with lib.lock:
            running = lib.thread
            recent = (lib.started_at is not None
                      and _clock() - lib.started_at < CATCH_UP_INTERVAL_S)
        if running is None:
            if recent:
                return False
            running = lib.start(force=True)
            if running is None:
                return False
        running.join(max(0.0, float(timeout)))
        return lib.snapshot.generation != before
    except Exception:  # noqa: BLE001 - a lookup must never fail because of this
        logger.exception("remote library: catch-up failed")
        return False


def calculator_path(assessment_id: str, version,
                    content_digest: Optional[str] = None) -> Optional[Path]:
    """The cached release calculator of ``assessment_id`` version ``version``, or None.

    Only the eligible versions of the current snapshot have one (downloaded and
    sha256-checked by the refresh). With ``content_digest`` (the loaded
    bundle's) the path comes back only when the workbook belongs to that
    content: the catalog's digest for the version, else its bundle's own, must
    equal it, so a workbook built for other curves is never handed out.
    """
    try:
        ref = f"{assessment_id}@v{int(version)}"
    except (TypeError, ValueError):
        return None
    entry = snapshot().calculators.get(ref)
    if entry is None:
        return None
    if content_digest is not None and (
            not entry.content_digest or _norm_digest(content_digest) != entry.content_digest):
        return None
    path = Path(entry.path)
    return path if path.is_file() else None


def status() -> dict:
    """Where the remote library stands, for logs and diagnostics. Starts nothing."""
    settings = _settings()
    out = {"enabled": settings.enabled, "source": settings.base,
           "cacheDir": str(settings.cache_dir), "ttlSeconds": ttl_s(),
           "generation": 0, "generatedAt": None, "records": 0, "notEligible": 0,
           "calculators": 0, "refreshing": False, "lastSuccess": None, "lastError": None}
    with _state_lock:
        lib = _state if _state is not None and _state.settings == settings else None
    if not settings.enabled or lib is None:
        return out
    with lib.lock:
        snap = lib.snapshot
        out.update(generation=snap.generation, generatedAt=snap.generated_at,
                   records=len(snap.records), notEligible=len(snap.ineligible_refs),
                   calculators=len(snap.calculators), refreshing=lib.thread is not None,
                   lastError=lib.last_error,
                   lastSuccess=(_dt.datetime.fromtimestamp(lib.last_success, _dt.timezone.utc)
                                .isoformat(timespec="seconds") if lib.last_success else None))
    return out


def reset(timeout: float = 10.0) -> None:
    """Forget the in-memory state after letting a refresh in flight finish (the
    disk cache stays, as across a restart). For tests."""
    global _state
    with _state_lock:
        lib, _state = _state, None
    thread = lib.thread if lib is not None else None
    if thread is not None:
        thread.join(timeout)
