"""The app's identity: one version number and the names every surface shows.

`APP_VERSION` is the single source of truth for the header chip, the start page, About, the
session envelope and the project file. It moves together with the desktop shell's csproj
`<Version>` and the newest section of CHANGELOG.md (tests/test_changelog.py pins all three).
"""
from __future__ import annotations

APP_VERSION = "1.0.0"
APP_VERSION_LABEL = f"v{APP_VERSION}"

APP_NAME = "StreamCurves"
#: What the app does, on the splash, the start page and the launcher page.
APP_TAGLINE = "Reference & Regional Curve Development"
#: The header's short subtitle (the header is narrow; the tagline lives on the splash).
APP_SUBTITLE = "Reference & Regional Curves"

REPO = "USACE-WRISES/staf"
REPO_URL = f"https://github.com/{REPO}"
ISSUES_URL = f"{REPO_URL}/issues"
RELEASES_URL = f"{REPO_URL}/releases/latest"

__all__ = ["APP_VERSION", "APP_VERSION_LABEL", "APP_NAME", "APP_TAGLINE", "APP_SUBTITLE",
           "REPO", "REPO_URL", "ISSUES_URL", "RELEASES_URL"]
