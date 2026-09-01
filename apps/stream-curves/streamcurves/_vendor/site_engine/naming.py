"""Engine vocabulary: tokens and display names of the two STAF watershed engines.

Tokens are immutable identifiers that live in provenance records, DEEP bundles
(``predictorSource``), StreamCurves digests, CLI choices and YAML source keys.
Display names are what user-visible copy says. Every consuming app imports
this module from its vendored copy so the four apps and the docs share one
vocabulary. Pure module: importing it never loads the geo stack.
"""
from __future__ import annotations

from typing import Optional

from . import ENGINE_ID, ENGINE_VERSION

SITE_ENGINE_TOKEN = "site-engine"
STREAMCAT_TOKEN = "streamcat"
USER_OVERRIDE_TOKEN = "user-override"

assert SITE_ENGINE_TOKEN == ENGINE_ID

DISPLAY_NAMES = {
    SITE_ENGINE_TOKEN: "STAF site engine",
    STREAMCAT_TOKEN: "StreamCat lookup engine",
    USER_OVERRIDE_TOKEN: "user override",
}


def display_name(token: str) -> str:
    """Display name for a token; unknown tokens come back unchanged."""
    return DISPLAY_NAMES.get(str(token), str(token))


def engine_label(version: Optional[str] = None) -> str:
    """``STAF site engine v0.2.0`` (the version the caller vendored)."""
    return f"{DISPLAY_NAMES[SITE_ENGINE_TOKEN]} v{version or ENGINE_VERSION}"


def source_label(token: str, *, version: Optional[str] = None,
                 detail: Optional[str] = None) -> str:
    """One plain source label, e.g. ``STAF site engine v0.2.0, exact watershed``
    or ``StreamCat lookup engine, NHDPlus V2 COMID 5215053``."""
    base = engine_label(version) if token == SITE_ENGINE_TOKEN else display_name(token)
    return f"{base}, {detail}" if detail else base


def is_engine(token: Optional[str]) -> bool:
    return str(token or "").startswith(SITE_ENGINE_TOKEN)


def anchor_label(anchor: Optional[dict]) -> str:
    """The reach a COMID-keyed value describes on a routed (``hrSurrogate``)
    site, e.g. ``nearest covered reach, COMID 5214461, 1,240 ft downstream,
    DA ratio 1.8``. Empty for covered (``v2Direct``) anchors."""
    if not anchor or anchor.get("anchorKind") != "hrSurrogate":
        return ""
    routing = anchor.get("routing") or {}
    if routing.get("declined"):
        return "no covered reach within the substitution limit"
    scored = anchor.get("scoredReach") or {}
    parts = ["nearest covered reach"]
    if scored.get("comid") is not None:
        parts.append(f"COMID {scored['comid']}")
    dist = routing.get("routedDistanceFt")
    if dist is not None:
        parts.append(f"{float(dist):,.0f} ft downstream")
    ratio = routing.get("daRatio")
    if ratio is not None:
        parts.append(f"DA ratio {ratio}")
    return ", ".join(parts)
