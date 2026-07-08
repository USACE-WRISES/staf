"""Tests for views.uihelpers.CompileProgress — the import-wizard compile counter.

Pure state + label formatting (the reactive flush/notification live in the
effect and are exercised live, not here).
"""

from views.uihelpers import CompileProgress


def test_total_batch_sources_are_one_unit_each():
    # NLDI snap + final assembly (2) + StreamCAT (1) + NRSA (1)
    p = CompileProgress.for_run(18, streamcat=True, nrsa=True)
    assert p.total == 4


def test_total_per_site_sources_scale_with_site_count():
    # 2 + StreamCAT(1) + NRSA(1) + MMW(18) = 22
    assert CompileProgress.for_run(18, streamcat=True, nrsa=True, mmw=True).total == 22
    # 2 + StreamStats(18) + MMW(18) = 38
    assert CompileProgress.for_run(18, streamstats=True, mmw=True).total == 38
    # site count actually drives the per-site weight
    assert CompileProgress.for_run(5, mmw=True).total == 7


def test_total_never_below_one():
    # even a zero-site run keeps the two always-on units
    assert CompileProgress.for_run(0).total == 2
    # a directly-constructed zero total is clamped so the toast never divides by 0
    assert CompileProgress(0).total == 1


def test_complete_advances_and_clamps_at_total():
    p = CompileProgress(3)
    assert p.done == 0
    p.complete()
    p.complete()
    assert p.done == 2
    p.complete(5)  # cannot overshoot
    assert p.done == 3


def test_title_reflects_live_counter():
    p = CompileProgress.for_run(18, mmw=True)  # total = 20
    assert p.title() == "Compiling site data (0 of 20)"
    p.complete()
    assert p.title() == "Compiling site data (1 of 20)"


def test_detail_with_and_without_per_site_suffix():
    p = CompileProgress(5)
    p.start("StreamCAT (10 metrics)")
    assert p.detail() == "StreamCAT (10 metrics)"
    p.start("Model My Watershed (watershed + attributes)", site=3, n_sites=18)
    assert p.detail() == "Model My Watershed (watershed + attributes), site 3 of 18"


def test_labels_have_no_em_dash():
    # user-visible copy must avoid em dashes (reads AI-generated)
    p = CompileProgress.for_run(3, streamstats=True, mmw=True)
    p.start("Model My Watershed (watershed + attributes)", site=1, n_sites=3)
    assert "—" not in p.title()
    assert "—" not in p.detail()
