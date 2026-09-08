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


# --------------------------------------------------------------------------- #
# The reference panel's sampling protocol (methodology 0.10, rule DATA-10)
# --------------------------------------------------------------------------- #
def test_the_panel_is_framed_on_stream_order_not_the_sampling_protocol():
    """Wadeable means NHDPlus V2 stream order 1 to 5 (methodology 0.10).

    The protocol flag is the field crew's call about the day, and it is a
    narrower population: measured over the three pilot ecoregions on
    2026-09-07, every WADEABLE station is order 1 to 5, but 57 of 301 stations
    of order 1 to 5 were sampled as BOATABLE. Framing on the protocol dropped
    all 57 out of the reference population, so order decides instead.
    """
    everything, _ = nd.resolve_site_panel("58", dataset="multi-cycle-v1")
    frame, ledger = nd.resolve_site_panel(
        "58", dataset="multi-cycle-v1", max_stream_order=5, protocols=("WADEABLE",))
    assert 0 < len(frame) < len(everything)
    # every station kept is in frame, whatever protocol sampled it
    assert (frame["stream_order"].dropna() <= 5).all()
    assert set(frame["protocol"].dropna().unique()) == {"WADEABLE", "BOATABLE"}
    assert (frame["protocol"] == "BOATABLE").sum() > 0
    # every station dropped has its order in the reason
    assert len(ledger) == len(everything) - len(frame)
    assert all("outside the reference frame" in r for r in ledger["reason"])
    # no frame keeps every stream, which is what every published version ran
    same, _ = nd.resolve_site_panel("58", dataset="multi-cycle-v1", max_stream_order=None)
    assert len(same) == len(everything)


def test_the_protocol_decides_only_where_the_order_is_unknown():
    frame, _ = nd.resolve_site_panel("58", dataset="multi-cycle-v1",
                                     max_stream_order=5, protocols=("WADEABLE",))
    unknown = frame[frame["stream_order"].isna()]
    assert len(unknown) > 0                       # NEH has a few unresolved COMIDs
    assert set(unknown["protocol"].dropna().unique()) == {"WADEABLE"}
    # with no fallback the same stations leave the panel
    strict, ledger = nd.resolve_site_panel("58", dataset="multi-cycle-v1",
                                           max_stream_order=5, protocols=())
    assert len(strict) == len(frame) - len(unknown)
    assert any("stream order unknown" in r for r in ledger["reason"])


def test_the_cached_order_table_covers_the_archive():
    orders = nd.stream_orders()
    assert len(orders) > 4000                     # one row per station COMID
    assert all(isinstance(k, int) and v is not None for k, v in list(orders.items())[:50])
    assert nd.STREAM_ORDER_PATH.name == "stream_order.csv"


def test_the_reference_frame_is_recorded_and_joins_the_digest_only_when_set():
    """A run that keeps every stream (each published version) adds no digest key;
    a framed panel is a different reference population and must move it."""
    from streamcurves import provenance as pv
    base = {"region": {"code": "58"}, "screening_method": "direct_engine",
            "nrsa_dataset": "multi-cycle-v1"}
    framed = pv.build_run_manifest({**base, "nrsa_max_stream_order": 5,
                                    "nrsa_protocols": ["WADEABLE"]}, argv=[])
    plain = pv.build_run_manifest(base, argv=[])
    assert framed["inputs"]["nrsa_dataset"]["maxStreamOrder"] == 5
    assert framed["inputs"]["nrsa_dataset"]["protocols"] == ["WADEABLE"]
    assert plain["inputs"]["nrsa_dataset"]["maxStreamOrder"] is None
    payload = pv.digest_payload_from_manifest(framed)
    assert payload["nrsa_max_stream_order"] == 5
    assert payload["nrsa_protocols"] == ["WADEABLE"]
    assert "nrsa_max_stream_order" not in pv.digest_payload_from_manifest(plain)
    assert framed["inputsDigest"] != plain["inputsDigest"]


# --------------------------------------------------------------------------- #
# The frame is visible and reversible (2026-09-07)
# --------------------------------------------------------------------------- #
def test_an_out_of_frame_station_can_be_readmitted_by_name():
    """The rule has an owner override, and the override is never silent."""
    framed, ledger = nd.resolve_site_panel("58", dataset="multi-cycle-v1",
                                           max_stream_order=5, protocols=("WADEABLE",))
    assert framed.attrs["frame_overrides"] == []
    key = str(ledger.iloc[0]["station_key"])
    kept, ledger2 = nd.resolve_site_panel(
        "58", dataset="multi-cycle-v1", max_stream_order=5, protocols=("WADEABLE",),
        keep_stations={key: "owner: the pool needs this reach"})
    assert len(kept) == len(framed) + 1 and len(ledger2) == len(ledger) - 1
    assert key in set(kept["site_id"].astype(str))
    over = kept.attrs["frame_overrides"]
    assert len(over) == 1 and over[0]["station_key"] == key
    assert over[0]["stream_order"] > 5 and over[0]["source"] == "owner"
    assert over[0]["reason"] == "owner: the pool needs this reach"
    assert "outside the reference frame" in over[0]["out_of_frame_reason"]


def test_the_builder_counts_what_the_frame_keeps_out():
    from streamcurves import region_build as rb
    stations = nd.load_dataset("multi-cycle-v1").stations
    counts = rb.frame_counts(stations, "58", max_stream_order=5)
    panel, ledger = nd.resolve_site_panel("58", dataset="multi-cycle-v1",
                                          max_stream_order=5, protocols=("WADEABLE",))
    # the page and the run agree, or the page is worse than useless
    assert counts["n_in_frame"] == len(panel)
    assert counts["n_out_of_frame"] == len(ledger)
    assert counts["n_total"] == counts["n_in_frame"] + counts["n_out_of_frame"]
    assert sum(counts["by_order"].values()) == counts["n_in_frame"]
    text = rb.frame_summary_text(counts, "wadeable", 5)
    assert f"{counts['n_in_frame']} of {counts['n_total']}" in text
    assert "stream order 1 to 5" in text and "DATA-10" in text
    # every stream: nothing is kept out, and the sentence says so
    every = rb.frame_counts(stations, "58")
    assert every["n_in_frame"] == every["n_total"] == counts["n_total"]
    assert "every stream" in rb.frame_summary_text(every, "all")


def test_the_frame_and_its_overrides_reach_the_manifest_and_the_digest():
    from streamcurves import provenance as pv
    base = {"region": {"code": "58"}, "screening_method": "direct_engine",
            "nrsa_dataset": "multi-cycle-v1", "nrsa_max_stream_order": 5,
            "nrsa_protocols": ["WADEABLE"], "nrsa_n_out_of_frame": 11}
    plain = pv.build_run_manifest(base, argv=[])
    kept = pv.build_run_manifest(
        {**base, "nrsa_frame_overrides": [{"station_key": "MER9-0902",
                                           "stream_order": 6.0,
                                           "reason": "owner: needed"}]}, argv=[])
    ds = kept["inputs"]["nrsa_dataset"]
    assert ds["nOutOfFrame"] == 11
    assert ds["frameOverrides"][0]["station_key"] == "MER9-0902"
    assert plain["inputs"]["nrsa_dataset"]["frameOverrides"] is None
    # a readmission changes the reference population, so it moves the digest
    assert pv.digest_payload_from_manifest(kept)["nrsa_frame_overrides"] == ["MER9-0902"]
    assert "nrsa_frame_overrides" not in pv.digest_payload_from_manifest(plain)
    assert kept["inputsDigest"] != plain["inputsDigest"]


def test_the_packet_shows_the_frame_its_exclusions_and_any_override():
    from streamcurves import review_packet as rp
    lines = rp._reference_frame_lines({
        "max_stream_order": 5, "n_out_of_frame": 11,
        "panel": {"nCandidates": 175, "nOutOfFrame": 11,
                  "byStreamOrder": {"1": 58, "5": 26},
                  "outOfFrameReasons": {"stream order 6 is outside the reference frame": 11}},
        "frame_overrides": [{"station_key": "MER9-0902", "stream_order": 6.0,
                             "reason": "owner: needed"}]})
    text = "\n".join(lines)
    assert "175 stations in frame" in text and "11 out of frame" in text
    assert "--reference-frame all" in text and "--include-site" in text
    assert "MER9-0902" in text and "owner: needed" in text
    assert "DATA-10" in text
    # an unframed run says so instead of printing an empty table
    every = "\n".join(rp._reference_frame_lines({"max_stream_order": None}))
    assert "every stream" in every
