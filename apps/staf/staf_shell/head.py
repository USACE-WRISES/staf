"""Every tool's head assets on one page.

Each tool's ``app.py`` lists its assets in ``HEAD``, in its own load order, as URLs relative to
its ``www/``. The STAF app serves each tool's ``www/`` under the tool's key (``sfari/styles.css``)
and the shared STAF assets once under ``staf/`` (the same files in every tool, vendored from
libs/staf_workbook). So on the STAF page:

* the shared files load once, ``staf/staf-ns.js`` before every other script;
* each tool's stylesheets load unchanged but apply only while that tool is shown: the shell flips
  their ``media`` between "all" and "not all", so the shown tool gets exactly its own cascade, and
  each keeps its place before or after the shared stylesheets;
* each tool's own scripts load under its prefix.
"""
from __future__ import annotations

from shiny import ui

SHARED = "staf/"
FIRST_SCRIPT = "staf/staf-ns.js"


def _url(tag) -> str:
    return str(tag.attrs.get("href") or tag.attrs.get("src") or "")


def _path(url: str) -> str:
    return url.split("?", 1)[0]


def head_tags(tools, active: str) -> list:
    """``tools``: the loaded tools in page order; ``active``: the key of the tool shown first."""
    before, shared_css, after, shared_js, own_js, seen = [], [], [], [], [], set()
    for tool in tools:
        after_shared = False
        for tag in tool.module.HEAD:
            url = _url(tag)
            if url.startswith(SHARED):
                after_shared = after_shared or tag.name == "link"
                if _path(url) not in seen:
                    seen.add(_path(url))
                    (shared_css if tag.name == "link" else shared_js).append(tag)
            elif tag.name == "link":
                (after if after_shared else before).append(ui.tags.link(
                    rel="stylesheet", href=f"{tool.key}/{url}", media="all" if tool.key == active else "not all",
                    **{"data-staf-css": tool.key}))
            elif tag.name == "script":
                own_js.append(ui.tags.script(src=f"{tool.key}/{url}", defer=""))
            else:
                own_js.append(tag)
    shared_js.sort(key=lambda tag: not _url(tag).startswith(FIRST_SCRIPT))
    return [*before, *shared_css, *after, *shared_js, *own_js]
