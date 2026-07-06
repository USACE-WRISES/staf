"""Offline tests for the 1-D Manning's hydraulics port (easi/hydraulics.py)."""
from __future__ import annotations

import pytest

from easi import hydraulics as hyd

# Symmetric V-channel: thalweg 0 at x=5, sides rising to 2 at x=0 and x=10.
V_ST = [0.0, 5.0, 10.0]
V_EL = [2.0, 0.0, 2.0]


def _q(stage, slope=0.005):
    sp = hyd.section_props(V_ST, V_EL, stage)
    return hyd.discharge(sp["A"], sp["P"], slope)


def test_section_props_full_v_channel():
    sp = hyd.section_props(V_ST, V_EL, 2.0)
    assert sp["T"] == pytest.approx(10.0, abs=1e-6)        # whole top wet
    assert sp["A"] == pytest.approx(10.0, abs=1e-6)        # 0.5 * 10 * 2
    assert sp["P"] == pytest.approx(2 * (5 ** 2 + 2 ** 2) ** 0.5, abs=1e-6)


def test_section_props_partial_v_channel():
    sp = hyd.section_props(V_ST, V_EL, 1.0)
    assert sp["T"] == pytest.approx(5.0, abs=1e-6)
    assert sp["A"] == pytest.approx(2.5, abs=1e-6)         # 0.5 * 5 * 1


def test_section_props_dry_at_thalweg():
    sp = hyd.section_props(V_ST, V_EL, 0.0)
    assert sp["A"] == 0.0 and sp["T"] == 0.0


def test_discharge_monotonic_in_stage():
    assert 0 < _q(1.0) < _q(1.5) < _q(2.0)


def test_discharge_zero_when_dry():
    assert hyd.discharge(0.0, 0.0, 0.01) == 0.0


def test_slope_floor_keeps_discharge_positive():
    sp = hyd.section_props(V_ST, V_EL, 1.5)
    assert hyd.discharge(sp["A"], sp["P"], 0.0) > 0       # slope floored, not zeroed


@pytest.mark.parametrize("stage", [0.5, 1.0, 1.5, 1.9])
def test_stage_for_discharge_round_trips(stage):
    back = hyd.stage_for_discharge(V_ST, V_EL, _q(stage), 0.005)
    assert back == pytest.approx(stage, abs=0.01)


def test_stage_for_discharge_none_when_unconveyable():
    assert hyd.stage_for_discharge(V_ST, V_EL, 1e9, 0.005) is None
