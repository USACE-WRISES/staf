"""Rule DATA-11: a revisited station contributes its most recent non-null value
per metric, not its newest cycle taken whole.

The motivating measurement (2026-09-19): in the Northeastern Highlands
dissolved nitrogen and two benthic metrics had 42 usable stations under the
newest-cycle-whole rule, where 71 carry a value in some cycle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd

pytestmark = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)")

NEH = "58"
THIN = ["chem_NTL_DISS", "bent_TOTLNTAX", "bent_TOLRPIND"]


def _dataset(visits, values) -> nd.NrsaDataset:
    stations = pd.DataFrame({"station_key": sorted(set(visits["station_key"]))})
    return nd.NrsaDataset(dataset_id=nd.MULTI_CYCLE_DATASET_ID, sites=stations,
                          values=values, stations=stations, visits=visits)


def _synthetic() -> nd.NrsaDataset:
    visits = pd.DataFrame([
        # A: sampled in all three cycles
        {"station_key": "A", "cycle": "1314", "visit_no": "1", "site_id": "a13"},
        {"station_key": "A", "cycle": "1819", "visit_no": "1", "site_id": "a18"},
        {"station_key": "A", "cycle": "2324", "visit_no": "1", "site_id": "a23"},
        # B: one cycle, two visits (the index visit is visit 1)
        {"station_key": "B", "cycle": "1819", "visit_no": "1", "site_id": "b18"},
        {"station_key": "B", "cycle": "1819", "visit_no": "2", "site_id": "b18"},
        # C: two EPA sites of one cycle on one station (the lower site id is the index)
        {"station_key": "C", "cycle": "2324", "visit_no": "1", "site_id": "c23_HP"},
        {"station_key": "C", "cycle": "2324", "visit_no": "1", "site_id": "c23_10"},
    ])
    values = pd.DataFrame([
        {"station_key": "A", "cycle": "1314", "visit_no": "1", "site_id": "a13", "m": 1.0, "k": 10.0},
        {"station_key": "A", "cycle": "1819", "visit_no": "1", "site_id": "a18", "m": 2.0, "k": np.nan},
        {"station_key": "A", "cycle": "2324", "visit_no": "1", "site_id": "a23", "m": np.nan, "k": np.nan},
        {"station_key": "B", "cycle": "1819", "visit_no": "1", "site_id": "b18", "m": 5.0, "k": np.nan},
        {"station_key": "B", "cycle": "1819", "visit_no": "2", "site_id": "b18", "m": 99.0, "k": 77.0},
        {"station_key": "C", "cycle": "2324", "visit_no": "1", "site_id": "c23_HP", "m": 8.0, "k": 8.0},
        {"station_key": "C", "cycle": "2324", "visit_no": "1", "site_id": "c23_10", "m": 7.0, "k": np.nan},
    ])
    return _dataset(visits, values)


def test_each_metric_takes_the_newest_cycle_that_measured_it():
    values, ledger = nd.latest_values(["A", "B", "C"], dataset=_synthetic(), metrics=["m", "k"])
    got = values.set_index("site_id")
    assert got.loc["A", "m"] == 2.0           # 2023-24 did not measure it; 2018-19 did
    assert got.loc["A", "k"] == 10.0          # only 2013-14 ever measured it
    src = ledger.set_index(["station_key", "metric"])["source_cycle"]
    assert src[("A", "m")] == "1819" and src[("A", "k")] == "1314"


def test_a_repeat_visit_never_supplies_a_value():
    """The index visit is the one a curve reads. A second visit is a QA repeat,
    and filling from it would mix protocols inside one pool."""
    values, ledger = nd.latest_values(["B"], dataset=_synthetic(), metrics=["m", "k"])
    got = values.set_index("site_id")
    assert got.loc["B", "m"] == 5.0 and pd.isna(got.loc["B", "k"])
    assert ("B", "k") not in set(zip(ledger["station_key"], ledger["metric"]))


def test_two_sites_on_one_station_resolve_to_the_index_site():
    values, ledger = nd.latest_values(["C"], dataset=_synthetic(), metrics=["m", "k"])
    got = values.set_index("site_id")
    assert got.loc["C", "m"] == 7.0 and pd.isna(got.loc["C", "k"])      # c23_10, never c23_HP
    assert set(ledger["site_name"]) == {"c23_10"}


def test_one_row_per_station_and_an_unknown_station_is_all_missing():
    values, _ = nd.latest_values(["A", "B", "C", "Z"], dataset=_synthetic(), metrics=["m"])
    assert list(values["site_id"]) == ["A", "B", "C", "Z"]
    assert pd.isna(values.set_index("site_id").loc["Z", "m"])


def test_the_cycle_filter_is_honored():
    values, _ = nd.latest_values(["A"], dataset=_synthetic(), metrics=["m"], cycles=("1314",))
    assert values.set_index("site_id").loc["A", "m"] == 1.0


def test_the_summary_counts_values_by_cycle():
    _, ledger = nd.latest_values(["A", "B", "C"], dataset=_synthetic(), metrics=["m", "k"])
    assert nd.latest_values_summary(ledger) == {
        "m": {"1819": 2, "2324": 1}, "k": {"1314": 1}}


# --------------------------------------------------------------------------- #
# on the archive
# --------------------------------------------------------------------------- #
def _neh_panel():
    max_order, protocols = nd.governed_frame("wadeable")
    panel, _ = nd.resolve_site_panel(NEH, dataset=nd.MULTI_CYCLE_DATASET_ID,
                                     max_stream_order=max_order, protocols=protocols)
    return panel


def test_the_rule_never_loses_a_value_the_old_rule_had():
    panel = _neh_panel()
    metrics = THIN + ["chem_PTL", "phab_XEMBED", "bent_EPT_NTAX"]
    whole = nd.panel_values(panel, dataset=nd.MULTI_CYCLE_DATASET_ID, metrics=metrics)
    latest, _ = nd.latest_values(panel["station_key"], dataset=nd.MULTI_CYCLE_DATASET_ID,
                                 metrics=metrics)
    a = whole.set_index("site_id").sort_index()
    b = latest.set_index("site_id").sort_index()
    assert list(a.index) == list(b.index)
    for m in metrics:
        had = a[m].notna()
        assert b.loc[had, m].notna().all(), m          # nothing lost
        assert b.loc[had, m].equals(a.loc[had, m]), m  # and the same value where both have one
        assert b[m].notna().sum() >= a[m].notna().sum(), m


def test_the_thin_metrics_gain_stations_in_the_northeastern_highlands():
    panel = _neh_panel()
    whole = nd.panel_values(panel, dataset=nd.MULTI_CYCLE_DATASET_ID, metrics=THIN)
    latest, ledger = nd.latest_values(panel["station_key"],
                                      dataset=nd.MULTI_CYCLE_DATASET_ID, metrics=THIN)
    for m in THIN:
        assert latest[m].notna().sum() > whole[m].notna().sum() + 10, m
    assert len(latest) == len(panel) and latest["site_id"].is_unique
    # every value has a ledger row naming its cycle
    assert len(ledger) == int(latest[THIN].notna().sum().sum())
    assert set(ledger["source_cycle"]) <= set(nd.CYCLES_NEWEST_FIRST)


def test_the_legacy_dataset_is_unchanged_by_the_rule():
    panel, _ = nd.resolve_site_panel(NEH, dataset=nd.LEGACY_DATASET_ID)
    whole = nd.panel_values(panel, dataset=nd.LEGACY_DATASET_ID, metrics=["chem_PTL"])
    latest, ledger = nd.latest_values(panel["site_id"], dataset=nd.LEGACY_DATASET_ID,
                                      metrics=["chem_PTL"])
    a = whole[whole["site_id"].isin(set(panel["site_id"]))].set_index("site_id").sort_index()
    b = latest.set_index("site_id").sort_index()
    assert a["chem_PTL"].equals(b["chem_PTL"]) and len(ledger) == 0


def test_the_value_policy_is_not_a_panel_policy():
    assert nd.POLICY_LATEST_NON_NULL in nd.VALUE_POLICIES
    with pytest.raises(ValueError):
        nd.resolve_site_panel(NEH, dataset=nd.MULTI_CYCLE_DATASET_ID,
                              policy=nd.POLICY_LATEST_NON_NULL)
