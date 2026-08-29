"""Layer B: exact-watershed and reach-scale metric computation.

Each metric module registers a compute function; ``compute_all`` runs the
registry over one Layer A record and returns the ``metrics`` block. A metric
that cannot compute contributes an entry with a warning (or is absent when its
source is feasibility-excluded), never an exception.

Modules land per the plan's G6 order: landcover, roads, dams, then the
feasibility-gated soils and runoff, then the cross-section re-anchor.
"""
from __future__ import annotations

from typing import Callable

# name -> compute(record, tree_geoms) -> dict of metric entries
_REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def compute_all(record: dict, *, tree_geoms: list) -> dict:
    """Run every registered metric family; merge their entries."""
    out: dict = {}
    for name in sorted(_REGISTRY):
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
