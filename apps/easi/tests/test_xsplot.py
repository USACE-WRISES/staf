"""Offline tests for the cross-section plot (easi/xsplot.py)."""
from __future__ import annotations

import base64

from easi import xsplot

ST = list(range(0, 21))
EL = [3.0 if abs(x - 10) > 5 else abs(x - 10) * 0.6 for x in ST]


def test_cross_section_png_is_png():
    png = xsplot.cross_section_png(ST, EL, bankfull_stage=1.5, floodplain_stage=3.0,
                                   thalweg=0.0, entrenchment_ratio=2.4,
                                   bank_height_ratio=1.1, bankfull_width_m=10.0,
                                   bankfull_depth_m=1.0, division="Interior Plains")
    assert png[:4] == b"\x89PNG" and len(png) > 1000


def test_cross_section_png_unit_toggle():
    # both unit modes render a valid PNG (ft default + explicit m)
    for unit in ("ft", "m"):
        png = xsplot.cross_section_png(ST, EL, bankfull_stage=1.5, floodplain_stage=2.2,
                                       thalweg=0.0, unit=unit)
        assert png[:4] == b"\x89PNG"


def test_cross_section_png_placeholder_on_bad_input():
    assert xsplot.cross_section_png([], [])[:4] == b"\x89PNG"
    assert xsplot.cross_section_png([0, 1], [0, 1])[:4] == b"\x89PNG"   # < 3 points


def test_cross_section_png_b64_round_trips():
    b64 = xsplot.cross_section_png_b64(ST, EL, bankfull_stage=1.5)
    assert base64.b64decode(b64)[:4] == b"\x89PNG"
