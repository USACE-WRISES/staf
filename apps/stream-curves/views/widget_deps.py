"""Static front-end dependencies for ipywidgets/ipyleaflet.

shinywidgets injects a widget library's JS only when a widget of that library
renders. Every ipyleaflet widget in this app lives in dynamically rendered UI
(the import wizard's region/sites maps), so the first render used to race its
own module load — the page-load "Could not instantiate widget" / "Could not
create a model" retry noise — unless a hidden warm-up widget primed the
library at page load (the old ``_sw_warmup``).

This module replaces the warm-up: it mirrors the HTMLDependency that
``shinywidgets._dependencies.require_dependency`` builds for jupyter-leaflet
and attaches it statically, plus an eager ``require()`` so the bundle is
FETCHED at page load. Later model creation awaits the same in-flight module,
so no widget ever races the load and nothing renders off-screen.
"""

from __future__ import annotations

import json
import os

from htmltools import HTMLDependency, TagList, tags
from htmltools._core import (
    HTMLDependencySource,  # pyright: ignore[reportPrivateImportUsage]
)
from ipyleaflet import Map
from shinywidgets._dependencies import (
    jupyter_extension_path,
    output_binding_dependency,
    parse_version_safely,
)


def _trait_default(cls, name: str) -> str:
    return str(cls.class_traits()[name].default_value)


def _leaflet_dependency() -> HTMLDependency | None:
    """The jupyter-leaflet extension bundle, built exactly the way
    shinywidgets' require_dependency() builds it at widget-render time (same
    name/version so the session-level dependency dedupe treats them as one)."""
    module_name = _trait_default(Map, "_view_module")  # "jupyter-leaflet"
    module_dir = jupyter_extension_path(module_name)
    if module_dir is None:
        return None
    version = parse_version_safely(_trait_default(Map, "_model_module_version"))
    source = HTMLDependencySource(subdir=module_dir)
    # "lib" is shiny's default lib_prefix; require_dependency reads it off the
    # session, but there is no session at page-build time.
    href = HTMLDependency(module_name, version, source=source).source_path_map(
        lib_prefix="lib"
    )["href"]
    config = {"paths": {module_name: os.path.join(href, "index")}}
    return HTMLDependency(
        module_name,
        version,
        source=source,
        all_files=True,
        head=TagList(
            # window.require comes from libembed-amd.js (output binding dep,
            # included first) — head scripts execute in document order.
            tags.script(f"window.require.config({json.dumps(config)})"),
            # Kick off the module fetch now; model creation later awaits the
            # same in-flight load instead of erroring and retrying.
            tags.script(
                f'window.require([{json.dumps(module_name)}], '
                "function() {}, function() {});"
            ),
        ),
    )


def static_ipywidget_dependencies() -> TagList:
    """Everything the page needs so dynamically rendered ipyleaflet widgets
    just work: the ipywidget output binding (require + HTMLManager + CSS) and
    the eagerly-loaded jupyter-leaflet bundle."""
    deps = TagList(output_binding_dependency())
    leaflet = _leaflet_dependency()
    if leaflet is not None:
        deps.append(leaflet)
    return deps
