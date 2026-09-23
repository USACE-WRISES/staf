"""App theme, icons, STAF cross-app URLs, and www asset versioning.

Ports ``app/helpers/theme.R`` (bslib flatly theme) and the shell helpers from
``app/app.R`` (``versioned_www_asset``). ``STAF_LINKS`` is a small per-app copy
by convention — same as EASI/SFARI/DEEP.
"""

from __future__ import annotations

import json
from functools import lru_cache

from shiny import ui

from streamcurves.paths import WWW_DIR

# --------------------------------------------------------------------------- #
# Theme: HYPE Desktop's look (the StreamCurves Desktop redesign, 2026-09-23).
# Plain Bootstrap with the shared navy accent and HYPE's status families
# (www/shell.css holds the same values as design tokens); the R app's flatly
# theme is retired. ui.Theme compiles Sass at first launch (cached); needs
# shiny[theme].
# --------------------------------------------------------------------------- #


def build_app_theme():
    try:
        return (
            ui.Theme(preset="shiny")
            .add_defaults(
                primary="#2f4b7c",
                success="#1a7f37",
                warning="#9a6700",
                danger="#b42318",
                info="#2f4b7c",
            )
            .add_defaults(**{"font-size-base": "0.875rem"})
        )
    except Exception:
        # No Sass compiler: Bootstrap's defaults, with shell.css carrying the look.
        return None


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
# Cross-app URLs: the in-app half of the URL mirror (docs/_data/apps.yml is the
# other; see README). About links "home" and "deep", and the Assessment library
# opens preliminary and final versions in DEEP from "deep". StreamCurves itself
# ships as StreamCurves Desktop, so "curves" is its latest release page.
# --------------------------------------------------------------------------- #

STAF_LINKS = {
    "home": "https://usace-wrises.github.io/staf/",
    "easi": "https://gtmenichino-easi.share.connect.posit.cloud/",
    "sfari": "https://gtmenichino-sfari.share.connect.posit.cloud/",
    "curves": "https://github.com/USACE-WRISES/staf/releases/latest",
    "deep": "https://gtmenichino-deep.share.connect.posit.cloud/",
}
