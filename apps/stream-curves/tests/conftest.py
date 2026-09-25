"""Shared test helpers for the StreamCurves suite."""

from __future__ import annotations

import os

import pytest

from streamcurves import deep_export


@pytest.fixture(autouse=True)
def _restore_process_environment():
    """Give every test the environment it started with.

    The batch entry points set process-global switches on purpose (``promote`` and
    ``regional_agent.publish`` set ``STAF_LIBRARY_ROOT``, the stage clears the EASI
    switches, experimental runs set ``STREAMCURVES_CONFIG_ROOT``); a test that drives
    them in-process must not leak those into the next module (campaign Round 1: the
    publish-gate tests failed after the stage-decision tests when run together).
    """
    saved = dict(os.environ)
    yield
    for key in list(os.environ):
        if key not in saved:
            del os.environ[key]
    for key, value in saved.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def documented_exclusions(reason: str = "no-suitable-metric",
                          justification: str = "Out of scope for this test fixture; "
                                               "documented so the publish gate can pass.",
                          recorded_by: str = "test-suite") -> list[dict]:
    """A coverage exception for every STAF function, for bundle fixtures.

    ``library.publish_version`` refuses a version while any of the 20 functions is
    neither covered nor justified, so a fixture holding one or two metrics needs its
    remaining functions on the record. Blanket-listing all 20 is safe:
    ``deep_export.function_coverage`` drops any exception naming a function the
    bundle actually covers, so callers do not have to track which those are.
    """
    return [
        {
            "functionId": str(f.get("id")),
            "reason": reason,
            "justification": justification,
            "recordedBy": recorded_by,
        }
        for f in deep_export.deep_read_staf_crosswalk()
    ]
