# R → Python porting conventions

Rules for porting `D:\Code\Work\stream-curves` (R) into this repo. Every ported
module follows these so shapes line up across modules without coordination.

## General

- **Module docstring names the R source** (e.g. `"""Port of R/10_reference_curves.R."""`).
  Keep the R function names (already snake_case) and argument names verbatim
  unless they collide with Python builtins.
- `streamcurves/` is **pure**: no `shiny` imports, no reactive code, no UI.
- Port behavior 1:1 — including quirks explicitly marked as warts. If a fix is
  tempting, port faithfully and leave a `# NOTE(parity):` comment instead.
- R `stop(...)` → `raise ValueError(...)` (same message text where practical).
  R `cli::cli_alert_warning/warning()` → `logging.getLogger("streamcurves").warning(...)`,
  plus, where the R function *returns* warnings, return them as `list[str]`.
- R `%||%` → `x if x is not None else default` (helper `_or(x, default)` fine).

## Data shapes

- Config objects are **plain dicts keyed like the R named lists**:
  `metric_config[metric_key] -> dict` with the same field names/defaults the R
  code uses (`column_name`, `display_name`, `units`, `metric_family`,
  `higher_is_better`, `min_sample_size`, `allowed_stratifications`, ...).
- Tabular results are **pandas DataFrames with the R column names verbatim**,
  in the same column order. One row per R row, same sort order.
- R `NA` → `np.nan` in numeric columns, `None`/`pd.NA` in object columns.
  R `NULL` argument/return → `None`.
- R factors → `pd.Categorical` **only where level order matters** (strata);
  otherwise plain object columns. Factor level order = R default = sorted
  unique (character) unless the R code sets levels explicitly.
- R list-columns (e.g. nested `curve_points`) → object columns holding
  DataFrames/lists/None.

## Numerics (parity-critical)

- Quantiles: `np.quantile(x, q)` (default linear == R type 7). `sd` →
  `np.std(x, ddof=1)`. Round like R only where the R code rounds.
- `scipy.stats.kruskal`, `mannwhitneyu(alternative="two-sided",
  method="asymptotic", use_continuity=True)` (R `W` == scipy `U1` of the
  first sample), `multipletests(method="fdr_bh")` with NaNs masked out.
- eta²: direct sums of squares, NOT `f_oneway`.
- OLS: `statsmodels` (`sm.OLS` / `smf.ols`); treatment (drop-first) dummy
  coding with R's factor-level order.
- BIC for GLMs: `-2*llf + k*log(n)` — never statsmodels' deviance-based `.bic`.
- leaps replica: `bic = n*log(RSS/TSS) + i*log(n)`, `cp = RSS/sigma2 - (n-2i)`
  with `i` = #coefficients incl. intercept, `sigma2 = RSS_full/(n - i_max)`.
- LOESS: `skmisc.loess` (span 0.75, degree 2, gaussian) — not statsmodels.
- Never use `Date.now()`-style nondeterminism in domain code; timestamps are
  passed in by callers.

## py-shiny async architecture (learned the hard way in M2 — follow these)

- `session.send_custom_message` is **async**: always `await` it from an async
  `@reactive.effect`. A bare call silently no-ops (RuntimeWarning in logs).
- **Never call `reactive.flush()` inside a `session.on_flushed` callback** —
  it re-enters the session's flushed-callback chain and recurses to a
  RecursionError. For R's `session$onFlushed(prepare)` pattern, launch the
  work as a detached task instead: `asyncio.create_task(coro)` from the
  effect (contextvars carry the session), keep a reference so it isn't GC'd,
  and `await asyncio.sleep(0)` first so the flush that painted the UI
  transmits before heavy work starts.
- Long work blocks the event loop (unlike R, where the socket write already
  happened): progress/detail updates only paint if the worker `await`s
  between steps — `await reactive.flush(); await asyncio.sleep(0)`
  (`WorkspaceProgressNotifier.flush()`); truly CPU-bound stretches between
  awaits still block, which matches R's synchronous-session behavior.
- `ui.modal()` has no dialog-class hook; the `workspaceModalDialogClass`
  custom message in curves.js tags `.modal-dialog` after insertion (retry
  loop — the DOM lands a flush later).
- Never mutate a container held in a `reactive.Value` — copy-then-set
  (helpers in `views/state.py` do this).
- **Task-side flushes go through `st.task_flush()`, never bare
  `reactive.flush()`** (M4/M5): py-shiny's `ReactiveEnvironment.flush` has no
  re-entrancy guard, and two interleaved flushers race `_flush_sequential`'s
  `empty()`/`get()` pair. `task_flush()` serializes ours behind one
  `asyncio.Lock`. Also: yield (`await asyncio.sleep(0)`) before a task's
  first flush, and wrap every detached-task body in try/except with
  `logger.exception` — task exceptions are otherwise silent until GC.
- **Outputs rendered into the workspace modal need
  `@output(suspend_when_hidden=False)`** (M4/M5 root cause of the "all
  outputs stuck recalculating, server idle" wedge): content that binds while
  the modal is mid-fade — or that renders later from async prepare work —
  reports itself hidden (`.clientdata_output_*_hidden=true`) and the server
  suspends it forever; no resize/tab-shown event fires headless, and in real
  browsers the race can hit the modal's *default* tab (non-default tabs
  recover via `shown.bs.tab`). All such renders are `workspace_active`-
  guarded, so unsuspending costs nothing. Belt-and-braces: the
  `workspaceModalDialogClass` handler in curves.js fires staggered
  window-resize events after modal insertion so default-suspended outputs
  (plots, grids) get a visibility recheck. The same wedge hits (a) leaf
  outputs inside a *navbar tab* whose containers arrive while another tab
  is active (the cross-section chips/editor/summary), and (b)
  `@render.download` links inside a plain `ui.modal` — they bind during the
  display:none insert frame and latch disabled; wrap them with
  `@output(suspend_when_hidden=False)`, which composes with
  `@render.download`.
- **Give DataGrids an explicit `height=`** — a height-less empty grid
  measures 0x0, so shiny reports it hidden and suspends it (the curve editor
  uses `height="260px"`).
- `faicons.icon_svg` takes **current FA6 names only** — no legacy aliases
  (R's `icon("exclamation-triangle")` must become
  `fa("triangle-exclamation")`; a bad name raises `ValueError` at render
  time and kills that output).
- Don't push `update_cell_selection` (or other renderer messages) from
  detached tasks to restore DataGrid selection after a re-render; structural
  edits simply clear the selection (documented divergence from R's DT proxy).
- **A reactive effect that early-returns before reading any reactive source
  dies permanently** (M8 root cause of the phase-1 scatter panels never
  rendering): its first run takes zero dependencies, so it is never
  invalidated again. R observers usually survive this because their `req()`
  guards read `rv$...` reactively; the port's `reactive.isolate()` wrappers
  removed those deps. Rule: take every dependency (read the reactive
  sources) BEFORE any guard/early-return, exactly mirroring what the R
  observer's `req()` line touches (`_register_panel_output_renderers` in
  views/phase1.py is the reference).
- **Interactive plotly in dynamically rendered UI goes through
  `views/plotly_html.py`, not shinywidgets** — a plain `@render.ui` fragment
  carrying the figure JSON + `Plotly.newPlot` (plotly.min.js served from the
  plotly package as an HTMLDependency), which is mechanically what R's
  `renderPlotly`/htmlwidgets does. A shinywidgets `render_widget` is only
  safe for outputs registered at module-server init with static module
  structure (cross-section transects); registered late its client model
  never wires up, and pool-registering at init disrupted sibling outputs
  (17ca25b). Give scatter-style figures `template="none"` so plotly.js
  renders on its client-side defaults — the white background and d3
  colorway the R app shows.

## Files & tests

- Paths come from `streamcurves.paths` (`CONFIG_DIR`, `DATA_DIR`,
  `TEMPLATES_DIR`) — never hardcoded.
- Every module gets `tests/test_<module>.py`:
  - synthetic/hand-computed unit cases that run standalone, plus
  - golden-fixture comparisons that `pytest.skip` when
    `tests/golden/<name>` is missing (fixtures land with scripts/export_golden.R).
- HTTP clients: pure `parse_*` functions separated from fetchers; fetchers
  never raise (return None/NA-shaped results); tests use canned responses,
  live tests marked `@pytest.mark.live`.
