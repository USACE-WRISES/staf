"""Dataset selection and the pooled-panel policy.

The legacy dataset is the default and must keep behaving exactly as it did,
because three published assessments fingerprint its two files. The multi-cycle
tests skip when the archive has not been built.
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd

MULTI = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)

# published regions, and what the legacy dataset gives for them today
INTERIOR_PLATEAU = "71"
LEGACY_IP_SITES = 25


# --------------------------------------------------------------------------- #
# the default must not move
# --------------------------------------------------------------------------- #

def test_the_default_dataset_is_the_legacy_snapshot():
    """DEFAULT_DATASET_ID defines what an ABSENT dataset means (old manifests,
    the digest rule). It must never move, even though new builds default to the
    pooled archive via default_build_dataset_id."""
    assert nd.DEFAULT_DATASET_ID == nd.LEGACY_DATASET_ID
    assert nd.available_datasets()[0] == nd.LEGACY_DATASET_ID


def test_a_new_build_defaults_to_the_pooled_archive_when_it_exists(monkeypatch):
    monkeypatch.setattr(nd, "multi_cycle_available", lambda: True)
    assert nd.default_build_dataset_id() == nd.MULTI_CYCLE_DATASET_ID
    monkeypatch.setattr(nd, "multi_cycle_available", lambda: False)
    assert nd.default_build_dataset_id() == nd.LEGACY_DATASET_ID
    # and the absence default did not move with it
    assert nd.DEFAULT_DATASET_ID == nd.LEGACY_DATASET_ID


def test_the_legacy_panel_matches_the_bundled_site_file():
    panel, ledger = nd.resolve_site_panel(INTERIOR_PLATEAU)
    assert len(panel) == LEGACY_IP_SITES
    assert ledger.empty
    assert set(panel["source_cycle"]) == {"1819"}
    # the station key is the site id, so nothing downstream sees a new identifier
    assert (panel["station_key"] == panel["site_id"]).all()
    assert panel["site_id"].str.startswith("NRS18_").all()


def test_the_legacy_panel_can_still_require_metrics():
    panel, ledger = nd.resolve_site_panel(
        INTERIOR_PLATEAU, require_metrics=["chem_PTL", "phab_XEMBED"])
    assert len(panel) <= LEGACY_IP_SITES
    assert len(panel) + len(ledger) == LEGACY_IP_SITES
    if not ledger.empty:
        assert set(ledger["reason"]) == {"missing required metrics"}
        assert ledger["missing"].str.len().gt(0).all()


def test_an_unknown_dataset_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown NRSA dataset"):
        nd.resolve_site_panel(INTERIOR_PLATEAU, dataset="no-such-dataset")


def test_an_unknown_policy_is_refused():
    with pytest.raises(ValueError, match="unknown policy"):
        nd.resolve_site_panel(INTERIOR_PLATEAU, policy="whatever")


# --------------------------------------------------------------------------- #
# pooled panels
# --------------------------------------------------------------------------- #

@MULTI
def test_pooling_more_than_doubles_a_real_region():
    legacy, _ = nd.resolve_site_panel(INTERIOR_PLATEAU)
    pooled, ledger = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert len(pooled) > 2 * len(legacy)
    assert ledger.empty          # nothing required, so nothing is excluded
    assert pooled["site_id"].is_unique


@MULTI
def test_each_station_appears_once_and_carries_its_cycle():
    pooled, _ = nd.resolve_site_panel(INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert pooled["station_key"].is_unique
    assert set(pooled["source_cycle"]) <= set(nd.CYCLES_NEWEST_FIRST)
    # the source label says which survey the row came from
    for row in pooled.itertuples():
        assert row.source == nd.CYCLE_LABELS[row.source_cycle]
    assert set(pooled.columns) >= set(nd.PANEL_COLUMNS)


@MULTI
def test_the_newest_cycle_wins_when_a_station_has_several():
    pooled, _ = nd.resolve_site_panel(INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    stations = ds.stations.set_index("station_key")
    for row in pooled.itertuples():
        sampled = str(stations.loc[row.station_key, "cycles_sampled"]).split(",")
        assert row.source_cycle == max(sampled), row.station_key


@MULTI
def test_restricting_the_cycles_restricts_the_panel():
    only_new, _ = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID, cycles=["2324"])
    everything, _ = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert 0 < len(only_new) < len(everything)
    assert set(only_new["source_cycle"]) == {"2324"}


@MULTI
def test_a_station_falls_back_to_an_older_cycle_rather_than_dropping_out():
    """The point of the policy: a metric missing in 2023-24 is taken from 2018-19."""
    pooled, _ = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=nd.MULTI_CYCLE_DATASET_ID,
        require_metrics=["phab_XEMBED", "phab_BFWD_RAT"])
    assert len(pooled) > 0
    # the panel is not all one cycle, which is what falling back looks like
    assert len(set(pooled["source_cycle"])) > 1


@MULTI
def test_every_excluded_station_is_accounted_for_in_the_ledger():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    everything, _ = nd.resolve_site_panel(INTERIOR_PLATEAU, dataset=ds)
    kept, ledger = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=ds, require_metrics=["phab_XEMBED"])
    dropped = set(everything["station_key"]) - set(kept["station_key"])
    assert dropped, "expected some station to lack the metric"
    assert dropped <= set(ledger["station_key"])
    assert set(ledger["reason"]) <= {
        "missing required metrics", "no values row", "no cycle in the requested set",
        "no visit record"}
    # every ledger row names what was missing
    named = ledger[ledger["reason"] == "missing required metrics"]
    assert named["missing"].str.contains("phab_XEMBED").all()


@MULTI
def test_an_empty_region_returns_empty_frames_not_an_error():
    panel, ledger = nd.resolve_site_panel("zzz", dataset=nd.MULTI_CYCLE_DATASET_ID)
    assert panel.empty and ledger.empty
    assert set(nd.PANEL_COLUMNS) <= set(panel.columns)


# --------------------------------------------------------------------------- #
# values for a panel
# --------------------------------------------------------------------------- #

@MULTI
def test_panel_values_line_up_with_the_panel_and_its_chosen_cycles():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel(
        INTERIOR_PLATEAU, dataset=ds, require_metrics=["phab_XEMBED"])
    values = nd.panel_values(panel, dataset=ds, metrics=["phab_XEMBED", "phab_BFWD_RAT"])
    assert len(values) == len(panel)
    assert list(values["site_id"]) == list(panel["site_id"])
    # the required metric is present for every retained station, by construction
    assert values["phab_XEMBED"].notna().all()


@MULTI
def test_panel_values_read_the_cycle_the_panel_chose():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel(INTERIOR_PLATEAU, dataset=ds)
    values = nd.panel_values(panel, dataset=ds, metrics=["phab_XEMBED"])
    lookup = (ds.values.set_index(["station_key", "cycle", "visit_no"])["phab_XEMBED"]
              .sort_index())
    for panel_row, value in zip(panel.itertuples(), values["phab_XEMBED"]):
        expected = lookup.get(
            (panel_row.station_key, panel_row.source_cycle, panel_row.visit_no))
        if isinstance(expected, pd.Series):
            # five stations merge two co-located sites from one cycle; the join
            # takes the first, so compare against the same one
            expected = expected.iloc[0]
        assert (pd.isna(expected) and pd.isna(value)) or expected == value


def test_panel_values_on_the_legacy_dataset_keep_the_old_shape():
    panel, _ = nd.resolve_site_panel(INTERIOR_PLATEAU)
    values = nd.panel_values(panel, metrics=["phab_XEMBED"])
    assert "site_id" in values.columns and "phab_XEMBED" in values.columns


def test_panel_values_tolerate_an_empty_panel():
    empty = pd.DataFrame(columns=nd.PANEL_COLUMNS + ["station_key", "source_cycle", "visit_no"])
    assert nd.panel_values(empty).empty
