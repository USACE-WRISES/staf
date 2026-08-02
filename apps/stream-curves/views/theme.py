"""App theme, icons, STAF cross-app URLs, and www asset versioning.

Ports ``app/helpers/theme.R`` (bslib flatly theme) and the shell helpers from
``app/app.R`` (``versioned_www_asset``). ``STAF_LINKS`` is a small per-app copy
by convention — same as EASI/SFARI/DEEP.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from shiny import ui

from streamcurves.paths import WWW_DIR

# --------------------------------------------------------------------------- #
# Theme — port of app/helpers/theme.R:
#   bs_theme(version=5, bootswatch="flatly", primary="#2c3e50",
#            success="#27ae60", warning="#f39c12", danger="#e74c3c",
#            info="#3498db", "font-size-base"="0.9rem")
# ui.Theme compiles Sass at first launch (cached); needs shiny[theme].
# --------------------------------------------------------------------------- #


def build_app_theme():
    try:
        return (
            ui.Theme(preset="flatly")
            .add_defaults(
                primary="#2c3e50",
                success="#27ae60",
                warning="#f39c12",
                danger="#e74c3c",
                info="#3498db",
            )
            .add_defaults(**{"font-size-base": "0.9rem"})
        )
    except Exception:
        # Fallback: shinyswatch preset; color/font overrides live in curves.css.
        import shinyswatch

        return shinyswatch.theme.flatly


app_theme = build_app_theme()


# --------------------------------------------------------------------------- #
# Icons.
# bi(): exact-parity port of bsicons::bs_icon() — the SVG markup was dumped
# from the R package into www/vendor/bs-icons.json (see scripts/convert_data.py).
# fa(): thin wrapper over faicons for the R shiny::icon() (Font Awesome) sites.
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _bs_icon_svgs() -> dict[str, str]:
    path = WWW_DIR / "vendor" / "bs-icons.json"
    return json.loads(path.read_text(encoding="utf-8"))


def bi(name: str, **kwargs) -> ui.HTML:
    svg = _bs_icon_svgs().get(name)
    if svg is None:
        raise KeyError(
            f"bootstrap icon {name!r} not in www/vendor/bs-icons.json — "
            "re-dump it from the R bsicons package"
        )
    return ui.HTML(svg)


def fa(name: str, **kwargs):
    import faicons

    return faicons.icon_svg(name, **kwargs)


# --------------------------------------------------------------------------- #
# www asset cache-busting — port of versioned_www_asset() (app/app.R:7-23).
# --------------------------------------------------------------------------- #


def versioned_www_asset(asset_name: str) -> str:
    path = WWW_DIR / asset_name
    try:
        stat = path.stat()
    except OSError:
        return asset_name
    from datetime import datetime

    version = f"{datetime.fromtimestamp(stat.st_mtime):%Y%m%d%H%M%S}-{stat.st_size}"
    return f"{asset_name}?v={version}"


# --------------------------------------------------------------------------- #
# Cross-app URLs. The "home" entry is the STAF link in the navbar (app.py); it
# used to have a dedicated banner strip below the navbar, which cost ~28px of
# height for one link -- EASI/SFARI/DEEP have always carried the same link
# inline in their header, so this now matches them.
#
# The rest of the dict is not the link's: the library view deep-links into DEEP
# from it, it is the in-app half of the URL mirror (see README), and the desktop
# shell rewrites all entries via STAF_LINKS_OVERRIDES -- which views/publish.py
# also reads as its "am I running on the desktop" flag.
# --------------------------------------------------------------------------- #

STAF_LINKS = {
    "home": "https://usace-wrises.github.io/staf/",
    "easi": "https://gtmenichino-easi.share.connect.posit.cloud/",
    "sfari": "https://gtmenichino-sfari.share.connect.posit.cloud/",
    "curves": "https://gtmenichino-stream-curves.share.connect.posit.cloud/",
    "deep": "https://gtmenichino-deep.share.connect.posit.cloud/",
}
_staf_links_overrides = os.environ.get("STAF_LINKS_OVERRIDES")
if _staf_links_overrides:  # desktop shell rewrites cross-app links; absent on web deploys
    STAF_LINKS.update(json.loads(_staf_links_overrides))
