import os

import pytest
# Presence of this file puts the repo root on sys.path so `import deep` works
# under any pytest invocation.

# The remote library (deep/remote_library.py) is off for every test: no test may
# reach the release or read a disk cache a local run left behind. Set at import,
# not in a fixture, because a test module reads config._registry_records() while
# it is collected. tests/test_remote_library.py turns it on per test, with a tmp
# cache folder and a fake fetch.
os.environ["DEEP_REMOTE_LIBRARY"] = "0"


@pytest.fixture(autouse=True)
def _clear_fabric_feature_memo():
    """The fabric feature memo is per process (2026-09-04): a test's answer
    must never satisfy the next test's ask."""
    from deep.datasources import fabric
    fabric.clear_feature_memo()
    yield
    fabric.clear_feature_memo()
