"""The StreamCurves project file (`<Name>.streamcurves`) and the gallery pack.

A project is a folder: the main file plus `exports/` (where the app's downloads start). The main
file is a zip:

    project.json                 identity and origin (project_meta keys), format, app version
    session.streamcurves.json    the session envelope, exactly what session_io writes and what
                                 the library stores as a version's session.streamcurves.json
    origin/...                   immutable files of the library version the project started
                                 from (meta.json, provenance.json, assessment.deep.json), so a
                                 copy of a version newer than the app's library snapshot keeps
                                 its annotations and record

A gallery PACK is the same zip with `desktop_project: false` (a standalone copy); opening one
imports it into a project folder, which stamps a new project id and `desktop_project: true`.
Packs are byte-deterministic (fixed timestamps, sorted names, fixed compression), so the release
builder can upload only names it has never published.

Everything here is pure (no Shiny). Writes are atomic: a temp file in the same folder, then
os.replace, retried briefly on the sharing violations OneDrive and antivirus scanners cause.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import project_meta, session_io as sio
from .version import APP_VERSION

FORMAT = "streamcurves-project"
#: The format a DEEP project is written in: StreamCurves 1.0.0 reads it, so every DEEP project
#: and pack stays format 1 unless it carries content an older app would drop.
FORMAT_VERSION = 1
#: The newest format this app reads. Format 2 adds `assessment_type` and typed parts (an EASI
#: project's `easi/...`); StreamCurves 1.0.0 refuses it with its "update the app" message
#: instead of opening it and dropping what it cannot read.
FORMAT_VERSION_MAX = 2
#: The pack schema; it is part of every pack's asset name (`<id>-v<N>-p1.streamcurves`), so a
#: change to what a pack holds reaches clients under a new name instead of never.
PACK_SCHEMA = 1
#: The pack schema of a format-2 project (an EASI method version's pack is `-p2-`).
PACK_SCHEMA_TYPED = 2
#: Zip folders of typed parts a project may carry beside its session (format 2).
PART_PREFIXES = ("easi/",)

PROJECT_JSON = "project.json"
SESSION_NAME = "session.streamcurves.json"
ORIGIN_PREFIX = "origin/"
#: The origin files a pack carries: immutable once a version is published. Status and
#: validation change after publishing, so they come from the catalog instead.
ORIGIN_FILES = ("meta.json", "provenance.json", "assessment.deep.json")

_FIXED_DATE = (1980, 1, 1, 0, 0, 0)


class ProjectFileError(ValueError):
    """A file that is not a readable StreamCurves project (the message is user-facing)."""


@dataclass
class ProjectFile:
    path: Path | None
    meta: dict                      # project_meta.clean_meta keys
    desktop_project: bool
    format_version: int
    app_version: str | None
    session_text: str
    origin: dict[str, bytes] = field(default_factory=dict)
    #: typed parts (format 2): zip name -> bytes, e.g. an EASI project's `easi/...`
    parts: dict[str, bytes] = field(default_factory=dict)
    #: "deep" (absent: every format-1 project) or "easi"
    assessment_type: str = "deep"

    def session_payload(self) -> dict:
        """The migrated, validated session payload (session_io.load_session_payload)."""
        return sio.load_session_payload(self.session_text)

    def origin_json(self, name: str) -> dict | None:
        raw = self.origin.get(name)
        if raw is None:
            return None
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None


def project_json(meta: dict, *, desktop_project: bool, format_version: int = FORMAT_VERSION,
                 assessment_type: str | None = None) -> dict:
    """The project.json document for `meta` (project_meta keys)."""
    m = project_meta.clean_meta(meta)
    doc = {
        "format": FORMAT,
        "format_version": int(format_version),
        "app_version": APP_VERSION,
        "desktop_project": bool(desktop_project),
        "saved_at": project_meta.now_iso(),
        **m,
    }
    if assessment_type and assessment_type != "deep":
        doc["assessment_type"] = assessment_type
    return doc


def _zip_bytes(entries: list[tuple[str, bytes]], *, deterministic: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in (sorted(entries) if deterministic else entries):
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE if deterministic
                                   else time.localtime(time.time())[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data, compresslevel=6)
    return buf.getvalue()


def _format_for(assessment_type: str | None, parts: dict | None) -> int:
    """The lowest format a project needs: 2 for a typed project or typed parts, else 1."""
    return 2 if (parts or (assessment_type and assessment_type != "deep")) else FORMAT_VERSION


def required_format(fields, *, pack: bool = False) -> int:
    """The lowest format a DEEP session's fields need. 2 when StreamCurves 1.0.0 would
    misread them (an owner decision only the REF-15 extension applies: 1.0.0 would still
    score the curves it replaces) or, in a project, drop part of them on a re-save (curves
    added for comparison or reasons recorded in the candidate register); else 1. A pack
    keeps format 1 for the register alone, as the version's provenance holds that record."""
    from . import owner_curves
    fields = fields if isinstance(fields, dict) else {}
    decisions = fields.get("owner_curve_decisions")
    if isinstance(decisions, list) and any(isinstance(d, dict) and owner_curves.needs_extension(d)
                                           for d in decisions):
        return 2
    reg = fields.get("candidate_register")
    if not pack and isinstance(reg, dict) and (reg.get("considered") or reg.get("decisions")):
        return 2
    return FORMAT_VERSION


def session_text_format(session_text: str, *, pack: bool = False) -> int:
    """:func:`required_format` of a session envelope's text."""
    try:
        doc = json.loads(session_text or "{}")
    except ValueError:
        return FORMAT_VERSION
    return required_format((doc or {}).get("fields") if isinstance(doc, dict) else None, pack=pack)


def build_bytes(*, meta: dict, session_text: str, origin: dict[str, bytes] | None = None,
                desktop_project: bool = True, deterministic: bool = False,
                saved_at: str | None = None, parts: dict[str, bytes] | None = None,
                assessment_type: str | None = None, min_format: int = FORMAT_VERSION) -> bytes:
    """The zip bytes of a project (or, with desktop_project=False, a pack). Typed parts
    (format 2) ride byte for byte under their own folder names. ``min_format``: the format
    the session needs (:func:`required_format`)."""
    for name in parts or {}:
        if not name.startswith(PART_PREFIXES) or ".." in name.split("/"):
            raise ValueError(f"not a project part name: {name}")
    doc = project_json(meta, desktop_project=desktop_project,
                       format_version=max(_format_for(assessment_type, parts), int(min_format)),
                       assessment_type=assessment_type)
    if deterministic:
        # a pack's bytes must not change with the clock
        doc["saved_at"] = saved_at or ""
    elif saved_at:
        doc["saved_at"] = saved_at
    entries = [(PROJECT_JSON, json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")),
               (SESSION_NAME, session_text.encode("utf-8"))]
    for name, data in sorted((origin or {}).items()):
        entries.append((ORIGIN_PREFIX + name, data))
    for name, data in sorted((parts or {}).items()):
        entries.append((name, data))
    return _zip_bytes(entries, deterministic=deterministic)


def atomic_write(path: Path, data: bytes, *, attempts: int = 6) -> Path:
    """Write `data` to `path` via a same-folder temp file and os.replace, retrying the replace
    on PermissionError (a sync client or scanner holding the old file for a moment)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".~" + path.name + "-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        delay = .15
        for i in range(attempts):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_project(path: str | os.PathLike[str], *, meta: dict, session_text: str,
                  origin: dict[str, bytes] | None = None, desktop_project: bool = True,
                  parts: dict[str, bytes] | None = None,
                  assessment_type: str | None = None, min_format: int | None = None) -> Path:
    """Write a project main file atomically, creating its folder and `exports/`. Without
    ``min_format`` the session text says what format it needs."""
    path = Path(path)
    if min_format is None:
        min_format = session_text_format(session_text) if (assessment_type or "deep") == "deep" \
            else FORMAT_VERSION
    data = build_bytes(meta=meta, session_text=session_text, origin=origin,
                       desktop_project=desktop_project, parts=parts,
                       assessment_type=assessment_type, min_format=min_format)
    atomic_write(path, data)
    if desktop_project:
        try:
            (path.parent / "exports").mkdir(exist_ok=True)
        except OSError:
            pass
    return path


def session_text_from_fields(fields: dict, *, session_name: str | None) -> str:
    """Encode state fields into the session envelope text (the slow part: run it off the
    event loop)."""
    payload = sio.dump_session_fields(fields, session_name=session_name, app_version=APP_VERSION)
    return sio.dumps_session(payload)


def read_project(path: str | os.PathLike[str] | bytes) -> ProjectFile:
    """Read a project or a pack (from a path or from bytes)."""
    src = io.BytesIO(path) if isinstance(path, (bytes, bytearray)) else Path(path)
    try:
        with zipfile.ZipFile(src) as zf:
            names = set(zf.namelist())
            if PROJECT_JSON not in names or SESSION_NAME not in names:
                raise ProjectFileError("This is not a StreamCurves project file.")
            doc = json.loads(zf.read(PROJECT_JSON).decode("utf-8"))
            session_text = zf.read(SESSION_NAME).decode("utf-8")
            origin = {n[len(ORIGIN_PREFIX):]: zf.read(n) for n in sorted(names)
                      if n.startswith(ORIGIN_PREFIX) and not n.endswith("/")}
            parts = {n: zf.read(n) for n in sorted(names)
                     if n.startswith(PART_PREFIXES) and not n.endswith("/")
                     and ".." not in n.split("/")}
    except zipfile.BadZipFile as e:
        raise ProjectFileError("This is not a StreamCurves project file.") from e
    except (UnicodeDecodeError, ValueError) as e:
        if isinstance(e, ProjectFileError):
            raise
        raise ProjectFileError(f"This project file is damaged ({e}).") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ProjectFileError("This is not a StreamCurves project file.")
    ver = doc.get("format_version")
    if not isinstance(ver, int) or ver < 1:
        raise ProjectFileError("This project file has no valid format version.")
    if ver > FORMAT_VERSION_MAX:
        raise ProjectFileError(
            f"This project was saved by a newer StreamCurves (project format {ver}; this app "
            f"reads up to {FORMAT_VERSION_MAX}). Update the app to open it.")
    atype = str(doc.get("assessment_type") or "deep")
    if atype not in ("deep", "easi"):
        raise ProjectFileError(f"This project holds a kind of assessment this app does not know "
                               f"({atype}). Update the app to open it.")
    fallback = None if isinstance(path, (bytes, bytearray)) else Path(path).stem
    return ProjectFile(
        path=None if isinstance(path, (bytes, bytearray)) else Path(path),
        meta=project_meta.clean_meta(doc, fallback_name=fallback),
        desktop_project=bool(doc.get("desktop_project")),
        format_version=ver,
        app_version=str(doc.get("app_version") or "") or None,
        session_text=session_text,
        origin=origin,
        parts=parts,
        assessment_type=atype,
    )


def import_as_project(src: ProjectFile, target: str | os.PathLike[str], *,
                      name: str | None = None, prepared_by: str | None = None,
                      description: str | None = None) -> Path:
    """Copy a pack (or another project) into a new project at `target`: a new project id and
    creation stamp, `desktop_project: true`, the session and origin files unchanged."""
    meta = dict(src.meta)
    meta["project_id"] = project_meta.new_identity()
    meta["project_created"] = project_meta.now_iso()
    if name:
        meta["project_name"] = name
    if prepared_by is not None:
        meta["prepared_by"] = prepared_by or None
    if description is not None:
        meta["project_description"] = description or None
    return write_project(target, meta=meta, session_text=src.session_text, origin=src.origin,
                         desktop_project=True, parts=src.parts,
                         assessment_type=src.assessment_type)


def pack_asset_name(assessment_id: str, version: int, sha256: str | None = None, *,
                    schema: int = PACK_SCHEMA) -> str:
    """`<id>-v<N>-p<schema>[-<sha8>].streamcurves`: the pack schema and a content hash in
    the name, so a changed pack is a new asset, never a silent overwrite."""
    tail = f"-{sha256[:8]}" if sha256 else ""
    return f"{assessment_id}-v{int(version)}-p{int(schema)}{tail}.streamcurves"


__all__ = ["FORMAT", "FORMAT_VERSION", "PACK_SCHEMA", "PROJECT_JSON", "SESSION_NAME",
           "ORIGIN_PREFIX", "ORIGIN_FILES", "ProjectFileError", "ProjectFile", "project_json",
           "build_bytes", "atomic_write", "write_project", "session_text_from_fields",
           "required_format", "session_text_format",
           "read_project", "import_as_project", "pack_asset_name"]
