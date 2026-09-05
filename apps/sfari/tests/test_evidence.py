"""Offline tests for the desktop-evidence adapters (inject ctx.extras, no network).

Focus: the catchment-hydrology impervious card also surfaces watershed agriculture and
advises switching the indicator when farming is the more limiting land-cover pressure
(the SFARI analog of EASI's selectable impervious/agricultural indicator). Every value
is the STAF site engine's (2026-09-05).
"""
from sfari import engine_prefill, evidence
from sfari.metrics.base import AnalysisContext


def _ctx(**engine_values):
    c = AnalysisContext(lat=44.0, lon=-114.0, comid=None)   # central Idaho-ish
    rec = {"status": "ok", "engineVersion": "0.3.0",
           "watershed": {"areaSqkm": 5.0, "nReaches": 4},
           "metrics": {k: {"value": v} for k, v in engine_values.items()}}
    c.extras["engine"] = {"status": "ok", "record": rec, "reason": None}
    c.extras["engine_metrics"] = engine_prefill.engine_metrics(rec)
    return c


def test_ev_impervious_reports_both_and_suggests_more_limiting():
    # impervious low (Strongly Agree), agriculture dominant (Strongly Disagree) -> suggest the worse
    r = evidence.ev_impervious(_ctx(imperviousPctWatershed=2.0, cropPctWatershed=55.0,
                                    hayPasturePctWatershed=6.0))
    assert r.status == "ok"
    assert "2.0% impervious" in r.value_text and "agricultural land" in r.value_text
    assert r.suggested_likert == "Strongly Disagree"       # agriculture is more limiting
    assert "impervious 2.0%" in r.note and "agricultural 61.0%" in r.note


def test_ev_impervious_suggests_impervious_when_it_drives():
    # urban: impervious high (Strongly Disagree), agriculture low (Strongly Agree) -> impervious drives
    r = evidence.ev_impervious(_ctx(imperviousPctWatershed=30.0, cropPctWatershed=3.0,
                                    hayPasturePctWatershed=0.0))
    assert r.suggested_likert == "Strongly Disagree"       # impervious is more limiting
    assert "impervious 30.0%" in r.note and "agricultural 3.0%" in r.note


def test_ev_impervious_without_agriculture_data():
    r = evidence.ev_impervious(_ctx(imperviousPctWatershed=8.0))     # no crop/hay values
    assert r.status == "ok" and "Land-cover indicators" not in r.note
    assert "agricultural" not in r.value_text
    assert r.suggested_likert == "Agree"                    # impervious 8% -> Agree


def test_ev_impervious_unavailable_without_impervious():
    r = evidence.ev_impervious(_ctx(cropPctWatershed=40.0))   # impervious missing -> unavailable
    assert r.status == "unavailable" and "did not return impervious cover" in r.note


# --- riparian buffer: natural vegetation for corridor metrics; forest-only for canopy shade --- #
def test_ev_corridor_uses_natural_vegetation():
    # grassland buffer: forest ~0 but dense grass/shrub -> natural veg counts (was forest-only)
    r = evidence.ev_corridor(_ctx(forestPctRiparian=2.0, shrubPctRiparian=10.0,
                                  grasslandPctRiparian=50.0, woodyWetlandPctRiparian=0.0,
                                  herbWetlandPctRiparian=0.0))
    assert r.status == "ok" and r.value == 62.0
    assert "natural vegetation" in r.value_text and "aerial basemap" in r.note


def test_ev_canopy_stays_forest_only():
    # canopy shade must ignore grass/shrub (forest canopy shades; grass does not)
    r = evidence.ev_canopy(_ctx(forestPctRiparian=8.0, grasslandPctRiparian=50.0))
    assert r.status == "ok" and r.value == 8.0
    assert "forest in the 100 m riparian buffer" in r.value_text


def test_ev_corridor_unavailable_without_vegetation():
    assert evidence.ev_corridor(_ctx()).status == "unavailable"
