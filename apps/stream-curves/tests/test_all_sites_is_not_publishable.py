"""An all-sites screen is for exploration, never a published reference set.

Rule REF-03 sets the floor at Functioning-at-Risk or better. The headless path
raised on anything looser, but the interactive wizard offers "All sites" in its
preset dropdown and the readiness checklist only asked whether a screen existed
and retained someone, so an effectively unscreened pool could reach the library
while the explicit "Skip screening" button was correctly blocked (2026-09-19).
"""
from __future__ import annotations

import pytest

from streamcurves import easi_screening as es
from streamcurves import run_state as rs
from streamcurves._vendor.easi.batch.qualify import PRESETS


def _snapshot(**over) -> dict:
    base = {"has_region": True, "has_screening": True, "n_retained": 12, "enriched": True,
            "mapping_confirmed": True, "curve_review": {}, "coverage": {"missing": 0}}
    base.update(over)
    return base


def _screening_item(snapshot: dict) -> dict:
    return next(i for i in rs.readiness_checklist(snapshot) if i["key"] == "screening")


@pytest.mark.parametrize("criteria", ["all_sites", dict(PRESETS["all_sites"])])
def test_the_all_sites_rule_is_not_publishable(criteria):
    assert es.screening_publishable({"criteria": criteria}) is False


@pytest.mark.parametrize("criteria", [
    "functional", "at_risk_or_better", dict(PRESETS["functional"]),
    dict(PRESETS["at_risk_or_better"]), dict(PRESETS["reference_condition"])])
def test_the_reference_presets_are_publishable(criteria):
    assert es.screening_publishable({"criteria": criteria}) is True


@pytest.mark.parametrize("table", [None, {}, {"criteria": None},
                                   {"criteria": {"field": "eci", "cmp": ">", "value": 0.5}}])
def test_an_absent_or_unrecognized_rule_reads_as_publishable(table):
    """An imported EASI batch carries whatever rule its author chose, and an
    older session carries none: neither can be shown to be the all-sites rule."""
    assert es.screening_publishable(table) is True


def test_the_exploration_presets_are_real_vendored_presets():
    for name in es.EXPLORATION_ONLY_PRESETS:
        assert name in PRESETS and name in es.SCREENING_PRESET_CHOICES


def test_the_checklist_refuses_an_all_sites_screen_and_says_why():
    item = _screening_item(_snapshot(screening_publishable=False))
    assert item["ok"] is False
    assert "All sites" in item["label"]


def test_the_checklist_accepts_a_reference_screen_and_an_older_snapshot():
    assert _screening_item(_snapshot(screening_publishable=True))["ok"] is True
    assert _screening_item(_snapshot())["ok"] is True          # key absent


def test_the_checklist_keeps_exactly_its_seven_items():
    assert len(rs.readiness_checklist(_snapshot(screening_publishable=False))) == 7
