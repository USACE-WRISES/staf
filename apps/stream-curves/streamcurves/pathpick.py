"""Where projects go, and how a typed path is read (after HYPE's hype_app/pathpick.py).

Pure path logic (no Shiny) so every rule is unit-testable. The typed-path modal is the last
resort picker (a plain browser, or the native dialog failing to open); its free text is where
"I typed my Desktop and the app dumped the project onto it" happens, so each reading lives here
behind tests.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import project_meta

SUFFIX = ".streamcurves"
LEGACY_SESSION_SUFFIX = ".streamcurves.json"
WORKBOOK_SUFFIXES = (".xlsx",)

MSG_EMPTY = "Enter a full path, for example D:\\Projects\\SiteA\\SiteA.streamcurves."
MSG_ABS = "Enter an absolute path (e.g. D:\\Projects\\SiteA\\SiteA.streamcurves)."
MSG_OPEN_DIR = "That is a folder. Enter the path of the .streamcurves file inside it."
MSG_UNREADABLE = "That path can't be read. Check it and try again."
MSG_MISSING = "That file doesn't exist. Check the path and try again."
MSG_KIND = ("Open a StreamCurves project (.streamcurves), a saved session "
            "(.streamcurves.json) or a workbook (.xlsx).")

MSG_NEW_NAME = "Enter a name for the project."
MSG_NEW_NAME_CHARS = 'That name cannot be used for a file. Avoid \\ / : * ? " < > | .'
MSG_NEW_FOLDER = "Choose the folder the project will live in."
MSG_NEW_FOLDER_ABS = "Enter a full folder path, for example D:\\Projects."
MSG_NEW_FOLDER_FILE = "That is a file. Choose the folder to put the project in."

#: Operating-system files that do not make a folder "occupied".
OS_CRUFT = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})


def picker_mode() -> str:
    """Picker flavor without the shell bridge: "auto" spawns the tkinter child dialog;
    STREAMCURVES_PICKER=modal keeps the typed-path modal (the E2E and preview lever)."""
    env = os.environ.get("STREAMCURVES_PICKER", "auto").strip().lower()
    return "modal" if env == "modal" else "auto"


def kind_of(path: str | os.PathLike[str]) -> str | None:
    """"project" | "session" | "workbook" | None, by name (the legacy suffix first, since
    `.streamcurves.json` also ends in `.json`)."""
    name = Path(path).name.lower()
    if name.endswith(LEGACY_SESSION_SUFFIX):
        return "session"
    if name.endswith(SUFFIX):
        return "project"
    if name.endswith(WORKBOOK_SUFFIXES):
        return "workbook"
    return None


def ensure_suffix(p: Path) -> Path:
    """Append .streamcurves when missing. APPEND, never with_suffix: 'Site v1.2' must become
    'Site v1.2.streamcurves', not 'Site v1.streamcurves'."""
    return p if p.name.lower().endswith(SUFFIX) else p.with_name(p.name + SUFFIX)


def _dir_is_empty(p: Path) -> bool:
    """Empty for project purposes: nothing but OS metadata."""
    return all(e.name.lower() in OS_CRUFT for e in p.iterdir())


def _unquote(raw: str) -> str:
    """Strip symmetric quote pairs, at most twice (Explorer's "Copy as path" nests once)."""
    s = raw.strip()
    for _ in range(2):
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            s = s[1:-1].strip()
    return s


def documents_dir() -> Path:
    """The user's Documents folder: the Windows known folder (follows OneDrive redirection),
    else ~/Documents when it exists, else home. Never raises."""
    if os.name == "nt":
        try:
            import ctypes

            class _GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
                            ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

            fid = _GUID(0xFDD39AD0, 0x238F, 0x46AF,
                        (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None,
                                                          ctypes.byref(out)) == 0:
                p = Path(out.value or "")
                ctypes.windll.ole32.CoTaskMemFree(out)
                if p.is_dir():
                    return p
        except Exception:  # noqa: BLE001 - a default, never a gate
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def default_projects_dir() -> Path:
    """Where projects go when nothing better is known: Documents\\StreamCurves Projects. Never
    created here; creation happens on first use."""
    return documents_dir() / "StreamCurves Projects"


def suffixed_target(root, stem: str) -> Path:
    """The main file in the first of `<root>/<stem>`, `<root>/<stem> (2)`, ... whose folder is
    absent or empty (HYPE's example policy, from HEC-RAS 2025). Read-only probes."""
    stem = str(stem).rstrip(" .") or "Project"
    for n in range(1, 100):
        name = stem if n == 1 else f"{stem} ({n})"
        d = Path(root) / name
        try:
            if not d.is_dir() or _dir_is_empty(d):
                return d / f"{name}{SUFFIX}"
        except OSError:
            break
    return Path(root) / stem / f"{stem}{SUFFIX}"


def new_project_target(name: str, folder: str) -> tuple[Path | None, str | None]:
    """(main file, None) for the New project dialog's Name + Folder, else (None, why not).

    The project gets its own subfolder in the chosen folder, named for the project and
    suffixed " (2)", " (3)"... when that name is taken, so a project never lands loose among
    other files. The folder need not exist yet; creating it is the caller's job.
    """
    clean = " ".join(str(name or "").split())
    if not clean:
        return None, MSG_NEW_NAME
    stem = project_meta.filename_stem(clean)
    if not stem:
        return None, MSG_NEW_NAME_CHARS
    raw_dir = _unquote(str(folder or ""))
    if not raw_dir:
        return None, MSG_NEW_FOLDER
    try:
        d = Path(raw_dir)
        if not d.is_absolute():
            return None, MSG_NEW_FOLDER_ABS
        if d.is_file():
            return None, MSG_NEW_FOLDER_FILE
        return suffixed_target(d, stem), None
    except (ValueError, OSError):
        return None, MSG_UNREADABLE


def interpret_typed_open(raw: str) -> tuple[Path | None, str | None]:
    """The typed-path modal's Open: an EXISTING project, session or workbook."""
    s = _unquote(raw)
    if not s:
        return None, MSG_EMPTY
    try:
        p = Path(s)
        if not p.is_absolute():
            return None, MSG_ABS
        if p.is_dir():
            return None, MSG_OPEN_DIR
        if kind_of(p) is None:
            return None, MSG_KIND
        if not p.is_file():
            return None, MSG_MISSING
        return p, None
    except (ValueError, OSError):
        return None, MSG_UNREADABLE


def interpret_typed_save(raw: str, *, known_stem: str | None = None) -> tuple[Path | None, str | None]:
    """The typed-path modal's Save As / Save to: a main file to create.

    A bare existing folder means "put the project here": an empty folder becomes
    <dir>/<dirname>.streamcurves, an occupied one gets a subfolder named for the project.
    """
    s = _unquote(raw)
    if not s:
        return None, MSG_EMPTY
    dir_intent = s[-1] in "\\/"
    try:
        p = Path(s)
        if not p.is_absolute():
            return None, MSG_ABS
        if p.is_dir():
            if _dir_is_empty(p):
                return p / f"{p.name or 'Project'}{SUFFIX}", None
            return suffixed_target(p, known_stem or p.name or "Project"), None
        if dir_intent:
            return p / f"{p.name or 'Project'}{SUFFIX}", None
        return ensure_suffix(p), None
    except (ValueError, OSError):
        return None, MSG_UNREADABLE


def interpret_typed_folder(raw: str) -> tuple[Path | None, str | None]:
    """The typed-path modal's folder pick (New project's Browse...)."""
    s = _unquote(raw)
    if not s:
        return None, MSG_NEW_FOLDER
    try:
        p = Path(s)
        if not p.is_absolute():
            return None, MSG_NEW_FOLDER_ABS
        if p.is_file():
            return None, MSG_NEW_FOLDER_FILE
        return p, None
    except (ValueError, OSError):
        return None, MSG_UNREADABLE


__all__ = ["SUFFIX", "LEGACY_SESSION_SUFFIX", "picker_mode", "kind_of", "ensure_suffix",
           "documents_dir", "default_projects_dir", "suffixed_target", "new_project_target",
           "interpret_typed_open", "interpret_typed_save", "interpret_typed_folder"]
