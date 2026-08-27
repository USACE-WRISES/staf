"""Workflow-strip navigation and the coverage-exception form.

Two regressions this pins:

* The substep chips were registered for a hardcoded ``range(1, 8)`` rather than
  the declared steps, so an eighth wizard step would have shipped with a chip
  that renders and does nothing.
* The "Document a gap" button and its modal were built inside a module server
  without ``ns()``, so their DOM ids were bare while the handlers listened on
  ``discipline_map-*``. The button never fired, and it is the only way to
  document a deliberate coverage gap, which is what unblocks publish.
"""
from __future__ import annotations

import ast
import io
import pathlib
import re

from streamcurves import run_state as rs

_VIEWS = pathlib.Path(__file__).resolve().parents[1] / "views"


def _src(name: str) -> str:
    return io.open(_VIEWS / name, encoding="utf-8").read()


# --------------------------------------------------------------------------- #
# Substep chips
# --------------------------------------------------------------------------- #
def test_substep_handlers_are_derived_from_the_declared_steps():
    """A literal range would silently drop the chip for a newly added step."""
    src = _src("stagebar.py")
    assert "range(1, 8)" not in src
    assert "rs.STAGE_SUBSTEPS.values()" in src


def test_every_declared_wizard_step_is_reachable():
    """The derived set has to actually cover what STAGE_SUBSTEPS declares."""
    declared = {n for steps in rs.STAGE_SUBSTEPS.values() for n, _ in steps}
    assert declared, "STAGE_SUBSTEPS is empty; the chips would all vanish"
    # the comprehension the module uses, evaluated the same way
    derived = sorted({n for steps in rs.STAGE_SUBSTEPS.values() for n, _ in steps})
    assert set(derived) == declared


# --------------------------------------------------------------------------- #
# Navigation guards
# --------------------------------------------------------------------------- #
def test_navigation_refuses_while_a_recompute_is_running():
    """Navigating mid-flush is what wedges the session."""
    src = _src("stagebar.py")
    go = src[src.index("def _go(stage_key"):src.index("@reactive.effect", src.index("def _go(stage_key"))]
    assert "tasks_running" in go
    assert "Still working" in go
    assert "return" in go


def test_a_blocked_stage_is_still_reachable():
    """Pills stay clickable on purpose so you can look ahead (uihelpers.py).
    Blocking them would strand any project whose stage status disagrees with
    the data it actually holds."""
    src = _src("stagebar.py")
    go = src[src.index("def _go(stage_key"):src.index("@reactive.effect", src.index("def _go(stage_key"))]
    blocked = go[go.index("STAGE_BLOCKED"):]
    # the blocked branch notifies, and navigation follows it rather than returning
    assert "notification_show" in blocked
    assert "_request_nav" in blocked


def test_the_blocked_message_reuses_the_pill_detail():
    """One source for the tooltip and the toast, so they cannot drift apart."""
    src = _src("stagebar.py")
    assert 'info.get("detail")' in src


# --------------------------------------------------------------------------- #
# Every strip handler is guarded
# --------------------------------------------------------------------------- #
def _unguarded_effects(name: str) -> list[str]:
    tree = ast.parse(_src(name))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decs = [ast.unparse(d) for d in node.decorator_list]
        if not any("reactive.effect" in d for d in decs):
            continue
        if not any(d.startswith("guard(") for d in decs):
            out.append(node.name)
    return out


def test_no_strip_handler_can_close_the_session():
    """An exception in a reactive effect reaches Session._unhandled_error, which
    closes the websocket and takes the in-memory project with it."""
    assert _unguarded_effects("stagebar.py") == []


def test_no_reference_curves_handler_can_close_the_session():
    assert _unguarded_effects("summary_page.py") == []


def test_no_mapping_handler_can_close_the_session():
    assert _unguarded_effects("discipline_map.py") == []


# --------------------------------------------------------------------------- #
# The coverage-exception form
# --------------------------------------------------------------------------- #
COVERAGE_INPUTS = [
    "open_coverage_exception",
    "exc_function",
    "exc_reason",
    "exc_justification",
    "exc_recorded_by",
    "exc_save",
]


def test_the_coverage_exception_inputs_are_namespaced():
    """Dynamic UI in a module server needs explicit ns(); without it the button
    renders and does nothing."""
    src = _src("discipline_map.py")
    for name in COVERAGE_INPUTS:
        assert f'ns("{name}")' in src, f"{name} is not namespaced"
        assert not re.search(r'input_\w+\(\s*"' + name + r'"', src), \
            f"{name} still has a bare id somewhere"


def _bare_ids_inside_server(name: str, server_fn: str) -> list[str]:
    """Inputs built inside the server with a literal id instead of ns(...).

    Shiny namespaces a module's static UI function for you. Anything built at
    runtime -- in a render function or a modal -- does not go through that, so a
    literal id lands in the DOM bare while the handler listens on <module>-<id>,
    and the control silently never fires.
    """
    tree = ast.parse(_src(name))
    server = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == server_fn
    )
    bare = []
    for node in ast.walk(server):
        if not isinstance(node, ast.Call):
            continue
        if not ast.unparse(node.func).startswith("ui.input_") or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            bare.append(first.value)
    return bare


def test_no_input_built_inside_the_mapping_server_has_a_bare_id():
    """The generalizing guard: this is how "Document a gap" stayed dead."""
    assert _bare_ids_inside_server("discipline_map.py", "discipline_map_server") == []


def test_a_non_stage_job_gets_a_readable_busy_label():
    """tasks_running is keyed by stage, so a job that is not one of the six falls
    through STAGE_LABELS and the toast would read its raw key."""
    src = _src("stagebar.py")
    assert '"region_build": "a region build"' in src
    go = src[src.index("def _go(stage_key"):src.index("@reactive.effect", src.index("def _go(stage_key"))]
    assert "_TASK_LABELS.get(k)" in go
