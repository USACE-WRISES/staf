"""Where the app is running: the per-user data root and whether this is an installed copy.

The data root mirrors the desktop shell's own resolution (ShellConfig.cs): STREAMCURVES_DATA_ROOT
when set (the shell exports it to the app process, so the override propagates), else
%LOCALAPPDATA%\\StreamCurves, else ~/.streamcurves for non-Windows dev shells. Recents, prefs,
the gallery's cache and downloads all live under it.

An INSTALLED copy is recognized by the file the payload build writes beside the app tree:
`<apps root>/desktop-manifest.json` (desktop/scripts/gen_desktop_manifest.py). A checkout never
has it, which is what keeps an installed copy read-only against the library no matter what
environment it inherits (streamcurves.library.writable).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import ROOT

#: apps/stream-curves -> apps (the payload's root in an installed copy)
APPS_ROOT = ROOT.parent
DESKTOP_MANIFEST = "desktop-manifest.json"


def data_root() -> Path:
    """The per-user data root (same resolution as the shell's ShellConfig)."""
    override = os.environ.get("STREAMCURVES_DATA_ROOT")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "StreamCurves"
    return Path.home() / ".streamcurves"


def in_desktop_shell() -> bool:
    """True when the desktop shell launched this process (it sets STREAMCURVES_DESKTOP=1)."""
    return os.environ.get("STREAMCURVES_DESKTOP", "").strip() == "1"


def desktop_manifest_path() -> Path:
    return APPS_ROOT / DESKTOP_MANIFEST


def is_installed_copy() -> bool:
    """True inside an installed apps payload (never in a checkout or a worktree)."""
    try:
        return desktop_manifest_path().is_file()
    except OSError:
        return False


def desktop_build_line() -> str | None:
    """The installed payload's identity for About ("apps 2026.09.23-abc1234"), or None."""
    try:
        doc = json.loads(desktop_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ver = str(doc.get("appsVersion") or doc.get("version") or "").strip()
    built = str(doc.get("builtFromCommit") or "").strip()[:7]
    if not ver and not built:
        return None
    return " ".join(p for p in (f"apps {ver}" if ver else "", f"({built})" if built else "") if p)


__all__ = ["APPS_ROOT", "DESKTOP_MANIFEST", "data_root", "in_desktop_shell",
           "desktop_manifest_path", "is_installed_copy", "desktop_build_line"]
