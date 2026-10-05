# STAF: EASI, SFARI and DEEP in one app (prototype)

One Shiny for Python app that hosts the three STAF tools, each as a Shiny module, under one header:
`STAF | Screening › Rapid › Detailed` on the left (the shown tool's tier lit), the
`EASI | SFARI | DEEP` switch in the middle (EASI's Nationwide screening switch beside it while EASI
is shown), and the shown tool's own actions on the right. Each tool stays its own app under
`apps/<tool>` and keeps deploying on its own; STAF is a separate deploy (`staf` on Connect Cloud).

## How it works

- **Tools are modules.** Each tool's `app.py` builds its page from pieces (`HEAD`, `_nav_actions`,
  `_header_center`, `_tool_body`, `server`) and also offers them as module pieces
  (`tool_nav_ui`, `tool_center_ui`, `tool_body_ui`, `tool_server`). On its own a tool's ids are
  plain (`map`); in STAF they carry its key (`sfari-map`).
- **The loader** (`staf_shell/loader.py`) points every tool's caches at one runtime folder, imports
  EASI first (its import checks the adopted method), gives SFARI and DEEP EASI's copy of the site
  engine (`staf_shell/engine.py`: one data bundle, one request limit, one tile pool), and loads each
  `app.py` under its own name. A tool that fails gets an "is not available here" section and the
  others carry on.
- **The page** (`staf_shell/shell_ui.py`) renders every tool's section at once; only the shown one
  is displayed (`html[data-staf-tool]`). Each tool's server starts the first time it is shown
  (`staf_shell/server.py`) and keeps running, so switching keeps each tool's work.
- **Styles:** each tool's stylesheets load unchanged, and only the shown tool's apply (their
  `media` flips), so every tool looks exactly as it does on its own (`staf_shell/head.py`).
- **Scripts:** the shared scripts (`www/staf/`, vendored from `libs/staf_workbook/assets/`) and each
  tool's own scripts find their tool through `staf-ns.js` and post to its prefixed inputs.
- **Bookmarks:** `?tool=easi|sfari|deep`; DEEP's assessment links (`?assessment=<id>@<version>`)
  open DEEP. The default is EASI.

## Run and test

```powershell
cd apps\staf
shiny run app.py --port 8040            # the repo .venv; or the "staf" preview entry
python -m pytest                        # from this folder
python scripts\measure_startup.py       # cold start and memory, STAF against each tool alone
```

Settings: `STAF_DISABLE_TOOLS=easi,deep` leaves tools out; `STAF_TOOLS_DIR` points at another copy
of the tools (the deploy uses `_tools/`); `STAF_RUNTIME_DIR` moves the shared caches (default
`<tmp>/staf`); `EASI_NATIONAL_VIEWER=1` turns on EASI's Nationwide screening as it does on its own.

## Deploy (Posit Connect Cloud, a NEW content item)

1. `python apps\staf\scripts\assemble_tools.py` copies each tool's deploy files (its own tracked
   Publisher list) into `_tools/` (gitignored), with `_tools/MANIFEST.json`.
2. Run the STAF suite: `tests/test_assembled.py` checks the copy matches the repo.
3. Open `apps\staf` as its own VS Code window, pick the `staf` Publisher configuration, check that
   `_tools/` is in the file list, and deploy. The first deploy creates the item; never commit or
   delete the deployment record Publisher writes then.
4. On Connect Cloud: size the item for three tools (8 GB / 2 CPU), copy the env vars the three
   tools' items use (not `EASI_REVIEW_ROOT`), and verify in a real browser (scripts get a 403).

The tools' own URLs, `docs/_data/apps.yml` and each `STAF_LINKS` stay as they are until a cut-over.

## Writing tool code that works in both places

- A tool's JavaScript never spells an input id: it posts to `STAFNs.id(STAFNs.tool(TOOL), name)`,
  checks `STAFNs.mine(event.target, TOOL)` in every delegated listener, and looks inside
  `STAFNs.scope(STAFNs.tool(TOOL))` for its own markers.
- Custom messages the shared scripts read (`staf-report-state`, `staf-unsaved`) carry
  `"ns": str(session.ns)`.
- `session.dynamic_route` names use underscores (a module rejects hyphens).
- Raw `id=` attributes in a tool's body must be the tool's own (`deep-cov-panel`); everything else
  is a Shiny id or a class.
- `def server` stays a plain top-level function: the tests read its source.
- Each tool's `tests/test_module_mode.py` keeps the module pieces honest, including a server start
  as a module.
