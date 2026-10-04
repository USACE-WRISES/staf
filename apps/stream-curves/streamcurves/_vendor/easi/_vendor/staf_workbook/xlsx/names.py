"""Excel's rules for sheet names: the scenario name is the tab name, so the page checks it with the
same function the workbook does."""
from __future__ import annotations

from typing import Optional

MAX_LEN = 31
FORBIDDEN = set('[]:*?/\\')
RESERVED = ("history",)


def sheet_name_problem(name: str, taken=()) -> Optional[str]:
    """Why ``name`` cannot be a sheet name (short text for the page), or None when it can."""
    if not name or not name.strip():
        return "Enter a name."
    if len(name) > MAX_LEN:
        return f"Use {MAX_LEN} characters or fewer."
    bad = sorted(set(name) & FORBIDDEN)
    if bad:
        return "These characters are not allowed: " + " ".join(bad)
    if name.startswith("'") or name.endswith("'"):
        return "A name cannot start or end with an apostrophe."
    if name.strip().lower() in RESERVED:
        return "That name is reserved by Excel."
    if name.strip().lower() in set(t.lower() for t in taken):
        return "That name is already used."
    return None


def unique_sheet_name(base: str, taken) -> str:
    """``base`` made valid and unique (helper sheets only; scenario names are checked on the page)."""
    clean = "".join(ch for ch in base if ch not in FORBIDDEN).strip("'").strip() or "Sheet"
    clean = clean[:MAX_LEN]
    low = set(t.lower() for t in taken)
    if clean.lower() not in low:
        return clean
    n = 2
    while True:
        tail = f" ({n})"
        cand = clean[:MAX_LEN - len(tail)] + tail
        if cand.lower() not in low:
            return cand
        n += 1
