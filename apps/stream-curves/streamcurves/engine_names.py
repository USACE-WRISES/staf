"""The two watershed engines' display names, from the vendored engine's vocabulary.

Every user-visible string that names an engine reads these, so the names
appear exactly once in the program. Tokens (``streamcat`` / ``site-engine`` /
``site_engine``) are provenance and never change; only display text does.
"""
from __future__ import annotations

from typing import Optional

try:
    from ._vendor.site_engine import naming as _naming
except Exception:  # noqa: BLE001 - the vendored copy is absent only in stripped builds
    _naming = None

SITE_ENGINE = _naming.DISPLAY_NAMES[_naming.SITE_ENGINE_TOKEN] if _naming else "STAF site engine"
STREAMCAT = _naming.DISPLAY_NAMES[_naming.STREAMCAT_TOKEN] if _naming else "StreamCat lookup engine"
SITE_ENGINE_DETAIL = "exact watershed"
# What one uncached training site costs with the 0.2.1 node walk.
SITE_ENGINE_COST = "usually under a minute per uncached site, up to about five on a large basin"


def site_engine_label(version: Optional[str] = None) -> str:
    """``STAF site engine v0.2.0``."""
    if _naming is not None:
        return _naming.engine_label(version)
    return f"{SITE_ENGINE} v{version or 'unknown'}"


def predictor_source_display(token: Optional[str]) -> str:
    """Display text for a manifest or bundle ``predictorSource`` value."""
    t = str(token or "streamcat")
    if t == "streamcat":
        return STREAMCAT
    return f"{SITE_ENGINE} ({t}), exact-watershed values"
