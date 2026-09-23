"""The Help dialog's content (app.py re-exports it; tests/test_stagebar_nav reads it there).

Derived from the strip's own vocabulary (run_state), so adding a stage or a tool updates the
dialog for free instead of leaving it stale.
"""
from __future__ import annotations

from shiny import ui

from streamcurves import run_state as rs


def app_help_content():
    stages = ui.tags.ul(
        *[ui.tags.li(ui.tags.strong(f"{i}. {rs.STAGE_SHORT[k]}"),
                     f": {rs.STAGE_HELP[k]}")
          for i, k in enumerate(rs.STAGE_KEYS, start=1)],
        class_="mb-2",
    )
    tools = ui.tags.ul(
        *[ui.tags.li(ui.tags.strong(rs.TOOL_LABELS[k]), f": {rs.TOOL_TITLES[k]}")
          for k in rs.TOOL_KEYS],
        class_="mb-2",
    )
    return ui.TagList(
        ui.tags.p(
            "StreamCurves develops ",
            ui.tags.strong("reference and regional curves"),
            " for stream metrics from published monitoring data and your own "
            "measurements, and publishes them as detailed assessments that DEEP runs.",
        ),
        ui.tags.h6("Projects", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "A project is a folder holding one .streamcurves file (plus an exports folder for "
            "what you save from the app). ",
            ui.tags.strong("Projects"),
            " in the header opens the start page: create a project, open one, reopen a "
            "recent one, or download an assessment from the library to work on your own "
            "copy. Your work saves as you go; ",
            ui.tags.strong("Save"),
            " saves now and ",
            ui.tags.strong("Save As"),
            " keeps a copy under a new name.",
            class_="mb-1",
        ),
        ui.tags.h6("Workflow", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "The numbered strip under the header, and the Project panel at the left, are the "
            "navigation; each stage is a page. A check marks a finished stage, an amber ring "
            "one that needs a look.",
            class_="text-muted mb-1",
        ),
        stages,
        ui.tags.h6("Tools", class_="fw-bold mt-3 mb-1"),
        tools,
        ui.tags.h6("Publishing", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "Assessments are published to the STAF assessment library by its maintainer. "
            "Draft versions are for review; DEEP runs the Preliminary and Final ones. If you "
            "revised a downloaded assessment, send your .streamcurves file to the maintainer.",
            class_="mb-1",
        ),
        ui.tags.h6("Data sources", class_="fw-bold mt-3 mb-1"),
        ui.tags.p(
            "NRSA field and lab data, USGS StreamStats, Model My Watershed, "
            "USGS 3DEP/NLDI, and two watershed engines. The StreamCat lookup "
            "engine (EPA StreamCat by NHDPlus V2 reach) is the default predictor "
            "source. The STAF site engine computes HR reach watershed values at the "
            "training sites and is the one selectable alternative, chosen in the "
            "region builder. Every build records which engine it used, and the "
            "EASI screening is pinned to the StreamCat lookup engine.",
            class_="mb-0",
        ),
    )


__all__ = ["app_help_content"]
