"""The STAF data bundle from its rolling prerelease: the core first, the rest when first read.

``tools/hr-slim`` publishes the bundle (``hrbuild/release.py``) on the ``staf-data-current``
prerelease (always a prerelease) as ``release.json`` (uploaded last: the bundle's identity and
every asset's size and sha256), ``core.zip`` (the region manifest, the cross-region links, the
coverage outlines, the V2 part's manifest and links, the 3DEP tile catalogs), one
``tables-<file>`` per national table and one ``region-<vpu>.zip`` per region.

``bundle.py`` reads it from here when ``STAF_DATA_SOURCE`` is ``bundle`` or ``auto`` and no
``STAF_DATA_BUNDLE`` folder is set: the core on first use, a table or a region the first time a
request reads it (the reader's ``ensure`` hooks). ``STAF_DATA_RELEASE`` points at another https
base or a local folder (the builder's release folder); files land in ``STAF_DATA_CACHE``
(default: the temp folder's ``staf_data_bundle``). Every asset is checked against the manifest's
sha256 and kept, across restarts, until the release replaces it (a rebuilt region that did not
change keeps its file). An asset that cannot be had raises ``Unavailable`` in the reader; the
engine's wrappers catch it and the service answers, and the asset is not asked for again for
``RETRY_S``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

DEFAULT_BASE = "https://github.com/USACE-WRISES/staf/releases/download/staf-data-current/"
ENV_BASE = "STAF_DATA_RELEASE"
ENV_CACHE = "STAF_DATA_CACHE"
MANIFEST = "release.json"
FORMAT = 1
#: the cache's record of the assets unpacked into it (asset -> sha256 and files)
STATE = "assets.json"
TIMEOUT_S = 120.0
RETRY_S = 300.0


class Unavailable(Exception):
    """The release, or one of its assets, cannot be had now."""


def default_cache() -> Path:
    """``STAF_DATA_CACHE``, else the temp folder's ``staf_data_bundle`` (the 3DEP tile reader
    looks for its catalogs in the same place)."""
    return Path(os.environ.get(ENV_CACHE) or Path(tempfile.gettempdir()) / "staf_data_bundle")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Release:
    """One published bundle (an https base or a local folder) and its cache folder."""

    def __init__(self, base: Optional[str] = None, cache=None, *, timeout: float = TIMEOUT_S):
        base = base or os.environ.get(ENV_BASE) or DEFAULT_BASE
        self.remote = base.startswith(("http://", "https://"))
        self.base = base if not self.remote or base.endswith("/") else base + "/"
        self.local = None if self.remote else Path(base)
        self.cache = Path(cache) if cache is not None else default_cache()
        self.timeout = timeout
        self.manifest: Optional[dict] = None
        self._lock = threading.RLock()
        self._asset_locks: dict = {}
        self._have: dict = {}
        self._checked: set = set()
        self._failed: dict = {}

    # ------------------------------------------------------------------ manifest
    def _fetch_manifest(self) -> str:
        if self.local is not None:
            try:
                return (self.local / MANIFEST).read_text(encoding="utf-8")
            except OSError as exc:
                raise Unavailable(f"{MANIFEST}: {exc}") from exc
        import requests
        try:
            r = requests.get(self.base + MANIFEST, timeout=self.timeout)
        except requests.RequestException as exc:
            raise Unavailable(f"{MANIFEST}: {exc}") from exc
        if r.status_code != 200:
            raise Unavailable(f"{MANIFEST}: HTTP {r.status_code}")
        return r.text

    def prepare(self) -> Path:
        """The cache folder with the current release's core in it (raises ``Unavailable``). The
        published manifest wins; without it (offline) the copy the cache last saw serves."""
        with self._lock:
            if self.manifest is not None:
                return self.cache
            try:
                text = self._fetch_manifest()
            except Unavailable:
                saved = self.cache / MANIFEST
                if not saved.exists():
                    raise
                text = saved.read_text(encoding="utf-8")
            manifest = json.loads(text)
            if manifest.get("format") != FORMAT:
                raise Unavailable(f"release format {manifest.get('format')!r}")
            self.cache.mkdir(parents=True, exist_ok=True)
            self._have = self._load_state()
            self.manifest = manifest
            try:
                self._ensure_asset(manifest["core"])
            except Unavailable:
                self.manifest = None
                raise
            for sub in ("values", "v2", "tables"):
                (self.cache / sub).mkdir(exist_ok=True)
            (self.cache / MANIFEST).write_text(text, encoding="utf-8", newline="\n")
            return self.cache

    def describe(self) -> dict:
        m = self.manifest or {}
        return {"release": self.base if self.remote else str(self.local), "packed": m.get("packed"),
                "cache": str(self.cache)}

    # ------------------------------------------------------------------ assets
    def ensure_region(self, vpu: str) -> None:
        """Region ``vpu``'s files are in the cache when this returns (raises ``Unavailable``)."""
        asset = ((self.manifest or {}).get("regions") or {}).get(str(vpu))
        if asset is None:
            raise Unavailable(f"region {vpu} is not in the release")
        self._ensure_asset(asset)

    def ensure_table(self, name: str) -> None:
        """National table ``name`` (``tables/<name>``) is in the cache when this returns; a table
        the core carries is there already."""
        asset = ((self.manifest or {}).get("tables") or {}).get(str(name))
        if asset is None:
            if (self.cache / "tables" / str(name)).exists():
                return
            raise Unavailable(f"table {name} is not in the release")
        self._ensure_asset(asset)

    def _present(self, name: str, sha: str) -> bool:
        """Whether the cache holds the asset (its files checked once per process)."""
        have = self._have.get(name) or {}
        if have.get("sha256") != sha:
            return False
        if name in self._checked:
            return True
        if all((self.cache / f).is_file() for f in have.get("files") or ()):
            self._checked.add(name)
            return True
        return False

    def _ensure_asset(self, name: str) -> None:
        entry = ((self.manifest or {}).get("assets") or {}).get(name)
        if entry is None:
            raise Unavailable(f"{name} is not in the release")
        sha = entry["sha256"]
        if self._present(name, sha):
            return
        with self._lock:
            lock = self._asset_locks.setdefault(name, threading.Lock())
        with lock:
            if self._present(name, sha):
                return
            failed = self._failed.get(name)
            if failed is not None and time.monotonic() - failed < RETRY_S:
                raise Unavailable(f"{name} failed recently")
            try:
                path = self._download(name, sha)
                try:
                    files = self._unpack(name, path, entry)
                finally:
                    path.unlink(missing_ok=True)
            except Unavailable:
                self._failed[name] = time.monotonic()
                raise
            except Exception as exc:  # noqa: BLE001 - a broken asset reads as unavailable
                self._failed[name] = time.monotonic()
                raise Unavailable(f"{name}: {exc}") from exc
            with self._lock:
                self._have[name] = {"sha256": sha, "files": files}
                self._checked.add(name)
                self._failed.pop(name, None)
                self._save_state()

    def _download(self, name: str, sha: str) -> Path:
        folder = self.cache / ".download"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{name}.{os.getpid()}.{threading.get_ident()}.part"
        if self.local is not None:
            try:
                shutil.copyfile(self.local / name, tmp)
            except OSError as exc:
                raise Unavailable(f"{name}: {exc}") from exc
        else:
            import requests
            try:
                with requests.get(self.base + name, stream=True, timeout=self.timeout) as r:
                    if r.status_code != 200:
                        raise Unavailable(f"{name}: HTTP {r.status_code}")
                    with open(tmp, "wb") as handle:
                        for chunk in r.iter_content(1 << 20):
                            handle.write(chunk)
            except requests.RequestException as exc:
                tmp.unlink(missing_ok=True)
                raise Unavailable(f"{name}: {exc}") from exc
        if _sha256(tmp) != sha:
            tmp.unlink(missing_ok=True)
            raise Unavailable(f"{name}: sha256 differs from the release manifest")
        return tmp

    def _unpack(self, name: str, path: Path, entry: dict) -> list[str]:
        """Move the asset's files into the cache, each by an atomic replace; their paths."""
        if not name.endswith(".zip"):
            os.replace(path, self._target(entry["file"]))
            return [entry["file"]]
        staging = Path(tempfile.mkdtemp(prefix=".unpack-", dir=self.cache))
        try:
            with zipfile.ZipFile(path) as zf:
                members = [m for m in zf.namelist() if not m.endswith("/")]
                for m in members:
                    self._target(m)                      # refuse a name outside the cache
                zf.extractall(staging, members)
            for m in members:
                os.replace(staging / m, self._target(m))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return members

    def _target(self, rel: str) -> Path:
        parts = PurePosixPath(rel).parts
        if not parts or PurePosixPath(rel).is_absolute() or ".." in parts or ":" in rel:
            raise Unavailable(f"unsafe file name {rel!r} in the release")
        target = self.cache.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------------ state
    def _load_state(self) -> dict:
        try:
            data = json.loads((self.cache / STATE).read_text(encoding="utf-8"))
            return dict((str(k), {"sha256": str(v["sha256"]), "files": [str(f) for f in v["files"]]})
                        for k, v in data.items())
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            return {}

    def _save_state(self) -> None:
        tmp = self.cache / f"{STATE}.{os.getpid()}.part"
        tmp.write_text(json.dumps(self._have, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        os.replace(tmp, self.cache / STATE)
