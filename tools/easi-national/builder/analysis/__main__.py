"""``python -m builder.analysis --steps ...``: run analysis steps in this
process, with a heartbeat of its own (``state/analysis_progress.json``) so a
queue worker running at the same time keeps ``progress.json`` to itself."""
from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from ..paths import DataRoot
from ..state import CancelRequested, Control, PauseRequested, Progress, UnitStates
from . import STEPS
from .runner import run_analysis


@dataclass
class AnalysisProgress(Progress):
    """The worker's heartbeat class writing to a sibling file."""

    @property
    def path(self) -> Path:
        return self.root.state / "analysis_progress.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m builder.analysis",
                                 description="EASI sensitivity analysis over the national dataset")
    ap.add_argument("--root", default=None, help="data root (default EASI_NATIONAL_ROOT)")
    ap.add_argument("--steps", nargs="*", default=None, choices=list(STEPS),
                    help="only these steps, in canonical order (default: all)")
    ap.add_argument("--force", action="store_true", help="rerun the steps even when up to date")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--levels", nargs="*", default=None)
    ap.add_argument("--screen", default=None)
    ap.add_argument("--phase", default=None)
    ap.add_argument("--reuse", nargs="*", default=None, choices=["distributions", "validation", "stability"],
                    help="stats parts whose existing outputs are reused instead of recomputed")
    args = ap.parse_args(argv)
    root = DataRoot(Path(args.root)) if args.root else DataRoot.default()
    root.ensure()
    options = {key: value for key, value in (("workers", args.workers), ("levels", args.levels),
                                             ("screen", args.screen), ("phase", args.phase), ("reuse", args.reuse))
               if value is not None}
    states, progress, control = UnitStates(root), AnalysisProgress(root), Control(root)
    progress.job = "Analysis"
    try:
        ran = run_analysis(root, states, progress, control, steps=args.steps,
                           force=args.force, options=options)
    except PauseRequested:
        progress.finish("paused")
        return 2
    except CancelRequested:
        progress.finish("cancelled")
        return 3
    except Exception:
        traceback.print_exc()
        progress.finish("failed")
        return 1
    progress.finish("done: " + (", ".join(ran) if ran else "everything up to date"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
