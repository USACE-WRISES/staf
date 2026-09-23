"""What this copy of StreamCurves may do: an installed copy, a checkout, or a maintainer's
checkout.

Three states, decided once from where the code runs and two environment switches:

* an INSTALLED copy (the desktop payload, recognized by desktop-manifest.json beside the app
  tree): the gallery comes from the `library` release, the library snapshot the payload
  ships is read-only, and nothing is written outside the user's own project folders. No
  publishing, no Approve or Certify, no Region builder, no region records for REF-15 choices
  (they stay in the project's session);
* a CHECKOUT (a STAF git checkout or worktree): the gallery reads the checkout's
  `apps/library` directly, and the Region builder's run folders and REF-15 region records live
  under `notes/` as they always have;
* a MAINTAINER's checkout: a checkout with STAF_LIBRARY_PUBLISH=1, where Publish, Validate's
  Approve and Certify, and recording validation write the library (the name recorded comes from
  STAF_LIBRARY_MAINTAINER).

STREAMCURVES_GALLERY_SOURCE=release makes a checkout read the gallery from the release (or from
STREAMCURVES_LIBRARY_BASE_URL), which is how the download flow is exercised in development.
"""
from __future__ import annotations

import os
from pathlib import Path

from .desktop_env import APPS_ROOT, is_installed_copy
from .paths import ROOT

_PUBLISH_ENV = "STAF_LIBRARY_PUBLISH"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def repo_root() -> Path | None:
    """The STAF checkout this app runs from, or None for an installed copy.

    A checkout is a folder holding `apps/` and a `.git` entry, which is a folder in a clone and
    a file in a worktree (both count). STAF_REPO_ROOT names one explicitly.
    """
    if is_installed_copy():
        return None
    env = os.environ.get("STAF_REPO_ROOT", "").strip()
    if env:
        p = Path(env)
        return p if (p / "apps").is_dir() else None
    for cand in (ROOT, *ROOT.parents):
        try:
            if (cand / "apps").is_dir() and (cand / ".git").exists():
                return cand
        except OSError:
            break
    # a copy of the tree without git metadata still has the checkout layout
    return APPS_ROOT.parent if (APPS_ROOT.parent / "apps").is_dir() else None


def is_checkout() -> bool:
    return repo_root() is not None


def can_publish() -> bool:
    """Maintainer mode: a checkout with the publish switch on."""
    return is_checkout() and _flag(_PUBLISH_ENV)


def gallery_source() -> str:
    """"release" (download packs) or "checkout" (read apps/library in place)."""
    forced = os.environ.get("STREAMCURVES_GALLERY_SOURCE", "").strip().lower()
    if forced in ("release", "checkout"):
        return "checkout" if forced == "checkout" and is_checkout() else "release"
    return "checkout" if is_checkout() else "release"


def mode() -> str:
    """"maintainer", "checkout" or "installed" (About and the start page say which)."""
    if can_publish():
        return "maintainer"
    return "checkout" if is_checkout() else "installed"


__all__ = ["repo_root", "is_checkout", "can_publish", "gallery_source", "mode"]
