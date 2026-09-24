"""Per-user preferences that are not project state (ported from HYPE's hype_app/prefs.py).

One JSON object at <data root>/prefs.json. Keys used today:

* `last_projects_dir`: the folder the last new project went into, so the next New project
  dialog starts there;
* `gallery_targets`: {"<assessment id>@<version>": main .streamcurves path}, where each
  downloaded assessment's working copy lives, so opening it again opens that copy;
* `prepared_by`: the initials last typed into "Prepared by (initials)", offered for the next
  new project only (a project keeps its own Prepared by, which :func:`recorded_by` reads);
* `mmw_api_key`: an optional Model My Watershed key, exported to the environment at startup.

Everything here is non-fatal: a read-only or broken data root never blocks creating a project.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from .desktop_env import data_root

_FILE = "prefs.json"
LAST_PROJECTS_DIR = "last_projects_dir"
GALLERY_TARGETS = "gallery_targets"
PREPARED_BY = "prepared_by"
MMW_API_KEY = "mmw_api_key"


def _path() -> Path:
    return data_root() / _FILE


def load() -> dict:
    """The whole preference object ({} when absent or unreadable)."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> None:  # noqa: A001 - the natural verb for a preference
    """Write one key (atomic replace); silently a no-op when the root cannot be written."""
    data = load()
    data[key] = value
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".prefs-", suffix=".json", dir=str(p.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, p)
    except OSError:
        pass


#: What StreamCurves records when nobody is named (the owner's rule of 2026-09-23).
NOT_GIVEN = "n/a"


def recorded_by(project_meta: Optional[Mapping] = None) -> str:
    """Who StreamCurves records as making a decision, an edit or a publish: initials, never a
    login. ``STAF_LIBRARY_MAINTAINER`` when the process sets it (a maintainer checkout, a
    rehearsal), else the open project's Prepared by (``project_meta``, the field its Project
    panel edits), else ``n/a``. The preference of the same name only offers initials to the next
    new project and is never recorded by itself. Nothing is refused for a missing name, and the
    Windows login is never read."""
    return (os.environ.get("STAF_LIBRARY_MAINTAINER", "").strip()
            or str((project_meta or {}).get("prepared_by") or "").strip() or NOT_GIVEN)


def given_or_na(value) -> str:
    """What was typed into an initials field, or ``n/a`` when it was left empty."""
    return str(value or "").strip() or NOT_GIVEN


def apply_environment() -> None:
    """Export the preferences that configure libraries through the environment.

    Today that is the Model My Watershed key (streamcurves/mmw.py reads MMW_API_KEY). An
    explicit environment value always wins over the stored one.
    """
    key = str(get(MMW_API_KEY) or "").strip()
    if key and not os.environ.get("MMW_API_KEY"):
        os.environ["MMW_API_KEY"] = key


__all__ = ["LAST_PROJECTS_DIR", "GALLERY_TARGETS", "PREPARED_BY", "MMW_API_KEY", "NOT_GIVEN",
           "load", "get", "set", "apply_environment", "recorded_by", "given_or_na"]
