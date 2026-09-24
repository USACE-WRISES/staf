"""Keep StreamCurves' own process on the EASI method it ships.

StreamCurves' vendored EASI (``_vendor/easi``) is an input to DEEP authoring: the
CURVE-11 fixed criteria, the REF-04 reference screen, the DEEP calculator's CWA
mapping and every run manifest's ``easiMethodVersion`` read it. A stray
``EASI_METHOD_PACKAGE``, ``EASI_DATA_DIR`` or ``EASI_CRITERIA_SET`` in a maintainer's
shell would switch that copy silently, so StreamCurves' entry points (the app and the
DEEP build scripts) clear them before anything imports the vendored copy. EASI
method versions are scored only in worker processes
(``streamcurves.easi_method.worker``), which the parent marks with
``STREAMCURVES_EASI_WORKER=1`` and hands the package explicitly.
"""
from __future__ import annotations

import logging
import os
from typing import MutableMapping

EASI_SWITCHES = ("EASI_METHOD_PACKAGE", "EASI_DATA_DIR", "EASI_CRITERIA_SET")
WORKER_ENV = "STREAMCURVES_EASI_WORKER"

logger = logging.getLogger("streamcurves")


def sanitize(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Remove the EASI method switches from this process's environment (never in a
    worker). Returns the names removed, which the caller may log."""
    if environ.get(WORKER_ENV) == "1":
        return []
    removed = [k for k in EASI_SWITCHES if k in environ]
    for k in removed:
        environ.pop(k, None)
    if removed:
        logger.warning("ignored %s: StreamCurves scores DEEP inputs with the EASI method it "
                       "ships; EASI method versions run in evaluation workers", ", ".join(removed))
    return removed
