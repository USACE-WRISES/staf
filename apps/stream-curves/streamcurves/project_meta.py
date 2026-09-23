"""Project identity metadata: pure helpers, no Shiny (after HYPE's hype_app/project_meta.py).

The metadata rides in the project file's `project.json`, beside (not inside) the session
envelope, so a session payload stays exactly what the library stores. Every reader tolerates
missing keys: an imported legacy session or a gallery pack starts with only what it carries.

Keys: project_name, project_id (uuid, new per project copy), project_created (ISO),
project_description, prepared_by, origin ({kind, assessmentId, assessmentName, version,
status, contentDigest, source}), app_version, saved_at.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

# Everything Win32 forbids in a file name, plus control characters.
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def new_identity() -> str:
    """A new immutable project identity."""
    return str(uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def filename_stem(name: str | None, fallback: str = "") -> str:
    """Win32-safe file stem from a project name: forbidden characters replaced, whitespace
    collapsed, the trailing dots and spaces Win32 drops trimmed, length clamped. An empty or
    fully forbidden name yields `fallback`."""
    s = _ILLEGAL.sub(" ", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80].rstrip(" .") or fallback


def clean_meta(raw: dict | None, *, fallback_name: str | None = None) -> dict:
    """Project metadata from a project.json, tolerant of missing or malformed keys."""
    st = raw if isinstance(raw, dict) else {}
    name = str(st.get("project_name") or "").strip() or str(fallback_name or "").strip() or None
    desc = st.get("project_description")
    desc = str(desc).strip() if isinstance(desc, str) and desc.strip() else None
    prepared = st.get("prepared_by")
    prepared = str(prepared).strip() if isinstance(prepared, str) and prepared.strip() else None
    created = st.get("project_created")
    origin = st.get("origin") if isinstance(st.get("origin"), dict) else None
    region = st.get("region") if isinstance(st.get("region"), dict) else None
    ui_loc = st.get("ui") if isinstance(st.get("ui"), dict) else None
    out = {
        "project_name": name,
        "project_id": str(st.get("project_id") or "").strip() or None,
        "project_created": str(created) if created else None,
        "project_description": desc,
        "prepared_by": prepared,
        "origin": dict(origin) if origin else None,
        # a brief of the session's region (kind, code, name), so the start page can draw
        # a project's outline without opening its session
        "region": ({k: region.get(k) for k in ("kind", "code", "name") if region.get(k)}
                   if region else None),
        # where the project was last looked at (page, wizard step, section)
        "ui": dict(ui_loc) if ui_loc else None,
    }
    return out


def created_display(iso: str | None) -> str:
    """Human date for the Project dialog: ISO stamp -> "Sep 23, 2026", else "Not recorded"."""
    if not iso:
        return "Not recorded"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%b %d, %Y").replace(" 0", " ")
    except ValueError:
        return "Not recorded"


def origin_label(origin: dict | None) -> str | None:
    """One line naming where a project started: "Eastern Corn Belt Plains v8 (Preliminary)"."""
    if not origin or not origin.get("assessmentId"):
        return None
    name = origin.get("assessmentName") or origin.get("assessmentId")
    ver = origin.get("version")
    status = origin.get("statusLabel") or ""
    out = f"{name} v{ver}" if ver else str(name)
    return f"{out} ({status})" if status else out


__all__ = ["new_identity", "now_iso", "filename_stem", "clean_meta", "created_display",
           "origin_label"]
