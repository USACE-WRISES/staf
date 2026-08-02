"""Shared test helpers for the StreamCurves suite."""

from __future__ import annotations

from streamcurves import deep_export


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
