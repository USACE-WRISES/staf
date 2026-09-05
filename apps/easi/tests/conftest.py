import pytest


@pytest.fixture(autouse=True)
def _clear_fabric_feature_memo():
    """The fabric feature memo is per process (2026-09-04): a test's answer
    must never satisfy the next test's ask."""
    from easi.datasources import fabric
    fabric.clear_feature_memo()
    yield
    fabric.clear_feature_memo()
