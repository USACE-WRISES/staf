import pytest
import pathlib
import sys

# Ensure the repo root (containing the `sfari` package + `data/`) is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _clear_fabric_feature_memo():
    """The fabric feature memo is per process (2026-09-04): a test's answer
    must never satisfy the next test's ask."""
    from sfari.datasources import fabric
    fabric.clear_feature_memo()
    yield
    fabric.clear_feature_memo()
