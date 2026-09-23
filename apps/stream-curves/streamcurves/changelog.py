"""CHANGELOG.md as data (ported from HYPE's hype_app/changelog.py).

apps/stream-curves/CHANGELOG.md is the single source of release notes: the What's new dialog
renders it, the start page's What's new column lists its releases, and the desktop release body
mirrors the matching section. This module is the one parser of its format:

    ## vX.Y.Z (YYYY-MM-DD)
    - one bullet per line (continuation lines are indented)

Best-effort and import-cheap: a missing or malformed file yields an empty list, never an
exception, because the start page must open no matter what.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import ROOT

#: One release section header, the format the tests pin.
SECTION_RE = re.compile(r"^## v(\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2})\)$", re.MULTILINE)
_BULLET_RE = re.compile(r"^- (.*)$")
_CONT_RE = re.compile(r"^\s{2,}(\S.*)$")
_INLINE_MD_RE = re.compile(r"\*\*|`")


@dataclass(frozen=True)
class Release:
    version: str            # "1.0.0"
    date: str               # ISO YYYY-MM-DD as written in the file
    bullets: tuple[str, ...]

    @property
    def label(self) -> str:
        """The user-visible spelling, matching version.APP_VERSION_LABEL."""
        return f"v{self.version}"

    @property
    def date_display(self) -> str:
        """`Sep 23, 2026`; the raw string when the date does not parse."""
        try:
            return datetime.strptime(self.date, "%Y-%m-%d").strftime("%b %d, %Y").replace(" 0", " ")
        except ValueError:
            return self.date

    @property
    def version_tuple(self) -> tuple[int, ...]:
        return tuple(int(p) for p in self.version.split("."))


def plain(text: str) -> str:
    """Strip the inline markdown the dialog renders (bold, code) for plain-text surfaces."""
    return _INLINE_MD_RE.sub("", text).strip()


def parse(md: str) -> list[Release]:
    """Every `## vX.Y.Z (date)` section in file order (newest first by convention)."""
    out: list[Release] = []
    headers = list(SECTION_RE.finditer(md))
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        bullets: list[str] = []
        for line in md[m.end():end].splitlines():
            b = _BULLET_RE.match(line)
            if b:
                bullets.append(b.group(1).strip())
                continue
            c = _CONT_RE.match(line)
            if c and bullets:
                bullets[-1] = f"{bullets[-1]} {c.group(1).strip()}"
        out.append(Release(version=m.group(1), date=m.group(2), bullets=tuple(bullets)))
    return out


def changelog_path() -> Path:
    """CHANGELOG.md beside app.py, in a checkout and in an installed payload alike."""
    return ROOT / "CHANGELOG.md"


def load(path: Path | None = None) -> list[Release]:
    """Parsed releases, or [] when the file is missing or unreadable."""
    try:
        text = (path or changelog_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    return parse(text)


def markdown(path: Path | None = None) -> str:
    """The file's body for the What's new dialog, without its top-level `#` title."""
    try:
        text = (path or changelog_path()).read_text(encoding="utf-8")
    except OSError:
        return ""
    return re.sub(r"\A#[^\n]*\n+", "", text.lstrip("﻿"))


__all__ = ["SECTION_RE", "Release", "parse", "plain", "changelog_path", "load", "markdown"]
