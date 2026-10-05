"""The STAF app's server: it starts each tool the first time the tool is shown.

A tool's server is its own Shiny module (its ids under its key). It starts once per session, the
first time the switch shows the tool, and then keeps running while other tools are shown, so a
visitor who only uses SFARI never pays for EASI or DEEP, and switching back finds the work as left.
"""
from __future__ import annotations

import traceback

from shiny import reactive, ui


def make_server(tools):
    def server(input, output, session):
        started = set()

        @reactive.effect
        @reactive.event(input.staf_tool)
        def _start_tool():
            tool = tools.get(input.staf_tool())
            if tool is None or not tool.ok or tool.key in started:
                return
            started.add(tool.key)
            try:
                with reactive.isolate():
                    tool.module.tool_server(tool.key)
            except Exception:  # noqa: BLE001 - the other tools keep working
                traceback.print_exc()
                ui.notification_show(f"{tool.name} could not start. Reload the page to try again.",
                                     type="error", duration=None)

    return server
