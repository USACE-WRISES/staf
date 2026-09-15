"""The EASI sensitivity analysis (the stress test) over the national dataset.

Pure computation over the data root, in checkpointed steps that each write
under ``<root>/analysis/`` and are skipped while their inputs digest matches.
Nothing here changes EASI's scoring code or catalog, so ``method_version()``
and the published scores stay untouched; the analysis reads the evidence,
re-scores it with the app's own evaluator, harvests the raw metric inputs,
builds reference panels and curves per ecoregion level, simulates the
candidate scoring schemes, and writes the report the owner decides from.

    python -m builder.analysis --steps strata candidates ...
    python -m builder.worker analysis --steps ...          (also a queue job)

The curve engine is StreamCurves' ``streamcurves.curves`` (imported from
``apps/stream-curves``, never vendored); ``bootstrap_stream_curves`` puts it on
``sys.path`` when this package is imported.
"""
from __future__ import annotations

from .. import bootstrap_stream_curves

#: Rides every step's inputs digest: bump it when a step's meaning changes.
ANALYSIS_VERSION = "0.1.0"

#: The steps in the order they run (each may be selected alone once its
#: upstream outputs exist).
STEPS = ("strata", "candidates", "erom", "attains", "landscape", "values", "nrsa",
         "panels", "curves", "runs", "stats", "report")

bootstrap_stream_curves()
