"""Offline tests for desktop-evidence adapters (inject ctx.extras, no network).

Focus: the catchment-hydrology impervious card also surfaces watershed agriculture and
advises switching the indicator when farming is the more limiting land-cover pressure
(the SFARI analog of EASI's selectable impervious/agricultural indicator).
"""
from sfari import evidence
from sfari.metrics.base import AnalysisContext


def _ctx(**streamcat):
    c = AnalysisContext(lat=44.0, lon=-114.0, comid=1)   # central Idaho-ish
    c.extras["streamcat"] = streamcat
    return c


def test_ev_impervious_reports_both_and_suggests_more_limiting():
    # impervious low (Strongly Agree), agriculture dominant (Strongly Disagree) -> suggest the worse
    r = evidence.ev_impervious(_ctx(pctimp2019ws=2.0, pctcrop2019ws=55.0, pcthay2019ws=6.0))
    assert r.status == "ok"
    assert "2.0% impervious" in r.value_text and "agricultural land" in r.value_text
    assert r.suggested_likert == "Strongly Disagree"       # agriculture is more limiting
    assert "impervious 2.0%" in r.note and "agricultural 61.0%" in r.note


def test_ev_impervious_suggests_impervious_when_it_drives():
    # urban: impervious high (Strongly Disagree), agriculture low (Strongly Agree) -> impervious drives
    r = evidence.ev_impervious(_ctx(pctimp2019ws=30.0, pctcrop2019ws=3.0))
    assert r.suggested_likert == "Strongly Disagree"       # impervious is more limiting
    assert "impervious 30.0%" in r.note and "agricultural 3.0%" in r.note


def test_ev_impervious_without_agriculture_data():
    r = evidence.ev_impervious(_ctx(pctimp2019ws=8.0))     # no crop/hay in the row
    assert r.status == "ok" and r.note == ""
    assert "agricultural" not in r.value_text
    assert r.suggested_likert == "Agree"                    # impervious 8% -> Agree


def test_ev_impervious_unavailable_without_impervious():
    r = evidence.ev_impervious(_ctx(pctcrop2019ws=40.0))   # impervious missing -> unavailable
    assert r.status == "unavailable"


# --- riparian buffer: natural vegetation for corridor metrics; forest-only for canopy shade --- #
def _rp_ctx(**rp):
    c = AnalysisContext(lat=44.0, lon=-114.0, comid=1)
    c.extras["streamcat_rp"] = rp
    return c


def test_ev_corridor_uses_natural_vegetation():
    # grassland buffer: forest ~0 but dense grass/shrub -> natural veg counts (was forest-only)
    r = evidence.ev_corridor(_rp_ctx(pctgrs2019wsrp100=50, pctshrb2019wsrp100=10,
                                     pctmxfst2019wsrp100=2))
    assert r.status == "ok" and r.value == 62.0
    assert "natural riparian vegetation" in r.value_text and "aerial basemap" in r.note


def test_ev_canopy_stays_forest_only():
    # canopy shade must ignore grass/shrub (forest canopy shades; grass does not)
    r = evidence.ev_canopy(_rp_ctx(pctgrs2019wsrp100=50, pctmxfst2019wsrp100=8))
    assert r.status == "ok" and r.value == 8.0
    assert "riparian forest" in r.value_text


def test_ev_corridor_unavailable_without_vegetation():
    assert evidence.ev_corridor(_rp_ctx()).status == "unavailable"
