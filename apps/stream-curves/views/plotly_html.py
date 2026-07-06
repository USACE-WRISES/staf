"""Interactive plotly figures delivered the way R's htmlwidgets delivers them:
a ``@render.ui`` fragment carrying the figure JSON plus a ``Plotly.newPlot``
call, with plotly.min.js served from the installed plotly package as an
HTMLDependency.

This sidesteps the ipywidgets comm/model machinery entirely — unlike a
shinywidgets ``render_widget``, a plain ui fragment renders no matter when or
where its output is registered (modals, per-panel dynamic outputs), which is
exactly what the phase-1 scatter comparison panels need (see views/phase1.py).
"""

from __future__ import annotations

import json
import uuid

import plotly
import plotly.io as pio
from htmltools import HTMLDependency
from shiny import ui

# R's plotly htmlwidget ships these config defaults with every widget; the
# modebar itself stays on its plotly.js default (visible on hover), as in R.
_R_PLOTLY_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["sendDataToCloud"],
    # htmlwidgets re-sizes plots on window resize; plotly.js's own responsive
    # flag is the equivalent here.
    "responsive": True,
}


def plotly_js_dependency() -> HTMLDependency:
    """plotly.min.js from the installed plotly package (no CDN, works offline).
    htmltools/shiny dedupe this per session, so every fragment can carry it."""
    return HTMLDependency(
        name="plotly-js",
        version=plotly.__version__,
        source={"package": "plotly", "subdir": "package_data"},
        script={"src": "plotly.min.js"},
    )


def plotly_html_fragment(fig, height_px: int, config: dict | None = None):
    """Render a plotly Figure as a self-drawing HTML fragment.

    The draw script waits for plotly.js to load (the dependency arrives with
    the same render message) and, when the container is hidden (inactive tab,
    animating modal), defers via ResizeObserver until it has real width — the
    situations htmlwidgets handles with its shown/resize hooks in R.
    """
    spec = json.loads(pio.to_json(fig))
    payload = json.dumps(
        {
            "data": spec.get("data") or [],
            "layout": spec.get("layout") or {},
            "config": {**_R_PLOTLY_CONFIG, **(config or {})},
        },
        separators=(",", ":"),
    ).replace("<", "\\u003c")  # user text can't close the script tag

    el_id = f"plotly-html-{uuid.uuid4().hex}"
    script = (
        "(function(){"
        f'var el=document.getElementById("{el_id}");'
        "if(!el)return;"
        f"var p={payload},n=0;"
        "function draw(){window.Plotly.newPlot(el,p.data,p.layout,p.config);}"
        "function start(){"
        "if(!el.isConnected)return;"
        "if(!(window.Plotly&&window.Plotly.newPlot)){if(n++<200)setTimeout(start,50);return;}"
        "if(el.offsetWidth>0){draw();return;}"
        "var ro=new ResizeObserver(function(){"
        "if(!el.isConnected){ro.disconnect();return;}"
        "if(el.offsetWidth>0){ro.disconnect();draw();}"
        "});"
        "ro.observe(el);"
        "}"
        "start();"
        "})();"
    )
    return ui.TagList(
        plotly_js_dependency(),
        ui.div(id=el_id, style=f"width:100%; height:{height_px}px;"),
        ui.tags.script(ui.HTML(script)),
    )
