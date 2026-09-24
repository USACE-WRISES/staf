"""Evidence packages: verified before use, installed once, reused offline.

An evidence package (AUTHORING.md, "Evidence") is ``evidence.json`` plus ``data/`` files,
distributed as a zip ``<packageId>-<version>-<sha8>.evidence.zip``. Its identity is its
content: ``dataDigest`` = SHA-256 over the canonical map of data-file SHA-256s, and
``packageDigest`` = SHA-256 over the canonical manifest without its ``producer`` block. A
download location (a folder or an https base, a rolling release URL included) and an
archive's own bytes are never an identity: the archive's SHA-256 checks the transfer, and the
package digests decide what was installed.

The store lives under the data root, one folder per package digest
(``<data root>/evidence/<packageId>/<package digest 12>/``), so an installed manifest is exactly
the one a project names. A package is ready only after every file's size and SHA-256 match its
manifest; an archive is extracted into a staging folder with path checks (no absolute paths, no
``..``, no links, data file types only) and moved into place only once verified. A folder keeps
``.verified.json``: the size and time of every file when it was last hashed in full. Listing the
store trusts that record only while every file keeps them (and hashes the folder again when one
does not); anything that reads the data (a refit, the viewer, an export) hashes every file first
(:func:`ready`). Downloads resume from ``.part`` files with an HTTP Range request, and a
verified package is reused offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from .desktop_env import data_root

SCHEMA = "staf-evidence-package"
SCHEMA_VERSION = 1
MANIFEST = "evidence.json"
STAMP = ".verified.json"
INDEX = "index.json"
#: The file types a package may carry (data, never code).
DATA_SUFFIXES = (".parquet", ".csv", ".json", ".txt", ".md", ".tsv", ".geojson")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
#: a package id is a folder name in the store: lower-case letters, digits, dot, dash, underscore
PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: a version label rides in archive names
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
#: names Windows reserves for devices, as a folder name or before its first dot
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_CHUNK = 1 << 20

ProgressFn = Callable[[int, int], None]


class EvidenceError(RuntimeError):
    pass


class EvidenceCancelled(EvidenceError):
    pass


class EvidenceMissing(EvidenceError):
    """The host does not hold the archive asked for."""


class EvidenceMismatch(EvidenceError):
    """The host's bytes under that name are not the archive the record names."""


def store_root() -> Path:
    return data_root() / "evidence"


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def data_digest(files: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical({k: v["sha256"] for k, v in sorted(files.items())})).hexdigest()


def package_digest(doc: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical({k: v for k, v in doc.items() if k != "producer"})).hexdigest()


def _safe_rel(name: str) -> str:
    """A package-relative path that stays inside the package and names a data file."""
    if not name or "\\" in name or name.startswith("/") or ":" in name:
        raise EvidenceError(f"unsafe path in package: {name!r}")
    parts = PurePosixPath(name).parts
    if any(p in ("", ".", "..") for p in parts):
        raise EvidenceError(f"unsafe path in package: {name!r}")
    if name != MANIFEST:
        if parts[0] != "data" or not name.lower().endswith(DATA_SUFFIXES):
            raise EvidenceError(f"a package carries data files only under data/: {name!r}")
    return name


def _no_constant(name: str):
    raise EvidenceError(f"evidence.json holds {name}, which is not a finite number")


def _finite(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        _no_constant(text)
    return value


def loads(text: str) -> dict:
    """A manifest's JSON, refusing NaN and Infinity, spelled out or overflowing (``1e999``): a
    canonical digest cannot hold them."""
    return json.loads(text, parse_constant=_no_constant, parse_float=_finite)


def usable_package_id(pid) -> bool:
    """A plain folder name: lower-case letters, digits, dot, dash, underscore; not a name
    Windows reserves; no trailing dot."""
    if not isinstance(pid, str) or not PACKAGE_ID_RE.fullmatch(pid) or pid.endswith("."):
        return False
    return pid.split(".", 1)[0] not in _RESERVED


def check_manifest(doc: dict) -> dict:
    """The manifest's shape: schema, id, version, digest and a file table of safe paths."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise EvidenceError("not an evidence package")
    schema_version = doc.get("schemaVersion", 0)
    if schema_version is None:
        schema_version = 0
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise EvidenceError("evidence.json has an unusable schemaVersion")
    if schema_version > SCHEMA_VERSION:
        raise EvidenceError("this evidence package is newer than this app reads; update the app")
    for key in ("packageId", "version", "dataDigest", "files"):
        if not doc.get(key):
            raise EvidenceError(f"evidence.json has no {key}")
    if not usable_package_id(doc["packageId"]):
        raise EvidenceError(f"evidence.json has an unusable package id: {doc['packageId']!r}")
    if not isinstance(doc["dataDigest"], str) or not doc["dataDigest"].startswith("sha256:"):
        raise EvidenceError("evidence.json has an unusable data digest")
    if not VERSION_RE.fullmatch(str(doc["version"])):
        raise EvidenceError(f"evidence.json has an unusable version: {doc['version']!r}")
    files = doc["files"]
    if not isinstance(files, dict) or not files:
        raise EvidenceError("evidence.json lists no files")
    for rel, rec in files.items():
        _safe_rel(rel)
        sha = rec.get("sha256") if isinstance(rec, dict) else None
        if not isinstance(rec, dict) or not isinstance(rec.get("bytes"), int) or isinstance(rec["bytes"], bool) \
                or rec["bytes"] < 0 or not isinstance(sha, str) or len(sha) != 64 \
                or any(ch not in "0123456789abcdef" for ch in sha):
            raise EvidenceError(f"evidence.json has a bad record for {rel}")
    if data_digest(files) != doc["dataDigest"]:
        raise EvidenceError("evidence.json's data digest does not match its file table")
    return doc


def read_manifest(folder: Path) -> dict:
    p = Path(folder) / MANIFEST
    if not p.is_file():
        raise EvidenceError(f"no {MANIFEST} in {folder}")
    if p.stat().st_size > MAX_MANIFEST_BYTES:
        raise EvidenceError("evidence.json is too large")
    try:
        doc = loads(p.read_text(encoding="utf-8"))
    except EvidenceError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvidenceError(f"evidence.json cannot be read ({exc})") from exc
    return check_manifest(doc)


def _data_files(folder: Path) -> list[str]:
    base = folder / "data"
    if not base.is_dir():
        return []
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in base.rglob("*") if p.is_file())


def verify_folder(folder: Path) -> dict:
    """Every listed file present with its size and SHA-256; nothing unlisted under data/."""
    folder = Path(folder)
    doc = read_manifest(folder)
    damaged = []
    for rel, rec in doc["files"].items():
        p = folder / rel
        if not p.is_file() or p.stat().st_size != rec["bytes"] or sha_file(p) != rec["sha256"]:
            damaged.append(rel)
    listed = set(doc["files"])
    extra = [rel for rel in _data_files(folder) if rel not in listed]
    return {"packageId": doc["packageId"], "version": doc["version"], "dataDigest": doc["dataDigest"],
            "packageDigest": package_digest(doc), "damaged": damaged, "unlisted": extra,
            "ok": not damaged and not extra, "manifest": doc}


# --------------------------------------------------------------------------- #
# the record of the last full check
# --------------------------------------------------------------------------- #
def _stats(folder: Path, doc: dict) -> dict:
    out = {}
    for rel in list(doc["files"]) + [MANIFEST]:
        st = (folder / rel).stat()
        out[rel] = [st.st_size, st.st_mtime_ns]
    return out


def _stamp(folder: Path, doc: dict) -> None:
    try:
        (folder / STAMP).write_text(json.dumps({"packageDigest": package_digest(doc),
                                                "stats": _stats(folder, doc)}), encoding="utf-8")
    except OSError:
        pass


def _unstamp(folder: Path) -> None:
    try:
        (folder / STAMP).unlink()
    except OSError:
        pass


def _stamp_ok(folder: Path, doc: dict) -> bool:
    try:
        rec = json.loads((folder / STAMP).read_text(encoding="utf-8"))
        return (rec.get("packageDigest") == package_digest(doc) and rec.get("stats") == _stats(folder, doc)
                and set(_data_files(folder)) <= set(doc["files"]))
    except (OSError, ValueError, TypeError):
        return False


def check(folder: Path, *, full: bool = False) -> dict:
    """:func:`verify_folder`, or (``full`` False) the record of the last full check while every
    file keeps the size and time it had then. ``checked`` says which: ``full`` or ``stamp``."""
    folder = Path(folder)
    doc = read_manifest(folder)
    if _renamed(folder, doc):
        # an installed folder is named by the digest it was installed with: a manifest that
        # hashes to another one was changed after the install
        _unstamp(folder)
        return {"packageId": doc["packageId"], "version": doc["version"], "dataDigest": doc["dataDigest"],
                "packageDigest": package_digest(doc), "damaged": [MANIFEST], "unlisted": [], "ok": False,
                "manifest": doc, "checked": "full"}
    if not full and _stamp_ok(folder, doc):
        return {"packageId": doc["packageId"], "version": doc["version"], "dataDigest": doc["dataDigest"],
                "packageDigest": package_digest(doc), "damaged": [], "unlisted": [], "ok": True,
                "manifest": doc, "checked": "stamp"}
    got = verify_folder(folder)
    if got["ok"]:
        _stamp(folder, doc)
    else:
        _unstamp(folder)
    return {**got, "checked": "full"}


def _renamed(folder: Path, doc: dict) -> bool:
    name = folder.name
    if len(name) != 12 or any(ch not in "0123456789abcdef" for ch in name):
        return False                      # not a store folder (a source being imported)
    return name not in (package_digest(doc).split(":", 1)[-1][:12],
                        doc["dataDigest"].split(":", 1)[-1][:12])


# --------------------------------------------------------------------------- #
# installing
# --------------------------------------------------------------------------- #
def _inside(root: Path, target: Path) -> Path:
    r, t = root.resolve(), target.resolve()
    if t == r or r not in t.parents:
        raise EvidenceError("the package would install outside the evidence store")
    return target


def _target(root: Path, doc: dict) -> Path:
    return _inside(root, root / doc["packageId"] / package_digest(doc).split(":", 1)[-1][:12])


def _reusable(target: Path, doc: dict) -> bool:
    """True when ``target`` already holds this package, every file hashed now; anything else
    there (damaged, edited, unreadable) is removed so the install replaces it."""
    if not target.is_dir():
        return False
    try:
        got = check(target, full=True)
    except Exception:  # noqa: BLE001 - an unreadable folder is replaced, never trusted
        got = {"ok": False}
    if got["ok"] and got.get("packageDigest") == package_digest(doc):
        return True
    shutil.rmtree(target, ignore_errors=True)
    return False


def install_zip(zip_path: Path, *, root: Optional[Path] = None) -> Path:
    """Install a package archive (or reuse the verified copy already installed). A damaged
    archive is refused with a plain message; nothing partial is ever left in the store."""
    root = Path(root or store_root())
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise EvidenceError(f"The archive is damaged or not a package ({exc}). Download or import "
                            "it again.") from exc
    try:
        with z:
            return _install_from(z, root)
    except EvidenceError:
        raise
    except (zipfile.BadZipFile, zlib.error, EOFError, UnicodeDecodeError, ValueError) as exc:
        raise EvidenceError(f"The archive is damaged ({exc}). Download or import it again.") from exc


def _install_from(z: zipfile.ZipFile, root: Path) -> Path:
    infos = [i for i in z.infolist() if not i.is_dir()]
    names = [i.filename for i in infos]
    if MANIFEST not in names:
        raise EvidenceError("the archive has no evidence.json")
    for info in infos:
        _safe_rel(info.filename)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise EvidenceError(f"links are not allowed in a package: {info.filename!r}")
    if z.getinfo(MANIFEST).file_size > MAX_MANIFEST_BYTES:
        raise EvidenceError("evidence.json is too large")
    doc = check_manifest(loads(z.read(MANIFEST).decode("utf-8")))
    listed = set(doc["files"]) | {MANIFEST}
    unlisted = [n for n in names if n not in listed]
    if unlisted:
        raise EvidenceError(f"the archive holds files its manifest does not list: {unlisted[:3]}")
    target = _target(root, doc)
    if _reusable(target, doc):
        return target
    staging = root / f".staging-{uuid.uuid4().hex[:10]}"
    staging.mkdir(parents=True)
    try:
        for info in infos:
            dest = staging / info.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            rec = doc["files"].get(info.filename)
            if rec is not None and info.file_size != rec["bytes"]:
                raise EvidenceError(f"{info.filename} is not the size its manifest records")
            h = hashlib.sha256()
            with z.open(info) as src, dest.open("wb") as out:
                for block in iter(lambda: src.read(_CHUNK), b""):
                    h.update(block)
                    out.write(block)
            if rec is not None and h.hexdigest() != rec["sha256"]:
                raise EvidenceError(f"{info.filename} does not match its manifest")
        missing = [rel for rel in doc["files"] if not (staging / rel).is_file()]
        if missing:
            raise EvidenceError(f"the archive lacks files its manifest lists: {missing[:3]}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _stamp(target, doc)
    return target


def install_folder(folder: Path, *, root: Optional[Path] = None) -> Path:
    """Install an unpacked package folder: verified first, then only ``evidence.json`` and the
    files it lists are copied (the source is never changed)."""
    folder = Path(folder)
    got = verify_folder(folder)
    if not got["ok"]:
        raise EvidenceError(f"the package is damaged: {(got['damaged'] + got['unlisted'])[:3]}")
    root = Path(root or store_root())
    doc = got["manifest"]
    target = _target(root, doc)
    if _reusable(target, doc):
        return target
    staging = root / f".staging-{uuid.uuid4().hex[:10]}"
    try:
        for rel in [MANIFEST] + list(doc["files"]):
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / rel, dest)
        if not verify_folder(staging)["ok"]:
            raise EvidenceError("the copied package did not verify")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _stamp(target, doc)
    return target


def installed(root: Optional[Path] = None) -> list[dict]:
    """Every package in the store, each ``verified`` or not (``damaged`` lists the files that
    failed). A folder is hashed again only when a file's size or time changed since its last
    full check."""
    root = Path(root or store_root())
    out = []
    if not root.is_dir():
        return out
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for ver in sorted(p for p in pkg.iterdir() if p.is_dir()):
            try:
                got = check(ver)
            except Exception as exc:  # noqa: BLE001 - one bad folder never hides the others
                # a manifest that cannot be read: listed as a damaged copy of the package its
                # folder is filed under, so the page offers to fetch it again
                out.append({"packageId": pkg.name, "version": None, "dataDigest": None,
                            "packageDigest": None, "installedAs": ver.name, "path": str(ver),
                            "title": pkg.name, "roles": [], "reproducibility": None, "bytes": 0,
                            "verified": False, "damaged": [MANIFEST], "manifest": None,
                            "problem": str(exc)[:200]})
                continue
            doc = got["manifest"]
            out.append({"packageId": doc["packageId"], "version": doc["version"],
                        "dataDigest": doc["dataDigest"], "packageDigest": got["packageDigest"],
                        "installedAs": ver.name, "path": str(ver),
                        "title": doc.get("title") or doc["packageId"],
                        "roles": doc.get("roles") or [], "reproducibility": doc.get("reproducibility"),
                        "bytes": sum(r["bytes"] for r in doc["files"].values()),
                        "verified": got["ok"], "damaged": got["damaged"] + got["unlisted"],
                        "manifest": doc})
    return out


def _short(digest) -> str:
    return str(digest or "").split(":", 1)[-1][:12]


def matches(rec: dict, ref: dict) -> bool:
    """True when an installed package (or a check result) is the one ``ref`` names: its package
    digest when the reference records one, else its data digest. A store folder installed under
    the reference's package digest is that package even when its manifest was changed since (it
    is then listed as damaged, never as another version)."""
    if rec.get("packageId") != ref.get("packageId"):
        return False
    if ref.get("packageDigest"):
        return (rec.get("packageDigest") == ref["packageDigest"]
                or (bool(rec.get("installedAs")) and rec.get("installedAs") == _short(ref["packageDigest"])))
    return rec.get("dataDigest") == ref.get("dataDigest")


def find(package_id: str, data_digest_: Optional[str] = None, *, package_digest_: Optional[str] = None,
         root: Optional[Path] = None) -> Optional[Path]:
    """The verified installed folder of a package (a specific data or package digest when
    given), or None."""
    for rec in installed(root):
        if rec["packageId"] != package_id or not rec["verified"]:
            continue
        if data_digest_ is not None and rec["dataDigest"] != data_digest_:
            continue
        if package_digest_ is not None and rec["packageDigest"] != package_digest_:
            continue
        return Path(rec["path"])
    return None


def ready(ref: dict, *, root: Optional[Path] = None) -> Path:
    """The installed folder of the package ``ref`` names, every file hashed now. Raises
    :class:`EvidenceError` saying what to do when it is missing or damaged."""
    root = Path(root or store_root())
    title = ref.get("title") or ref.get("packageId")
    mine = [r for r in installed(root) if matches(r, ref)]
    damaged: list = []
    for rec in mine:
        try:
            got = check(Path(rec["path"]), full=True)
        except Exception:  # noqa: BLE001 - an unreadable manifest is damage
            got = {"ok": False, "damaged": [MANIFEST], "unlisted": []}
        if got["ok"] and (not ref.get("packageDigest") or got.get("packageDigest") == ref["packageDigest"]):
            return Path(rec["path"])
        damaged += got["damaged"] + got["unlisted"]
    if mine:
        raise EvidenceError(f"{title} is damaged on this computer ({', '.join(damaged[:3])} failed its "
                            "check). Import or download it again.")
    raise EvidenceError(f"{title} is not on this computer. Import or download it first.")


def pick(folder: Path) -> Path:
    """A package folder (``<folder>/evidence.json``), or the copy to use when ``folder`` is a
    store's package folder holding one or more installed versions: the verified one, the most
    recently installed when several verify. Raises :class:`EvidenceError` when none verifies."""
    folder = Path(folder)
    if (folder / MANIFEST).is_file():
        return folder
    copies = sorted(p for p in folder.glob("*") if (p / MANIFEST).is_file())
    good = []
    for c in copies:
        try:
            if check(c)["ok"]:
                good.append(c)
        except Exception:  # noqa: BLE001 - an unreadable copy is not a candidate
            continue
    if not good:
        raise EvidenceError(f"no verified package under {folder} ({len(copies)} copies found)")
    return max(good, key=lambda c: (c / MANIFEST).stat().st_mtime)


# --------------------------------------------------------------------------- #
# the NRSA archive StreamCurves ships (DEEP's development data), as a package record
# --------------------------------------------------------------------------- #
def nrsa_archive_record(folder: Optional[Path] = None) -> Optional[dict]:
    """The in-app NRSA archive described the way a package is: its version, what it covers,
    and every file checked against its manifest (``None`` when the archive is not built)."""
    from .paths import ROOT
    folder = Path(folder or ROOT / "data" / "nrsa")
    mpath = folder / "manifest.json"
    if not mpath.is_file():
        return None
    doc = json.loads(mpath.read_text(encoding="utf-8"))
    files = doc.get("files") or {}
    damaged = []
    for name, rec in files.items():
        p = folder / name
        want = str(rec.get("sha256") or "").split(":", 1)[-1]
        if not p.is_file() or p.stat().st_size != rec.get("bytes") or sha_file(p) != want:
            damaged.append(name)
    return {"packageId": "nrsa-archive", "version": doc.get("datasetId"),
            "title": "NRSA multi-cycle archive", "roles": ["development"],
            "reproducibility": "regenerable",
            "bytes": int(doc.get("totalBytes") or sum(int(r.get("bytes") or 0) for r in files.values())),
            "coverage": {"cycles": [c.get("label") or c.get("id") if isinstance(c, dict) else c
                                    for c in doc.get("cycles") or []],
                         "files": len(files), "sourceFiles": doc.get("sourceFileCount")},
            "manifestSha256": sha_file(mpath), "verified": not damaged, "damaged": damaged}


# --------------------------------------------------------------------------- #
# downloading
# --------------------------------------------------------------------------- #
def _default_http_get(url: str, *, headers: Optional[dict] = None, stream: bool = False,
                      timeout: float = 30.0):
    import requests
    return requests.get(url, headers=headers or {}, stream=stream, timeout=timeout)


#: Replaced in tests: (url, headers=, stream=, timeout=) -> .status_code, .iter_content, .close
http_get = _default_http_get


def _is_http(base: str) -> bool:
    return base.lower().startswith(("http://", "https://"))


def _stream_error(exc: Exception) -> EvidenceError:
    """A failure while bytes arrive: a dropped connection reads as one, a disk error as one."""
    mod = type(exc).__module__ or ""
    if mod.startswith(("requests", "urllib3", "http", "socket", "ssl")) or isinstance(exc, (ConnectionError, TimeoutError)):
        return EvidenceError("The download stopped before it finished. Try again: it continues "
                             "where it stopped.")
    return EvidenceError(f"The download could not be saved ({exc}).")


def download(base: str, name: str, *, sha256: str, size: int, root: Optional[Path] = None,
             progress: Optional[ProgressFn] = None,
             cancel: Optional[threading.Event] = None) -> Path:
    """Fetch ``name`` from ``base`` (a folder or an http(s) base) into the store's download
    folder, resuming a ``.part``, and return it once its size and SHA-256 match. Cancel and a
    dropped connection keep the ``.part`` for a later resume; bytes that are not the archive
    asked for are deleted (:class:`EvidenceMismatch`); a name the host does not hold raises
    :class:`EvidenceMissing`."""
    root = Path(root or store_root())
    downloads = root / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    if "/" in name or "\\" in name or not name.endswith(".evidence.zip"):
        raise EvidenceError(f"not a package archive name: {name!r}")
    dest = downloads / name
    if dest.is_file() and dest.stat().st_size == size and sha_file(dest) == sha256:
        return dest
    part = downloads / (name + ".part")
    for attempt in (0, 1):
        have = part.stat().st_size if part.is_file() else 0
        if have >= size:
            # a finished transfer that was never renamed, or bytes past the end: decide now
            if have == size and sha_file(part) == sha256:
                os.replace(part, dest)
                return dest
            part.unlink(missing_ok=True)
            have = 0
        restart = _fetch_into(base, name, part, have, size, progress=progress, cancel=cancel)
        if restart and attempt == 0:
            part.unlink(missing_ok=True)
            continue
        break
    if not part.is_file() or part.stat().st_size != size or sha_file(part) != sha256:
        part.unlink(missing_ok=True)
        raise EvidenceMismatch(f"The host's {name} is not the archive this record names.")
    os.replace(part, dest)
    return dest


def _fetch_into(base: str, name: str, part: Path, have: int, size: int, *, progress, cancel) -> bool:
    """Append the bytes after ``have`` to ``part``. True when the host cannot continue from
    there (HTTP 416) and the transfer must start over."""
    if not _is_http(base):
        src = Path(base) / name
        if not src.is_file():
            raise EvidenceMissing(f"{name} is not at {base}")
        try:
            with src.open("rb") as fh, part.open("ab" if have else "wb") as out:
                fh.seek(have)
                got = have
                for block in iter(lambda: fh.read(_CHUNK), b""):
                    if cancel is not None and cancel.is_set():
                        raise EvidenceCancelled("Download cancelled.")
                    out.write(block)
                    got += len(block)
                    if progress:
                        progress(got, size)
        except EvidenceError:
            raise
        except OSError as exc:
            raise _stream_error(exc) from exc
        return False
    headers = {"Range": f"bytes={have}-"} if have else {}
    url = base + ("" if base.endswith("/") else "/") + name
    try:
        r = http_get(url, headers=headers, stream=True, timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        raise EvidenceError("The download could not start. Check the connection and try "
                            "again.") from exc
    try:
        if r.status_code == 404:
            raise EvidenceMissing(f"{name} is not at {base}")
        if r.status_code == 416:
            return True
        if r.status_code == 200 and have:
            have = 0                      # the server ignored the range: start over
        elif r.status_code not in (200, 206):
            raise EvidenceError(f"The download failed (HTTP {r.status_code}).")
        try:
            with part.open("ab" if have else "wb") as out:
                got = have
                for chunk in r.iter_content(_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise EvidenceCancelled("Download cancelled.")
                    if not chunk:
                        continue
                    out.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, size)
        except EvidenceError:
            raise
        except Exception as exc:  # noqa: BLE001 - said plainly; the .part stays for a resume
            raise _stream_error(exc) from exc
    finally:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass
    return False


def read_index(base: str) -> Optional[dict]:
    """The host's ``index.json`` (``{packageId: {zip, zipSha256, zipBytes, packageDigest,
    dataDigest}}``); {} when the host holds none or an unusable one; None when the host
    cannot be reached."""
    if _is_http(base):
        try:
            r = http_get(base + ("" if base.endswith("/") else "/") + INDEX, timeout=30.0)
        except Exception:  # noqa: BLE001 - the host is not there
            return None
        try:
            if r.status_code != 200:
                return {}
            text = r.content.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
    else:
        folder = Path(base)
        if not folder.is_dir():
            return None
        p = folder / INDEX
        if not p.is_file():
            return {}
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return None
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _archive_is(z: Path, ref: dict) -> bool:
    """True when the archive at ``z`` holds the package ``ref`` names (its manifest only is read)."""
    try:
        with zipfile.ZipFile(z) as zf:
            doc = check_manifest(loads(zf.read(MANIFEST).decode("utf-8")))
    except Exception:  # noqa: BLE001 - install_zip says what is wrong with it
        return True
    return matches({"packageId": doc["packageId"], "packageDigest": package_digest(doc),
                    "dataDigest": doc["dataDigest"]}, ref)


def fetch_reference(base: str, ref: dict, *, root: Optional[Path] = None,
                    progress: Optional[ProgressFn] = None,
                    cancel: Optional[threading.Event] = None) -> Path:
    """Install the package ``ref`` names from ``base`` and return its folder: the archive the
    reference pins, or, when the host holds this package under another archive (a re-export,
    other bytes), the one its ``index.json`` lists for this package. Either is accepted only
    when the installed package is the one named (:func:`matches`). The downloaded archive is
    removed once installed."""
    root = Path(root or store_root())
    title = ref.get("title") or ref.get("packageId")
    arch = ref.get("archive") or {}
    tried = []
    if arch.get("name") and arch.get("sha256") and arch.get("bytes") is not None:
        try:
            z = download(base, arch["name"], sha256=arch["sha256"], size=int(arch["bytes"]), root=root,
                         progress=progress, cancel=cancel)
            if _archive_is(z, ref):
                folder = install_zip(z, root=root)
                if matches(check(folder), ref):
                    z.unlink(missing_ok=True)
                    return folder
            z.unlink(missing_ok=True)       # another version under the pinned name: not installed
            tried.append((arch["name"], arch["sha256"]))
        except (EvidenceMissing, EvidenceMismatch):
            tried.append((arch["name"], arch["sha256"]))
    index = read_index(base)
    if index is None:
        raise EvidenceError(f"The host of {title} could not be reached. Check the connection and try "
                            "again, or import the package from a file.")
    entry = index.get(str(ref.get("packageId")))
    if not isinstance(entry, dict) or not matches({"packageId": ref.get("packageId"), **entry}, ref) \
            or not entry.get("zip") or (entry.get("zip"), entry.get("zipSha256")) in tried:
        raise EvidenceError(f"The host does not hold this version of {title}. Import it from a file "
                            "or ask for the package.")
    z = download(base, entry["zip"], sha256=entry["zipSha256"], size=int(entry["zipBytes"]), root=root,
                 progress=progress, cancel=cancel)
    if not _archive_is(z, ref):
        z.unlink(missing_ok=True)
        raise EvidenceError(f"The host's copy of {title} is another version of the package.")
    folder = install_zip(z, root=root)
    z.unlink(missing_ok=True)
    if not matches(check(folder), ref):
        raise EvidenceError(f"The host's copy of {title} is another version of the package.")
    return folder


__all__ = ["SCHEMA", "SCHEMA_VERSION", "MANIFEST", "STAMP", "INDEX", "DATA_SUFFIXES", "EvidenceError",
           "EvidenceCancelled", "EvidenceMissing", "EvidenceMismatch", "store_root",
           "check_manifest", "read_manifest", "verify_folder", "check", "install_zip",
           "install_folder", "installed", "matches", "find", "ready", "download", "read_index",
           "fetch_reference", "data_digest", "package_digest", "http_get", "nrsa_archive_record",
           "sha_file", "loads", "usable_package_id", "pick"]
