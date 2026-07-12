"""EASI batch engine: a stable, UI-free surface for running the full assessment
over N point locations, with structured results, bounded concurrency, retry
classification, cancellation, and reference-condition qualification.

Public entry points live in ``easi.batch.api``. Serializable contracts live in
``easi.batch.contracts``. This package is what the EASI batch UI and (vendored)
StreamCurves screening both call.
"""
from __future__ import annotations

ENGINE_API_VERSION = 1
