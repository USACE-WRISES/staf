"""One EPA site per station per cycle, chosen by rule and joined exactly.

Five stations in the pooled archive carry two EPA sites in ONE cycle (a
probability site and a hand-picked site on the same reach share a UNIQUE_ID).
The panel used to keep whichever came first in the file, and the value join,
keyed on the visit alone, returned both sites' rows for that station: 176 value
rows for the 175 Northeastern Highlands candidates (2026-09-19).
"""
from __future__ import annotations

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd

pytestmark = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)")

KEY = ["station_key", "cycle", "visit_no"]


def _shared_station_visits() -> pd.DataFrame:
    visits = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).visits
    return visits[visits.duplicated(KEY, keep=False)].sort_values(KEY + ["site_id"])


def test_the_archive_still_has_the_five_shared_stations():
    """If EPA's files or the station linker change this count, the rule below
    needs a second look before anything else does."""
    shared = _shared_station_visits()
    assert shared["station_key"].nunique() == 5
    assert len(shared) == 10


def test_the_index_visit_is_the_lower_site_id():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel(None, dataset=ds)
    picked = panel.set_index("station_key")["site_name"]
    for station, group in _shared_station_visits().groupby("station_key"):
        cycle = panel.set_index("station_key").loc[station, "source_cycle"]
        same_cycle = group[group["cycle"] == cycle]
        if same_cycle.empty:      # the station's newest cycle is not the shared one
            continue
        assert picked[station] == sorted(same_cycle["site_id"])[0], station


def test_the_explicit_order_picks_what_file_order_did():
    """The rule was chosen so that nothing already published moves."""
    visits = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).visits
    by_file = (visits.sort_values(KEY, kind="stable").drop_duplicates(["station_key", "cycle"]))
    by_rule = (visits.sort_values(nd.INDEX_VISIT_ORDER).drop_duplicates(["station_key", "cycle"]))
    a = by_file.set_index(["station_key", "cycle"])["site_id"].sort_index()
    b = by_rule.set_index(["station_key", "cycle"])["site_id"].sort_index()
    assert a.equals(b)


def test_panel_values_returns_one_row_per_station():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel("58", dataset=ds)       # holds NRS18_ME_10025
    values = nd.panel_values(panel, dataset=ds, metrics=["chem_PTL", "phab_XEMBED"])
    assert len(values) == len(panel)
    assert values["site_id"].is_unique


def test_the_values_come_from_the_picked_site():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel("58", dataset=ds)
    row = panel[panel["station_key"] == "NRS18_ME_10025"].iloc[0]
    got = nd.panel_values(panel, dataset=ds, metrics=["chem_PTL"]).set_index("site_id")
    want = ds.values[(ds.values["station_key"] == row["station_key"])
                     & (ds.values["cycle"] == row["source_cycle"])
                     & (ds.values["site_id"] == row["site_name"])]["chem_PTL"].iloc[0]
    have = got.loc["NRS18_ME_10025", "chem_PTL"]
    assert (pd.isna(want) and pd.isna(have)) or have == want


def test_a_panel_without_site_names_still_gets_one_row_per_station():
    ds = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID)
    panel, _ = nd.resolve_site_panel("58", dataset=ds)
    values = nd.panel_values(panel.drop(columns=["site_name"]), dataset=ds,
                             metrics=["chem_PTL"])
    assert len(values) == len(panel) and values["site_id"].is_unique
