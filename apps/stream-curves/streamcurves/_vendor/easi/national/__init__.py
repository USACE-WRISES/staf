"""The national precomputed EASI dataset: contracts, offline providers, the
dataset client, and the map-tile store.

The dataset stores, per NHDPlus V2 COMID, the *evidence* every metric adapter
reads (the StreamCat row, the flowline attributes, the NRSA record, the four
point-service answers, the bankfull block) and the baked scores. EASI never
re-fetches that evidence: ``assessment.assess_preloaded`` runs the unchanged
adapters over a record and rebuilds the full report in about a millisecond,
so a precomputed reach reads exactly like a live run minus the network.

Nothing in this package imports pyarrow, pmtiles, or requests at module import
time; those live inside the functions that need them so the vendored copy in
StreamCurves stays importable.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from .. import config

#: The evidence / scores record schema written by the builder and read here.
SCHEMA_VERSION = 2

#: The rolling GitHub prerelease that carries the dataset (always a prerelease).
DATASET_TAG = "easi-national-current"

# The code and catalog files that turn evidence into ratings. Their digest is
# the dataset's ``method_version``: baked scores computed under a different
# digest are stale, while a report recalled from evidence always uses the
# current methods.
_METHOD_SOURCES = ("scoring.py", "screening_methods.py", "config.py",
                   "watershed.py", "assessment.py", "bieger.py", "geo.py",
                   "datasources/fabric.py", "national/records.py")
_METHOD_DATA = ("easi-metrics.json",
                "cwa-mapping.json", "functions.json", "ecoregion-crosswalk.json",
                "reference-curves.json")


def method_version() -> str:
    """A short, deterministic digest of the scoring method."""
    return _method_version(config.criteria_set())


@lru_cache(maxsize=2)
def _method_version(criteria_set: str) -> str:
    pkg = Path(__file__).resolve().parent.parent
    files = [pkg / name for name in _METHOD_SOURCES]
    files += sorted((pkg / "metrics").glob("*.py"))
    files += [Path(config.DATA_DIR) / name
              for name in (config.screening_methods_filename(), *_METHOD_DATA)]
    digest = hashlib.sha256()
    digest.update(criteria_set.encode("utf-8") + b"\0")
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()[:12]


method_version.cache_clear = _method_version.cache_clear
