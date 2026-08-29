"""The per-metric anchor annotation pass.

Invariants: covered runs get neutral labels and NOTHING else changes
(labels-only enrichment); routed runs label the substitution per the
framework-fixed map; the StreamCat-fallback rule forces surrogateWatershed;
a failed Phase 2 re-anchor is stated, never papered over."""
from __future__ import annotations

import copy

from easi import assessment
from easi.metrics import biology, hydraulics, hydrology, registry


def _rows():
    return [
        {"metricId": hydraulics.HYPORHEIC_ID, "name": "Hyporheic Exchange",
         "rating": "Good", "usedFallback": False},
        {"metricId": hydrology.IMPERVIOUS_ID, "name": "Impervious",
         "rating": "Fair", "usedFallback": False},
        {"metricId": biology.INVASIVES_ID, "name": "Invasives",
         "rating": "Fair", "usedFallback": False},
        {"metricId": hydraulics.LOW_FLOW_ID, "name": "Low Flow",
         "rating": "Poor", "usedFallback": True},   # StreamCat fallback fired
    ]


def _hr_anchor(applied=True):
    return {"anchorKind": "hrSurrogate",
            "scoredReach": {"comid": 5214461},
            "reanchored": {"applied": applied, "warnings": []}}


def test_metric_anchor_map_covers_all_registered_metrics():
    assert set(registry.METRIC_ANCHOR) == set(registry.REGISTRY)


def test_covered_run_is_labels_only():
    rows = _rows()
    before = copy.deepcopy(rows)
    assessment._annotate_anchors(rows, None)
    for r, b in zip(rows, before):
        extra = {k: v for k, v in r.items() if k not in b}
        assert set(extra) == {"anchor", "anchorLabel"}
        assert {k: v for k, v in r.items() if k in b} == b   # nothing mutated
    assert rows[0]["anchor"] == "clickedReach"
    assert rows[0]["anchorLabel"] == "assessed reach"        # neutral wording
    assert rows[1]["anchorLabel"] == "assessed watershed"


def test_routed_run_labels_the_substitution():
    rows = _rows()
    anchor = _hr_anchor(applied=True)
    assessment._annotate_anchors(rows, anchor)
    assert rows[0]["anchorLabel"] == "clicked HR reach"
    assert rows[1]["anchorLabel"] == "surrogate watershed"
    assert rows[2]["anchorLabel"] == "clicked point"
    # fallback rule: a StreamCat fallback anchors the surrogate watershed
    assert rows[3]["anchor"] == "surrogateWatershed"
    # the banner table is stamped onto the anchor payload
    table = anchor["metricAnchors"]
    assert table[hydraulics.HYPORHEIC_ID]["name"] == "Hyporheic Exchange"
    assert table[biology.INVASIVES_ID]["anchor"] == "clickedPoint"


def test_failed_reanchor_is_stated():
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor(applied=False))
    assert rows[0]["anchorLabel"] == "surrogate reach (HR data unavailable)"
    assert rows[2]["anchorLabel"] == "surrogate reach (HR data unavailable)"
    # watershed metrics were always on the surrogate; their label is unchanged
    assert rows[1]["anchorLabel"] == "surrogate watershed"
