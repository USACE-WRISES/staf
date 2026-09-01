"""The per-metric anchor annotation pass.

Invariants: covered runs get neutral labels and NOTHING else changes
(labels-only enrichment); routed runs label where each row's evidence comes
from (the exact watershed, the clicked stream, the nearest covered reach, or
unavailable) and which engine answered it; a StreamCat integrity fallback is
COMID-keyed; a failed Phase 2 re-anchor is stated, never papered over."""
from __future__ import annotations

import copy

from easi import assessment, watershed
from easi.metrics import biology, geomorphology, hydraulics, hydrology, registry


def _rows():
    return [
        {"metricId": hydraulics.HYPORHEIC_ID, "name": "Hyporheic Exchange",
         "rating": "Good", "usedFallback": False},
        {"metricId": hydrology.IMPERVIOUS_ID, "name": "Impervious",
         "rating": "Fair", "usedFallback": False},
        {"metricId": biology.INVASIVES_ID, "name": "Invasives",
         "rating": "Fair", "usedFallback": False},
        {"metricId": hydraulics.LOW_FLOW_ID, "name": "Low Flow",
         "rating": "Poor", "usedFallback": True,
         "evidenceFamily": "iwi_landscape"},          # StreamCat HYD fallback fired
        {"metricId": geomorphology.CHANNEL_EVOL_ID, "name": "Channel Evolution",
         "rating": "Fair", "usedFallback": True,
         "evidenceFamily": "incision_geometry"},      # a 3DEP proxy, not StreamCat
    ]


def _hr_anchor(applied=True, declined=False):
    return {"anchorKind": "hrSurrogate",
            "scoredReach": {"comid": 5214461},
            "routing": {"routedDistanceFt": 1240.4, "daRatio": 1.8,
                        "declined": declined},
            "reanchored": {"applied": applied, "warnings": []}}


_ENGINE_LAYER = watershed.from_engine({"engineVersion": "0.2.0", "metrics": {}})


def test_metric_anchor_map_covers_all_registered_metrics():
    assert set(registry.METRIC_ANCHOR) == set(registry.REGISTRY)
    assert set(registry.METRIC_ANCHOR.values()) == {
        "clickedReach", "clickedPoint", "surrogateComid", "watershed"}


def test_covered_run_is_labels_only():
    rows = _rows()
    before = copy.deepcopy(rows)
    assessment._annotate_anchors(rows, None)
    for r, b in zip(rows, before):
        extra = {k: v for k, v in r.items() if k not in b}
        assert set(extra) == {"anchor", "anchorLabel", "engine", "engineLabel"}
        assert {k: v for k, v in r.items() if k in b} == b   # nothing mutated
    assert rows[0]["anchor"] == "clickedReach"
    assert rows[0]["anchorLabel"] == "assessed reach"        # neutral wording
    assert rows[0]["engine"] == "" and rows[0]["engineLabel"] == ""
    assert rows[1]["anchorLabel"] == "assessed watershed"
    assert rows[1]["engine"] == "streamcat"
    assert rows[1]["engineLabel"] == "StreamCat lookup engine"
    assert rows[3]["anchor"] == "surrogateComid" and rows[3]["engine"] == "streamcat"
    assert rows[4]["anchor"] == "clickedReach"              # incision geometry stays put


def test_routed_run_labels_the_exact_watershed():
    rows = _rows()
    anchor = _hr_anchor(applied=True)
    assessment._annotate_anchors(rows, anchor, watershed_layer=_ENGINE_LAYER)
    assert rows[0]["anchorLabel"] == "clicked HR reach"
    assert rows[1]["anchorLabel"] == "exact watershed (STAF site engine)"
    assert rows[1]["engine"] == "site-engine"
    assert rows[1]["engineLabel"] == "STAF site engine"
    assert rows[2]["anchorLabel"] == "clicked point"
    # fallback rule: a StreamCat integrity component is COMID-keyed
    assert rows[3]["anchor"] == "surrogateComid"
    assert rows[3]["anchorLabel"] == \
        "nearest covered reach (COMID 5214461, 1,240 ft downstream)"
    assert rows[3]["engine"] == "streamcat"
    # the banner table is stamped onto the anchor payload
    table = anchor["metricAnchors"]
    assert table[hydraulics.HYPORHEIC_ID]["name"] == "Hyporheic Exchange"
    assert table[biology.INVASIVES_ID]["anchor"] == "clickedPoint"
    assert table[hydrology.IMPERVIOUS_ID]["engine"] == "site-engine"


def test_routed_legacy_labels_the_surrogate_watershed():
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor(),
                                 watershed_layer=watershed.from_streamcat({}))
    assert rows[1]["anchorLabel"] == "surrogate watershed (StreamCat lookup engine)"
    assert rows[1]["engine"] == "streamcat"
    # no layer at all reads as the lookup engine too
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor())
    assert rows[1]["anchorLabel"] == "surrogate watershed (StreamCat lookup engine)"


def test_declined_routing_withholds_comid_rows():
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor(declined=True),
                                 watershed_layer=_ENGINE_LAYER)
    assert rows[3]["anchorLabel"] == "unavailable past the substitution limit"
    assert rows[3]["engine"] == "unavailable"
    assert rows[1]["anchorLabel"] == "exact watershed (STAF site engine)"


def test_unavailable_layer_is_stated():
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor(),
                                 watershed_layer=watershed.unavailable("budget"))
    assert rows[1]["anchorLabel"] == "unavailable (exact watershed not calculated)"
    assert rows[1]["engine"] == "unavailable"
    assert rows[1]["engineLabel"] == "watershed evidence unavailable"


def test_failed_reanchor_is_stated():
    rows = _rows()
    assessment._annotate_anchors(rows, _hr_anchor(applied=False),
                                 watershed_layer=_ENGINE_LAYER)
    assert rows[0]["anchorLabel"] == "surrogate reach (HR data unavailable)"
    assert rows[2]["anchorLabel"] == "surrogate reach (HR data unavailable)"
    # watershed metrics never depended on the re-anchor; their label is unchanged
    assert rows[1]["anchorLabel"] == "exact watershed (STAF site engine)"
