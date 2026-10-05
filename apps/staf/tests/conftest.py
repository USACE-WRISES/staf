"""The STAF suite loads the STAF app once, with the tools' own test settings: EASI never reaches the
library release for its adopted method, the site engine asks the services (which no test reaches),
DEEP's remote library stays off, and the shared caches live in a temporary folder."""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
os.environ.setdefault("EASI_ADOPTED_METHOD", "0")
os.environ.setdefault("STAF_DATA_SOURCE", "service")
os.environ["DEEP_REMOTE_LIBRARY"] = "0"
os.environ.setdefault("STAF_RUNTIME_DIR", tempfile.mkdtemp(prefix="staf-tests-"))
sys.path.insert(0, str(HERE))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def staf():
    """The STAF app (apps/staf/app.py), loaded once: its tools are in ``staf.TOOLS``."""
    import app
    return app
