"""SELECT-01 is answerable on the Publish page (2026-09-20).

A function carrying more metrics than the portfolio maximum publishes only with a
recorded human approval. The batch runner takes one as ``--approve-portfolio`` and an
opened build carries its own on the origin, but a session opened as a project file has
neither, so the interactive publish used to come back with the gate's error and no way
to answer it. The page now names the functions and records the publisher's approval.

These tests pin the three pieces that have to agree: the count the page makes without
building a bundle, the count the gate makes from the real bundle, and the handler that
turns the checkbox into ``meta['portfolioApprovals']``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

import views.publish as pub
from streamcurves import deep_export, library as lib
from streamcurves.deep_export import build_deep_assessment_bundle, deep_collect_curve_rows
from views import assessment_publish as ap
from views.state import AppState

FUNCTION = "Water & soil quality"
FUNCTION_ID = "water-soil-quality"
TRIPLE = ("chem_COND", "chem_PH", "chem_TURB")


def _row(metric):
    return {"metric": metric, "display_name": np.nan, "higher_is_better": False,
            "curve_status": "complete", "stratum": "",
            "curve_points": pd.DataFrame({"point_order": [1, 2, 3],
                                          "metric_value": [0.0, 10.0, 50.0],
                                          "index_score": [1.0, 0.7, 0.0]})}


def _completed(metrics=TRIPLE):
    out = {}
    for m in metrics:
        rows = pd.DataFrame([_row(m)])
        rows["curve_points"] = [_row(m)["curve_points"]]
        out[m] = {"phase4_curve_rows": rows}
    return out


def _mapping(metrics=TRIPLE):
    return pd.DataFrame(
        [(m, "Physicochemistry", FUNCTION, i) for i, m in enumerate(metrics, 1)],
        columns=["metric_key", "discipline", "function_label", "sort_order"])


def _state(metrics=TRIPLE, *, origin=None) -> AppState:
    st = AppState.fresh()
    st.completed_metrics.set(_completed(metrics))
    st.discipline_function_mapping.set(_mapping(metrics))
    st.assessment_source.set(origin)
    return st


# ---- the count the page makes, without building a bundle --------------------
def test_the_quick_count_matches_the_bundle():
    """The page's count and the gate's count must name the same function, or the
    page asks for an approval the gate does not want, or misses one it does."""
    completed, mapping = _completed(), _mapping()
    quick = deep_export.metrics_per_function_quick(completed, mapping)
    bundle = build_deep_assessment_bundle(deep_collect_curve_rows(completed), mapping, {}, {})
    from_bundle = {str(b["functionId"]): len(b["metrics"]) for b in bundle["metricsByFunction"]}
    assert quick[FUNCTION_ID] == from_bundle[FUNCTION_ID] == 3
    assert lib.functions_over_metric_limit(bundle) == [(FUNCTION_ID, 3)]


def test_the_quick_count_adds_the_metrics_that_join_outside_the_mapping():
    """A pressure-screen build's fixed criteria are in the bundle but not in the
    editable mapping, so a function can reach the maximum on them."""
    counts = deep_export.metrics_per_function_quick(
        _completed(("chem_COND",)), _mapping(("chem_COND",)),
        extra={FUNCTION_ID: 2, "catchment-hydrology": 2})
    assert counts[FUNCTION_ID] == 3 and counts["catchment-hydrology"] == 2


def test_nothing_built_yet_counts_nothing():
    assert deep_export.metrics_per_function_quick({}, _mapping()) == {}


# ---- what the page asks for -------------------------------------------------
def test_the_page_asks_for_an_approval_when_a_function_is_over_the_maximum():
    pending = ap.portfolio_approval_needed(_state())
    assert [p["functionId"] for p in pending] == [FUNCTION_ID]
    assert pending[0]["nMetrics"] == 3
    assert pending[0]["functionName"]                      # named, not just an id


def test_two_metrics_need_no_approval():
    assert ap.portfolio_approval_needed(_state(TRIPLE[:2])) == []


def test_an_origin_approval_is_not_asked_for_twice():
    """An opened agent build already carries the approval its own stage recorded."""
    origin = {"portfolio_approvals": [{"functionId": FUNCTION_ID, "approvedBy": "owner"}]}
    assert ap.portfolio_approval_needed(_state(origin=origin)) == []


def test_a_metric_the_review_removed_is_not_counted():
    """The page counts what the bundle will carry: a removed third metric does not
    put its function over the maximum, so no approval is asked for."""
    st = _state()
    st.curve_review.set({m: {"status": "auto_ok",
                             "decision": "removed_from_scope" if m == TRIPLE[2] else "auto_finalized"}
                         for m in TRIPLE})
    assert ap.portfolio_approval_needed(st) == []


def test_an_approval_without_an_approver_does_not_count():
    origin = {"portfolio_approvals": [{"functionId": FUNCTION_ID, "approvedBy": ""}]}
    assert [p["functionId"] for p in ap.portfolio_approval_needed(_state(origin=origin))] \
        == [FUNCTION_ID]


def test_the_prompt_names_the_function_and_its_count():
    text = pub._portfolio_approval_text(
        [{"functionId": FUNCTION_ID, "functionName": "Water and soil quality", "nMetrics": 3}])
    assert "Water and soil quality (3 metrics)" in text and "SELECT-01" in text
    assert "—" not in text and ";" not in text            # project style gates


# ---- what the handler does with the checkbox --------------------------------
SRC = Path(pub.__file__).read_text(encoding="utf-8")


def test_the_form_emits_the_checkbox_and_the_handler_reads_it():
    assert '"pub_select01"' in SRC
    assert "input.pub_select01()" in SRC


def test_the_handler_judges_the_real_bundle_and_records_the_publisher():
    """The page's quick count may be stale; the publish itself reads the bundle it
    is about to write, and the approval is recorded under the publisher's name."""
    assert "lib.functions_over_metric_limit(bundle)" in SRC
    assert '"approvedBy": maintainer' in SRC


def test_an_unticked_box_refuses_before_anything_is_written():
    """The refusal restores the stage stamp the handler set, like the failure path."""
    handler = SRC[SRC.index("if unapproved:"):SRC.index("if source_doc:")]
    assert "state.run_stage_status.set(prev_stage_status)" in handler
    assert "return" in handler


@pytest.mark.parametrize("ident", ["pub_select01"])
def test_the_new_id_is_listed_with_the_other_handler_ids(ident):
    """views/publish.py's own contract test reads HANDLER_IDS; keep them in step."""
    from tests.test_publish_page import HANDLER_IDS
    assert ident in HANDLER_IDS
