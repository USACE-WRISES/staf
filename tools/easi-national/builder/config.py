"""Builder configuration: the data root, service endpoints, pacing, and the
constants the dataset is stamped with."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ENV_ROOT = "EASI_NATIONAL_ROOT"
DEFAULT_ROOT = Path(r"D:\Data\easi-national")

#: GitHub repo and rolling tag the dataset publishes to (always a prerelease).
GITHUB_REPO = "USACE-WRISES/staf"
DATASET_TAG = "easi-national-current"

#: Dataset stamps. Tier 1 = the 16 metrics from lookups; Tier 2 adds the four
#: cross-section metrics from the stored 3DEP archive. A published HUC4 unit is
#: stamped per unit; the manifest scalar is the minimum over published units.
BASE_TIER = 1
XS_TIER = 2
TIER = BASE_TIER
REACH_LENGTH_FT = 1000.0
#: NRSA evidence window anchor (``nrsa.evidence_for_reach(as_of=...)``); pinned so
#: a re-run months later scores the same records.
NRSA_AS_OF = date(2026, 9, 10)
#: Point-service radii the app uses, plus the buffer the chunk pulls carry so
#: reaches near a chunk edge see every station/dam/assessment unit they should.
WQP_RADIUS_MI = 5.0
NID_RADIUS_MI = 1.0
ATTAINS_BUFFER_M = 2000.0
CHUNK_BUFFER_MI = 10.0
WQP_YEARS = 10

#: Service endpoints (the app's own, see apps/easi/easi/datasources).
STREAMCAT_URL = "https://api.epa.gov/StreamCat/streams/metrics"
FABRIC_BASE = "https://api.water.usgs.gov/fabric/pygeoapi/collections"
WQP_RESULT_URL = "https://www.waterqualitydata.us/data/Result/search"
WQP_STATION_URL = "https://www.waterqualitydata.us/data/Station/search"
ATTAINS_BASE = "https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer"
NID_URL = ("https://geospatial.sec.usace.army.mil/dls/rest/services/NID/"
           "National_Inventory_of_Dams_Public_Service/FeatureServer/0/query")
NAS_URL = "https://nas.er.usgs.gov/api/v2/occurrence/search"

#: Request sizing and pacing.
FABRIC_IN_CHUNK = 150           # COMIDs per fabric IN filter: 160 works, 200 is refused (403), 900 is a 414
STREAMCAT_COMID_CHUNK = 500     # COMIDs per StreamCat POST when not by state
STREAMCAT_NAME_GROUP = 5        # metric names per StreamCat request
STREAMCAT_CONCURRENCY = int(os.environ.get("EASI_NATIONAL_STREAMCAT_CONCURRENCY") or 3)   # region pulls in flight
ATTAINS_PAGE = 500              # 1,000 with geometry answers a server-side 500; 500 pages fine
NID_PAGE = 2000
NAS_MIN_INTERVAL_S = 0.3        # <= ~3-4 requests per second
HTTP_TIMEOUT_S = 120.0
WQP_TIMEOUT_S = 1500.0          # cells are pre-sized by station count; a slower one is split, never retried whole
WQP_CONCURRENCY = int(os.environ.get("EASI_NATIONAL_WQP_CONCURRENCY") or 8)   # requests in flight at once (paced per host)
#: stations = station lists per cell, then results by station-id batches (the fast
#: shape); cells = the older bounding-box pull
WQP_METHOD = (os.environ.get("EASI_NATIONAL_WQP_METHOD") or "stations").strip().lower()
#: First calendar month of the national monthly WQP pull (ten years back).
WQP_MONTHLY_START = (os.environ.get("EASI_NATIONAL_WQP_MONTHLY_START") or "2016-09").strip()
#: Months downloaded at once in the national monthly WQP pull.
WQP_MONTHLY_WORKERS = int(os.environ.get("EASI_NATIONAL_WQP_MONTHLY_WORKERS") or 3)
WQP_BATCH_TIMEOUT_S = 240.0     # the portal gateway drops a request silent for ~180 s; longer is dead
FABRIC_CONCURRENCY = int(os.environ.get("EASI_NATIONAL_FABRIC_CONCURRENCY") or 3)   # flowline batches in flight
#: Processes for the per-HUC8 derive, joins and score steps (pure computation).
HUC8_WORKERS = int(os.environ.get("EASI_NATIONAL_HUC8_WORKERS") or max(1, min(6, (os.cpu_count() or 2) - 1)))
#: Processes for the two cross-section stages: the sampling waits on 3DEP range
#: reads (about 0.5 s a reach in one process, 3 MB of reads), the derivation is
#: pure numpy; one process per CPU, 8 to 16.
XS_WORKERS = int(os.environ.get("EASI_NATIONAL_XS_WORKERS") or max(8, min(16, os.cpu_count() or 8)))
#: Estimated download budget for the sampling per calendar month (the run is
#: paced under a home connection's data cap); the stage pauses when reached.
XS_BYTE_BUDGET_GB = float(os.environ.get("EASI_NATIONAL_XS_BYTE_BUDGET_GB") or 800)
#: Threads listing the 3DEP bucket when the 1 m catalog is built.
DEM_CATALOG_WORKERS = int(os.environ.get("EASI_NATIONAL_DEM_CATALOG_WORKERS") or 4)

#: The 21 StreamCat base names the adapters read (registry.STREAMCAT_NAMES is
#: the source of truth; imported lazily to avoid the easi import at config time).
STREAMCAT_AOIS = ("ws", "cat", "wsrp100")


def data_root() -> Path:
    """The data root: ``EASI_NATIONAL_ROOT`` or ``D:\\Data\\easi-national``."""
    return Path(os.environ.get(ENV_ROOT) or DEFAULT_ROOT)


def streamcat_names() -> list[str]:
    from easi.metrics import registry
    return list(registry.STREAMCAT_NAMES)
