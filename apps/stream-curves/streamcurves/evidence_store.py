"""Evidence packages: verified before use, installed once, reused offline.

An evidence package (AUTHORING.md, "Evidence") is ``evidence.json`` plus ``data/`` files,
distributed as a zip ``<packageId>-<version>-<sha8>.evidence.zip``. Its identity is its data:
``dataDigest`` = SHA-256 over the canonical map of data-file SHA-256s; a download location
(a folder or an https base, a rolling release URL included) is never an identity.

The store lives under the data root (``<data root>/evidence/<packageId>/<data digest 12>/``).
A package is ready only after every file's size and SHA-256 match its manifest; an archive is
extracted into a staging folder with path checks (no absolute paths, no ``..``, no links, data
file types only) and moved into place only once verified. Downloads resume from ``.part``
files with an HTTP Range request, and a verified package is reused offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from .desktop_env import data_root

SCHEMA = "staf-evidence-package"
SCHEMA_VERSION = 1
MANIFEST = "evidence.json"
#: The file types a package may carry (data, never code).
DATA_SUFFIXES = (".parquet", ".csv", ".json", ".txt", ".md", ".tsv", ".geojson")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_CHUNK = 1 << 20

ProgressFn = Callable[[int, int], None]


class EvidenceError(RuntimeError):
    pass


class EvidenceCancelled(EvidenceError):
    pass


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


def check_manifest(doc: dict) -> dict:
    """The manifest's shape: schema, id, version, digest and a file table of safe paths."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise EvidenceError("not an evidence package")
    if int(doc.get("schemaVersion") or 0) > SCHEMA_VERSION:
        raise EvidenceError("this evidence package is newer than this app reads; update the app")
    for key in ("packageId", "version", "dataDigest", "files"):
        if not doc.get(key):
            raise EvidenceError(f"evidence.json has no {key}")
    files = doc["files"]
    if not isinstance(files, dict) or not files:
        raise EvidenceError("evidence.json lists no files")
    for rel, rec in files.items():
        _safe_rel(rel)
        if not isinstance(rec, dict) or not isinstance(rec.get("bytes"), int) or rec["bytes"] < 0 \
                or len(str(rec.get("sha256") or "")) != 64:
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
    return check_manifest(json.loads(p.read_text(encoding="utf-8")))


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
    extra = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in (folder / "data").rglob("*")
                   if p.is_file() and str(p.relative_to(folder)).replace("\\", "/") not in listed)
    return {"packageId": doc["packageId"], "version": doc["version"], "dataDigest": doc["dataDigest"],
            "packageDigest": package_digest(doc), "damaged": damaged, "unlisted": extra,
            "ok": not damaged and not extra, "manifest": doc}


def _target(root: Path, doc: dict) -> Path:
    return root / doc["packageId"] / doc["dataDigest"].split(":", 1)[-1][:12]


def install_zip(zip_path: Path, *, root: Optional[Path] = None) -> Path:
    """Install a package archive (or reuse the verified copy already installed)."""
    root = Path(root or store_root())
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise EvidenceError(f"not a readable package archive: {exc}") from exc
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        names = [i.filename for i in infos]
        if MANIFEST not in names:
            raise EvidenceError("the archive has no evidence.json")
        for info in infos:
            _safe_rel(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise EvidenceError(f"links are not allowed in a package: {info.filename!r}")
        manifest_info = z.getinfo(MANIFEST)
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise EvidenceError("evidence.json is too large")
        doc = check_manifest(json.loads(z.read(MANIFEST).decode("utf-8")))
        listed = set(doc["files"]) | {MANIFEST}
        unlisted = [n for n in names if n not in listed]
        if unlisted:
            raise EvidenceError(f"the archive holds files its manifest does not list: {unlisted[:3]}")
        target = _target(root, doc)
        if target.is_dir():
            got = verify_folder(target)
            if got["ok"] and got["dataDigest"] == doc["dataDigest"]:
                return target
            shutil.rmtree(target, ignore_errors=True)
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
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return target


def install_folder(folder: Path, *, root: Optional[Path] = None) -> Path:
    """Install an unpacked package folder (verified first; the source is never changed)."""
    got = verify_folder(folder)
    if not got["ok"]:
        raise EvidenceError(f"the package is damaged: {(got['damaged'] + got['unlisted'])[:3]}")
    root = Path(root or store_root())
    target = _target(root, got["manifest"])
    if target.is_dir() and verify_folder(target)["ok"]:
        return target
    staging = root / f".staging-{uuid.uuid4().hex[:10]}"
    shutil.copytree(folder, staging, ignore=shutil.ignore_patterns("*.part"))
    if not verify_folder(staging)["ok"]:
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceError("the copied package did not verify")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    return target


def installed(root: Optional[Path] = None) -> list[dict]:
    """Every verified package in the store (a damaged copy is listed as damaged)."""
    root = Path(root or store_root())
    out = []
    if not root.is_dir():
        return out
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for ver in sorted(p for p in pkg.iterdir() if p.is_dir()):
            try:
                doc = read_manifest(ver)
            except (EvidenceError, ValueError, OSError):
                continue
            out.append({"packageId": doc["packageId"], "version": doc["version"],
                        "dataDigest": doc["dataDigest"], "path": str(ver),
                        "title": doc.get("title") or doc["packageId"],
                        "roles": doc.get("roles") or [], "reproducibility": doc.get("reproducibility"),
                        "bytes": sum(r["bytes"] for r in doc["files"].values()),
                        "manifest": doc})
    return out


def find(package_id: str, data_digest_: Optional[str] = None, *,
         root: Optional[Path] = None) -> Optional[Path]:
    """The installed folder of a package (a specific data digest when given)."""
    for rec in installed(root):
        if rec["packageId"] == package_id and (data_digest_ is None or rec["dataDigest"] == data_digest_):
            return Path(rec["path"])
    return None


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


def download(base: str, name: str, *, sha256: str, size: int, root: Optional[Path] = None,
             progress: Optional[ProgressFn] = None,
             cancel: Optional[threading.Event] = None) -> Path:
    """Fetch ``name`` from ``base`` (a folder or an http(s) base) into the store's download
    folder, resuming a ``.part``, and return it once its size and SHA-256 match. Cancel keeps
    the ``.part`` for a later resume; a mismatch deletes it."""
    root = Path(root or store_root())
    downloads = root / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    if "/" in name or "\\" in name or not name.endswith(".evidence.zip"):
        raise EvidenceError(f"not a package archive name: {name!r}")
    dest = downloads / name
    if dest.is_file() and dest.stat().st_size == size and sha_file(dest) == sha256:
        return dest
    part = downloads / (name + ".part")
    if not base.lower().startswith(("http://", "https://")):
        src = Path(base) / name
        if not src.is_file():
            raise EvidenceError(f"{name} is not at {base}")
        have = part.stat().st_size if part.is_file() else 0
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
    else:
        have = part.stat().st_size if part.is_file() else 0
        if have > size:
            part.unlink(missing_ok=True)
            have = 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        url = base + ("" if base.endswith("/") else "/") + name
        try:
            r = http_get(url, headers=headers, stream=True, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            raise EvidenceError("The download could not start. Check the connection and try "
                                "again.") from exc
        try:
            if r.status_code == 404:
                raise EvidenceError(f"{name} is not at {base}")
            if r.status_code == 200 and have:
                have = 0                  # the server ignored the range: start over
            elif r.status_code not in (200, 206):
                raise EvidenceError(f"The download failed (HTTP {r.status_code}).")
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
        finally:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
    if part.stat().st_size != size or sha_file(part) != sha256:
        part.unlink(missing_ok=True)
        raise EvidenceError("The downloaded package did not match its record. Try again.")
    os.replace(part, dest)
    return dest


__all__ = ["SCHEMA", "SCHEMA_VERSION", "MANIFEST", "DATA_SUFFIXES", "EvidenceError",
           "EvidenceCancelled", "store_root", "check_manifest", "read_manifest", "verify_folder",
           "install_zip", "install_folder", "installed", "find", "download", "data_digest",
           "package_digest", "http_get", "nrsa_archive_record"]
