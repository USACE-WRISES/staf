"""STAF Site Computation Engine — true point-watershed site computation.

Public surface: ``compute_site(lat, lon, config=None)`` returning a
JSON-serializable SiteComputation dict (see ``provenance.py`` for the
contract), plus the identity constants below. Never raises to callers;
failures degrade into the record with recorded reasons.
"""
from __future__ import annotations

ENGINE_ID = "site-engine"
ENGINE_VERSION = "0.1.0"

from .engine import compute_site  # noqa: E402,F401  (public API)
