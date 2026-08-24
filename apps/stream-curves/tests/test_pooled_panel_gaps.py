"""The three gaps that stopped a pooled panel from being usable.

Each fix is measured rather than assumed, and each test pins the measurement so a
later data rebuild cannot quietly invalidate it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves import regional_agent as ra
from streamcurves import stratifiers as st

MULTI = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)


# --------------------------------------------------------------------------- #
# COMID on the panel, and the precedence that consumes it
# --------------------------------------------------------------------------- #

@MULTI
def test_the_pooled_panel_carries_a_comid_so_streamcat_can_join():
    panel, _ = nd.resolve_site_panel("71", dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert "comid" in panel.columns
    # the station table backfills from an older cycle, so most rows resolve even
    # though 2023-24 publishes a COMID for only about a third of its sites
    assert panel["comid"].notna().sum() > len(panel) / 2


def test_the_legacy_panel_still_has_no_comid_column():
    """The default path must be untouched: attach_comids does the work there."""
    panel, _ = nd.resolve_site_panel("71")
    assert "comid" not in panel.columns


def test_attach_comids_prefers_the_screen_then_the_panel_then_the_evidence_file():
    base = pd.DataFrame({"site_id": ["A", "B", "C"], "comid": [111, None, 333]})
    screen = {"easi_screening_sites": [{"site_id": "A", "comid": 999}]}
    out = ra.attach_comids(base, screen)
    assert out["comid"].iloc[0] == 999      # the screen's snapped reach wins
    assert out["comid"].iloc[2] == 333      # the panel's value survives
    assert pd.isna(out["comid"].iloc[1])    # nothing known stays unknown


def test_attach_comids_fills_a_frame_that_already_has_the_column():
    """It used to return early on an existing column, which would have thrown away
    the screen's better answer for every pooled row."""
    base = pd.DataFrame({"site_id": ["A"], "comid": [111]})
    out = ra.attach_comids(base, {"easi_screening_sites": [{"site_id": "A", "comid": 999}]})
    assert out["comid"].iloc[0] == 999


def test_attach_comids_accepts_a_screening_table_in_either_shape():
    base = pd.DataFrame({"site_id": ["A"]})
    as_rows = ra.attach_comids(base, {"easi_screening_sites": [{"site_id": "A", "comid": 7}]})
    as_frame = ra.attach_comids(
        base, {"easi_screening_sites": pd.DataFrame({"site_id": ["A"], "comid": [7]})})
    assert as_rows["comid"].iloc[0] == as_frame["comid"].iloc[0] == 7
    # and with nothing at all
    assert "comid" in ra.attach_comids(base, None).columns
    assert "comid" in ra.attach_comids(base, {"easi_screening_sites": []}).columns


# --------------------------------------------------------------------------- #
# the derived channel-slope column
# --------------------------------------------------------------------------- #

@MULTI
def test_channel_slope_is_filled_for_the_cycle_that_lacks_it():
    values = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).values
    coverage = values.groupby("cycle")["phab_XSLOPE_use"].apply(lambda s: s.notna().mean())
    # 2013-14 publishes no XSLOPE_use column at all; the derivation fills it
    assert coverage["1314"] > 0.9
    assert coverage["1819"] > 0.9 and coverage["2324"] > 0.9


@MULTI
def test_the_slope_derivation_agrees_where_both_columns_exist():
    """The evidence for the fill: XSLOPE and XSLOPE_use are the same number in the
    cycles that publish both. If that stops being true, the fill is unsound."""
    values = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).values
    both = values[values["phab_XSLOPE"].notna() & values["phab_XSLOPE_use"].notna()]
    both = both[both["cycle"].isin(["1819", "2324"])]
    assert len(both) > 3000
    agreement = np.isclose(both["phab_XSLOPE"], both["phab_XSLOPE_use"],
                           rtol=1e-4, atol=1e-6).mean()
    assert agreement > 0.99, agreement


# --------------------------------------------------------------------------- #
# the multi-source stratifier
# --------------------------------------------------------------------------- #

def test_a_candidate_may_declare_one_source_or_several():
    assert st.candidate_sources({"source_column": "a"}) == ["a"]
    assert st.candidate_sources({"source_columns": ["a", "b"]}) == ["a", "b"]
    # the list wins when both are given, and an empty config yields nothing
    assert st.candidate_sources({"source_column": "a", "source_columns": ["b"]}) == ["b"]
    assert st.candidate_sources({}) == []
    assert st.candidate_sources(None) == []


def test_the_source_used_is_the_one_covering_the_most_rows():
    """Declaration order cannot decide it: on a pooled panel the preferred column
    is present but nearly empty, which is exactly the elevation case."""
    data = pd.DataFrame({"land_ELEVWS": [1, None, None, None], "elevws": [9, 9, 9, None]})
    cfg = {"source_columns": ["land_ELEVWS", "elevws"]}
    assert st.resolve_source_column(cfg, data) == "elevws"
    # and the reverse, when the first source is the fuller one
    legacy = pd.DataFrame({"land_ELEVWS": [1, 2, 3], "elevws": [None, None, None]})
    assert st.resolve_source_column(cfg, legacy) == "land_ELEVWS"


def test_resolve_returns_nothing_when_no_source_has_data():
    data = pd.DataFrame({"land_ELEVWS": [None, None]})
    assert st.resolve_source_column({"source_columns": ["land_ELEVWS", "elevws"]}, data) is None
    assert st.resolve_source_column({"source_column": "absent"}, data) is None


def test_the_registry_declares_the_streamcat_fallback_for_elevation():
    registry = st.load_national_registry()
    elevation = (registry.get("candidates") or {}).get("ElevationClass") or {}
    assert st.candidate_sources(elevation) == ["land_ELEVWS", "elevws"]
    # every declared source is attached, so the resolver has something to choose from
    attached = st.source_columns(registry)
    assert "land_ELEVWS" in attached and "elevws" in attached
    # the single-source candidates are unchanged
    assert st.candidate_sources(registry["candidates"]["DrainageAreaClass"]) == ["land_WSAREASQKM"]


@MULTI
def test_all_three_stratifiers_survive_a_pooled_panel():
    """Before these fixes, slope reached 47 of 64 and elevation 18 of 64 on the
    Interior Plateau pool, which would have thinned STRAT-00 badly."""
    panel, _ = nd.resolve_site_panel("71", dataset=nd.MULTI_CYCLE_DATASET_ID)
    values = nd.panel_values(panel, dataset=nd.MULTI_CYCLE_DATASET_ID,
                             metrics=["land_WSAREASQKM", "phab_XSLOPE_use"])
    assert values["land_WSAREASQKM"].notna().sum() == len(panel)
    assert values["phab_XSLOPE_use"].notna().sum() == len(panel)
    # elevation is covered by StreamCat at run time, which needs a COMID
    assert panel["comid"].notna().sum() > len(panel) / 2
