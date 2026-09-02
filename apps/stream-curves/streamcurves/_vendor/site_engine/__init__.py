"""STAF site engine (provenance token ``site-engine``): true point-watershed
site computation.

Public surface: ``compute_site(lat, lon, config=None, *, progress=None)``
returning a JSON-serializable SiteComputation dict (see ``provenance.py`` for
the contract), the identity constants below, the ``naming`` vocabulary module
(tokens and display names for both STAF watershed engines), and ``anchor``
(shared site anchoring for the consuming apps). Never raises to callers;
failures degrade into the record with recorded reasons.

``compute_site`` resolves lazily so that importing ``naming`` from a vendored
copy at UI import time costs nothing: the delineation and metric stack loads
on first use.
"""
from __future__ import annotations

ENGINE_ID = "site-engine"
ENGINE_VERSION = "0.2.2"

from . import naming  # noqa: E402,F401  (pure vocabulary module, cheap import)


def __getattr__(name: str):
    if name == "compute_site":
        from .engine import compute_site
        return compute_site
    raise AttributeError(f"module 'site_engine' has no attribute {name!r}")
