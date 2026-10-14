"""The words and colors of a library version's lifecycle status, as people read them.

Stored literals (``draft``, ``preliminary``, ``certified`` ...) stay in every machine field
(status.json, catalog.json, baked bundles, session provenance); only human-facing text uses
these. The Excel calculator stamps its status with them (``deep_calculator``), and
``library`` re-exports the labels it has always offered.

Standard library only and importing nothing from ``streamcurves``: DEEP's tests load the
calculator generator by file path, outside the package, and the generator loads this file
the same way then.

DEEP holds the same literals in apps/deep/deep/session.py (no shared package; the
STAF_LINKS precedent). tests/test_status_words.py keeps the two equal.
"""
from __future__ import annotations

STATUS_LABELS = {
    "draft": "Draft",
    "preliminary": "Preliminary",
    "under_review": "Under review",
    "certified": "Final",
    "revised": "Revised",
    "retired": "Retired",
}
# What each status DEEP runs means, in one line (owner, 2026-10-14).
STATUS_NOTES = {
    "draft": "For research and regional assessment development only.",
    "preliminary": "Reviewed and approved as Preliminary. Not yet certified for use.",
    "certified": "Cleared and approved for use.",
}
# Each status's (line, fill) colors: DEEP's region palette. The line color carries the
# calculator's status banners, under white text.
STATUS_COLORS = {
    "draft": ("#475569", "#6b6459"),
    "preliminary": ("#b45309", "#d97706"),
    "certified": ("#303f9f", "#3f51b5"),
}
# The one sentence a file prints for its assessment's status.
STATUS_LINE = "Assessment status: {label}. {note}"
# A status DEEP never shows (under review, revised, retired) keeps the gray of a Draft.
OTHER_COLORS = STATUS_COLORS["draft"]


def status_key(status) -> str:
    """The stored literal, trimmed and lowercased."""
    return str(status or "").strip().lower()


def status_label(status, default: str = "preliminary") -> str:
    """Display label for a lifecycle status; unknown strings title-case rather
    than render blank."""
    s = status_key(status)
    return STATUS_LABELS.get(s) or (s.title() if s else STATUS_LABELS[default])


def status_parts(status) -> tuple:
    """``("Assessment status: Draft.", "For research ...")``: the head and the line of
    meaning, apart (a print header sets them on two lines). A status DEEP never shows has
    no line of meaning, so its second part is empty."""
    s = status_key(status)
    head = STATUS_LINE.format(label=status_label(s), note="").rstrip()
    return head, STATUS_NOTES.get(s, "")


def status_line(status) -> str:
    """``Assessment status: Draft. For research ...``, or ``Assessment status: Retired.``"""
    return " ".join(part for part in status_parts(status) if part)


def status_colors(status) -> tuple:
    """``(line, fill)`` for a status."""
    return STATUS_COLORS.get(status_key(status), OTHER_COLORS)
