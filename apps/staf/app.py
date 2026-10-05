"""STAF: EASI, SFARI and DEEP in one app.

Each tool stays its own app under apps/<tool> and also runs here as a Shiny module, chosen with the
switch in the shared header (``?tool=easi|sfari|deep`` bookmarks a tool). The shell lives in
``staf_shell``; see README.md.

    cd apps/staf && shiny run app.py --port 8040
"""
from __future__ import annotations

from pathlib import Path

from shiny import App

from staf_shell import loader, server, shell_ui

HERE = Path(__file__).resolve().parent
TOOLS = loader.load_all()

app = App(shell_ui.make_ui(TOOLS), server.make_server(TOOLS),
          static_assets=shell_ui.static_assets(TOOLS, HERE / "www"))
