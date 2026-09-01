"""Layer B: exact-watershed and reach-scale metric computation.

Each metric module registers a compute function; ``compute_all`` runs the
registry over one Layer A record and returns the ``metrics`` block. A metric
that cannot compute contributes an entry with a warning (or is absent when its
source is feasibility-excluded), never an exception. ``families`` selects a
subset of the registry (``config["metricFamilies"]``); the run order is the
sorted family name, so records stay deterministic whatever the caller lists.

Families: landcover, roads, dams, soils, runoff, xsection (see ``families()``).
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from ..progress import notify

# name -> compute(record, tree_geoms) -> dict of metric entries
_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def families() -> tuple[str, ...]:
    """Every registered metric family, sorted (the run order)."""
    return tuple(sorted(_REGISTRY))


def compute_all(record: dict, *, tree_geoms: list,
                families: Optional[Iterable[str]] = None,
                progress: Optional[Callable[[dict], Any]] = None) -> dict:
    """Run the selected metric families (all when ``families`` is None) and
    merge their entries. Unknown names are ignored with a record warning."""
    if families is None:
        wanted = sorted(_REGISTRY)
    else:
        asked = {str(f) for f in families}
        unknown = sorted(asked - set(_REGISTRY))
        if unknown:
            record.setdefault("warnings", []).append(
                "unknown metric families ignored: " + ", ".join(unknown))
        wanted = [name for name in sorted(_REGISTRY) if name in asked]
    out: dict = {}
    for name in wanted:
        notify(progress, stage="metrics", family=name)
        try:
            entries = _REGISTRY[name](record, tree_geoms) or {}
        except Exception as exc:  # noqa: BLE001 - never break the record
            entries = {f"{name}Error": {
                "value": None, "unit": "", "source": name, "vintage": "",
                "spatialSupport": "pointWatershed",
                "warnings": [f"metric family failed: {exc}"]}}
        out.update(entries)
    return out


# Import metric modules for their registration side effects.
from . import landcover  # noqa: E402,F401
from . import roads      # noqa: E402,F401
from . import dams       # noqa: E402,F401
from . import runoff     # noqa: E402,F401
from . import soils      # noqa: E402,F401
from . import xsection   # noqa: E402,F401
