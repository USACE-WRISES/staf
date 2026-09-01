"""Progress reporting for long engine runs.

A caller passes ``progress`` (any callable) to ``compute_site``; it receives
plain dict events such as ``{"stage": "walk", "hops": 7, "reaches": 43}``.
Events never enter the SiteComputation record (the record stays
deterministic) and a failing callback never breaks a run.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

STAGES = ("site", "walk", "catchments", "union", "geometry", "reach",
          "metrics", "done")


def notify(progress: Optional[Callable[[dict], Any]], **event: Any) -> None:
    if progress is None:
        return
    try:
        progress(dict(event))
    except Exception:  # noqa: BLE001 - the UI is never allowed to break a run
        pass
