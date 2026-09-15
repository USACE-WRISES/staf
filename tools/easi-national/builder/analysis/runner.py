"""The analysis driver: the steps in order, each one skipped while its inputs
digest matches the done marker (unit ``analysis`` in ``state/units.json``),
rerun with ``force``. Every step module exposes ``inputs(root, options) ->
str`` and ``run(root, progress, control, options)``; the registry below names
them, so a step's code lives next to its siblings without a fixed one-module-
per-step rule."""
from __future__ import annotations

import importlib
from typing import Callable, Optional

from ..paths import DataRoot
from ..state import Control, Progress, UnitStates
from ..stages import common
from . import STEPS

UNIT = "analysis"

#: step -> (module under builder.analysis, inputs function, run function)
REGISTRY: dict[str, tuple[str, str, str]] = {
    "strata": ("strata", "inputs", "run"),
    "candidates": ("candidates", "inputs_streamcat", "run_streamcat"),
    "erom": ("candidates", "inputs_erom", "run_erom"),
    "attains": ("candidates", "inputs_attains", "run_attains"),
    "landscape": ("values", "inputs_landscape", "run_landscape"),
    "values": ("values", "inputs_values", "run_values"),
    "nrsa": ("nrsa", "inputs", "run"),
    "panels": ("panels", "inputs", "run"),
    "curves": ("curves", "inputs", "run"),
    "runs": ("schemes", "inputs", "run"),
    "stats": ("diagnostics", "inputs", "run"),
    "report": ("report", "inputs", "run"),
}


def step_functions(step: str) -> tuple[Callable, Callable]:
    module_name, inputs_name, run_name = REGISTRY[step]
    module = importlib.import_module(f"builder.analysis.{module_name}")
    return getattr(module, inputs_name), getattr(module, run_name)


def run_analysis(root: DataRoot, states: UnitStates, progress: Progress, control: Control,
                 steps: Optional[list[str]] = None, *, force: bool = False,
                 options: Optional[dict] = None) -> list[str]:
    """Run the requested steps (all by default) in canonical order; returns
    the names of the steps that actually ran."""
    options = dict(options or {})
    wanted = [str(s) for s in (steps or STEPS)]
    unknown = sorted(set(wanted) - set(STEPS))
    if unknown:
        raise ValueError(f"unknown analysis steps {unknown}; known: {list(STEPS)}")
    root.ensure()
    root.analysis.mkdir(parents=True, exist_ok=True)
    ran: list[str] = []
    for step in STEPS:
        if step not in wanted:
            continue
        inputs_fn, run_fn = step_functions(step)
        inputs = inputs_fn(root, options)
        control.check()
        if common.run_stage(states, UNIT, step, inputs,
                            lambda: run_fn(root, progress, control, options),
                            progress, force=force):
            ran.append(step)
            progress.say(f"analysis {step}: done")
        else:
            progress.say(f"analysis {step}: up to date")
    return ran
