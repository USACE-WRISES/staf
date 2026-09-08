"""STAF site engine (provenance token ``site-engine``): HR reach watershed
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
# 0.4.1 (2026-09-08): NLCD land cover outside the product's footprint.
# A watershed straddling the Canadian border raised inside cover_statistics
# and lost the whole support; one in Alaska or Hawaii raised nothing and
# reported zero percent of every class. The covered path is unchanged; a
# second pass scores the covered cells alone and reports the fraction, and
# a polygon with no covered cell yields no value rather than a zero.
ENGINE_VERSION = "0.4.1"

from . import naming  # noqa: E402,F401  (pure vocabulary module, cheap import)


def __getattr__(name: str):
    if name == "compute_site":
        from .engine import compute_site
        return compute_site
    raise AttributeError(f"module 'site_engine' has no attribute {name!r}")
