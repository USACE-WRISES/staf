"""The reference-evidence index, now covering all three survey cycles.

The screen matches a reach on COMID, never on a site id, so a cycle with no
records is a reach the screen cannot evidence. Before this the index held 2018-19
only and none of the 1,327 stations new in 2023-24 had any evidence at all.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

import pandas as pd
import pytest

from streamcurves import nrsa_dataset as nd
from streamcurves._vendor.easi.datasources import nrsa as evidence

MULTI = pytest.mark.skipif(
    not nd.multi_cycle_available(),
    reason="multi-cycle archive not built (scripts/nrsa/build_values_table.py)",
)

MAX_AGE_YEARS = 10   # evidence_for_reach's window


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return list(evidence._records())


def test_the_index_covers_every_cycle(records):
    by_cycle = Counter(str(r.get("cycle") or "?") for r in records)
    assert set(by_cycle) == {"1314", "1819", "2324"}
    # 2023-24 used to contribute 897 records, before the missing COMIDs were snapped
    assert by_cycle["2324"] > 2000
    assert by_cycle["1819"] > 2000
    assert len(records) > 6000


def test_every_record_can_be_placed_on_a_reach(records):
    """A record with no COMID is invisible to the screen, so the builder drops it."""
    for record in records:
        assert record.get("comid") is not None
        assert record.get("lat") is not None and record.get("lon") is not None
        assert record.get("date")


def test_records_carry_at_least_one_usable_signal(records):
    signals = ("pctDry", "wettedPct", "embeddednessPct", "benthicClass", "fishClass")
    for record in records[:500]:
        assert any(record.get(s) is not None for s in signals)


def test_condition_classes_stay_in_the_declared_vocabulary(records):
    allowed = {"Good", "Fair", "Poor", None}
    for record in records:
        assert record.get("benthicClass") in allowed
        assert record.get("fishClass") in allowed


# --------------------------------------------------------------------------- #
# what it actually buys, measured through the reader's own age window
# --------------------------------------------------------------------------- #

def _eligible_comids(records: list[dict]) -> set[int]:
    today = date.today()
    cutoff = date(today.year - MAX_AGE_YEARS, today.month, today.day)
    out = set()
    for record in records:
        try:
            sampled = date.fromisoformat(str(record.get("date"))[:10])
        except ValueError:
            continue
        if cutoff <= sampled <= today:
            comid = record.get("comid")
            if comid is not None and int(comid) < 1_000_000_000:
                out.add(int(comid))
    return out


@MULTI
def test_most_pooled_stations_can_now_be_evidenced(records):
    """Interior Plateau went from 27 evidenced reaches of 64 to 48."""
    eligible = _eligible_comids(records)
    panel, _ = nd.resolve_site_panel("71", dataset=nd.MULTI_CYCLE_DATASET_ID)
    comids = pd.to_numeric(panel["comid"], errors="coerce").dropna().astype(int)
    assert int(comids.isin(eligible).sum()) >= 45


@MULTI
def test_the_stations_new_in_2023_24_are_no_longer_invisible(records):
    eligible = _eligible_comids(records)
    stations = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).stations
    new = stations[stations["cycles_sampled"] == "2324"]
    comids = pd.to_numeric(new["comid"], errors="coerce").dropna().astype(int)
    # essentially all of them, against 32 before the snap and the rebuild
    assert int(comids.isin(eligible).sum()) > 1200


@MULTI
def test_2013_14_records_exist_but_are_outside_the_reader_age_window(records):
    """They are written because the window is the reader's policy and a parameter,
    not a property of the data. Today they simply do not count."""
    assert Counter(str(r.get("cycle")) for r in records)["1314"] > 2000
    eligible = _eligible_comids(records)
    old_only = [r for r in records if str(r.get("cycle")) == "1314"]
    in_window = [r for r in old_only
                 if r.get("comid") is not None and int(r["comid"]) in eligible]
    # a 2013-14 reach only counts when a newer cycle also sampled it
    assert len(in_window) < len(old_only) / 2


# --------------------------------------------------------------------------- #
# every station has a reach now
# --------------------------------------------------------------------------- #

@MULTI
def test_no_station_is_left_without_a_comid():
    stations = nd.load_dataset(nd.MULTI_CYCLE_DATASET_ID).stations
    assert stations["comid"].isna().sum() == 0
    # and the ones EPA published nothing for are marked as snapped, not passed off
    if "comid_source" in stations.columns:
        assert set(stations["comid_source"].dropna()) <= {"epa_published", "nldi_snap"}
        assert (stations["comid_source"] == "nldi_snap").sum() > 1000
