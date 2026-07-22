"""Automatic source selection is a fixed hierarchy, not a user choice.

Every metric now resolves one automatic method chain (connected observation ->
published model -> named screening proxy), so no adapter emits competing automatic
formulas for the user to pick between. ``apply_source_choices`` is retained for
manually supplied evidence and must stay a safe no-op meanwhile: it is what the
worksheet composes with ``rescore``, and an explicit override still wins.
"""
from __future__ import annotations

from easi import assessment, config, scoring, screening_methods
from easi.metrics import hydrology, physicochemistry
from easi.metrics.base import AnalysisContext, MetricResult

WETLANDS_ID = hydrology.WETLANDS_ID


def _ctx(**extras):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1)
    c.extras.update(extras)
    c.extras.setdefault("prefetch_variants", True)
    return c


def _row(mid, res):
    meta = config.metrics_by_id()[mid]
    idx = (scoring.rating_to_index(res.rating, meta.get("indexMidpoints"))
           if res.rating in assessment.VALID else None)
    return {
        "metricId": mid, "functionId": meta["functionId"], "name": meta["name"],
        "rating": res.rating, "generatedRating": res.rating,
        "index": None if idx is None else round(idx, 3),
        "functionScore": None if idx is None else scoring.function_score(idx),
        "valueText": res.value_text, "source": res.source, "confidence": res.confidence,
        "note": res.note, "status": res.status, "criteria": "", "criteriaBands": {},
        "landCover": None, "ripVeg": None, "scoring": res.scoring,
        "completeness": (res.scoring or {}).get("completeness", "complete"),
    }


# --------------------------------------------------------------------------- #
# The fixed-hierarchy contract
# --------------------------------------------------------------------------- #
def test_adapters_do_not_offer_competing_automatic_sources():
    """No automatic result carries alternative source variants for the user to choose."""
    res = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2.0,
                                             "pcthbwet2019ws": 1.0}))
    assert res.rating == "Fair"
    assert res.variants is None and res.source_key is None


def test_wetlands_requires_both_classes_rather_than_swapping_source():
    """A missing class is unknown, not an invitation to score a different source."""
    res = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2.0},
                                  landcover={"wetland_pct": 8.0}))
    assert res.rating is None and res.status == "unavailable"
    assert res.scoring["completeness"] == "not_assessed"


def test_impairment_falls_back_by_hierarchy_not_by_user_choice(monkeypatch):
    """Category 3 is inconclusive, so the documented CHEM fallback applies on its own."""
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point",
                        lambda *a, **k: {"assessment_unit": "AU-3", "ircategory": "3",
                                         "distance_m": 0.0, "match_type": "intersect"})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point",
                        lambda *a, **k: {})
    res = physicochemistry.impairment(_ctx(streamcat={"chemcat": 0.8, "chemws": 0.75}))
    assert res.rating == "Good"
    assert res.variants is None
    assert res.scoring["usedFallback"] is True
    assert res.scoring["evidenceFamily"] == "iwi_landscape"


# --------------------------------------------------------------------------- #
# The retained merge path
# --------------------------------------------------------------------------- #
def test_apply_source_choices_is_a_noop_without_variants():
    res = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2.0,
                                             "pcthbwet2019ws": 1.0}))
    base = assessment._finalize([_row(WETLANDS_ID, res)], 20, {})
    merged = assessment.apply_source_choices(base, {WETLANDS_ID: "nlcd"})
    assert merged["metricRows"][0]["rating"] == base["metricRows"][0]["rating"]
    assert merged["metricRows"][0]["scoring"] == base["metricRows"][0]["scoring"]


def test_apply_source_choices_merges_a_supplied_variant_and_stays_pure():
    """The merge path still works when a variant is present (manual evidence)."""
    res = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2.0,
                                             "pcthbwet2019ws": 1.0}))
    meta = config.metrics_by_id()[WETLANDS_ID]
    alt = MetricResult(WETLANDS_ID, rating="Good", value_text="8.0% wetland (manual)",
                       source="field record", confidence="M",
                       scoring=screening_methods.evaluate(
                           WETLANDS_ID,
                           {"woodyWetland": 6.0, "herbaceousWetland": 2.0},
                           source_tier="manual").trace)
    row = _row(WETLANDS_ID, res)
    row["sourceVariants"] = {"manual": assessment._serialize_variant(WETLANDS_ID, meta, alt)}
    row["sourceChoice"] = None
    base = assessment._finalize([row], 20, {})

    merged = assessment.apply_source_choices(base, {WETLANDS_ID: "manual"})
    assert merged["metricRows"][0]["rating"] == "Good"
    assert merged["metricRows"][0]["sourceChoice"] == "manual"
    # pure: the original report is untouched
    assert base["metricRows"][0]["rating"] == "Fair"


def test_explicit_override_still_wins_after_a_merge():
    res = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2.0,
                                             "pcthbwet2019ws": 1.0}))
    base = assessment._finalize([_row(WETLANDS_ID, res)], 20, {})
    merged = assessment.apply_source_choices(base, {WETLANDS_ID: "manual"})
    rescored = assessment.rescore(merged, {WETLANDS_ID: "Poor"})
    row = rescored["metricRows"][0]
    assert row["rating"] == "Poor" and row["status"] == "override"
    assert row["generatedRating"] == "Fair"      # the generated result is preserved
